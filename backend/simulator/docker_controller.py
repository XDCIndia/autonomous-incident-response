"""Docker controller for managing IRAS-labeled service containers.

Uses the Python Docker SDK to perform container lifecycle operations.
All containers are identified by the ``iras.service`` label.

Safety: Only operates on containers with IRAS labels — never on
arbitrary containers.

Thread safety: A threading.Lock serialises access to the Docker SDK
client, which is not thread-safe.  All public methods are async and
offload to a thread via ``asyncio.to_thread()``, acquiring the lock
there so concurrent callers never corrupt the connection pool.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import DockerException, NotFound, APIError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LABEL_SERVICE = "iras.service"
LABEL_VERSION = "iras.version"
LABEL_MANAGED = "iras.managed"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ContainerInfo:
    """Snapshot of a running container's configuration."""
    container_id: str
    name: str
    service: str
    version: str
    image: str
    status: str
    health: str
    labels: dict[str, str] = field(default_factory=dict)
    env: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    network: str = ""
    networks: list[str] = field(default_factory=list)


@dataclass
class ContainerConfig:
    """Complete configuration needed to recreate a container identically."""
    image: str
    name: str
    service: str
    version: str
    labels: dict[str, str] = field(default_factory=dict)
    environment: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    network: str = ""
    networks: list[str] = field(default_factory=list)
    healthcheck: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class DockerController:
    """Manages Docker containers for IRAS fault injection and remediation.

    All operations are scoped to containers carrying the ``iras.service``
    label to prevent accidental interference with unrelated containers.

    Every public method is **async** and offloads sync Docker SDK calls to
    a thread via ``asyncio.to_thread()``.  A ``threading.Lock`` serialises
    access so that the Docker client's internal connection pool is never
    corrupted by concurrent calls.
    """

    def __init__(self):
        self._lock = threading.Lock()
        try:
            self._client = docker.from_env()
            self._client.ping()
            logger.info("DockerController: connected to Docker daemon")
        except DockerException as exc:
            logger.error("DockerController: failed to connect to Docker — %s", exc)
            raise

    # ------------------------------------------------------------------
    # Private sync helpers — ALL Docker SDK calls happen here, under lock.
    # ------------------------------------------------------------------

    def _sync_get_service_container(self, service_name: str) -> docker.models.containers.Container | None:
        with self._lock:
            try:
                containers = self._client.containers.list(
                    filters={"label": f"{LABEL_SERVICE}={service_name}"},
                    all=True,
                )
                if not containers:
                    logger.warning("No container found for service=%s", service_name)
                    return None
                running = [c for c in containers if c.status == "running"]
                return running[0] if running else containers[0]
            except DockerException as exc:
                logger.error("Error finding container for %s: %s", service_name, exc)
                return None

    def _inspect(self, container: docker.models.containers.Container) -> ContainerInfo:
        container.reload()
        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels", {})
        env = attrs.get("Config", {}).get("Env", [])
        health_state = attrs.get("State", {}).get("Health", {})
        health = health_state.get("Status", "none") if health_state else "none"
        net_settings = attrs.get("NetworkSettings", {}).get("Networks", {})
        networks = list(net_settings.keys())
        port_bindings = attrs.get("HostConfig", {}).get("PortBindings", {}) or {}
        return ContainerInfo(
            container_id=container.id,
            name=container.name,
            service=labels.get(LABEL_SERVICE, ""),
            version=labels.get(LABEL_VERSION, ""),
            image=attrs.get("Config", {}).get("Image", ""),
            status=container.status,
            health=health,
            labels=labels,
            env=env,
            ports=port_bindings,
            network=networks[0] if networks else "",
            networks=networks,
        )

    def _sync_get_container_info(self, service_name: str) -> ContainerInfo | None:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return None
        with self._lock:
            return self._inspect(container)

    def _sync_save_container_config(self, service_name: str) -> ContainerConfig | None:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return None
        with self._lock:
            container.reload()
            attrs = container.attrs
            config = attrs.get("Config", {})
            host_config = attrs.get("HostConfig", {})
            net_settings = attrs.get("NetworkSettings", {}).get("Networks", {})
            labels = config.get("Labels", {})
            networks = list(net_settings.keys())
            hc = config.get("Healthcheck")
            healthcheck = None
            if hc:
                healthcheck = {
                    "test": hc.get("Test", []),
                    "interval": hc.get("Interval", 0),
                    "timeout": hc.get("Timeout", 0),
                    "retries": hc.get("Retries", 0),
                    "start_period": hc.get("StartPeriod", 0),
                }
            return ContainerConfig(
                image=config.get("Image", ""),
                name=container.name,
                service=labels.get(LABEL_SERVICE, ""),
                version=labels.get(LABEL_VERSION, ""),
                labels=labels,
                environment=config.get("Env", []),
                ports=host_config.get("PortBindings", {}) or {},
                network=networks[0] if networks else "",
                networks=networks,
                healthcheck=healthcheck,
            )

    def _sync_stop_container(self, service_name: str, timeout: int = 10) -> bool:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return False
        with self._lock:
            try:
                logger.info("Stopping container %s (%s)", container.name, container.short_id)
                container.stop(timeout=timeout)
                return True
            except DockerException as exc:
                logger.error("Failed to stop %s: %s", service_name, exc)
                return False

    def _sync_remove_container(self, service_name: str, force: bool = True) -> bool:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return False
        with self._lock:
            try:
                logger.info("Removing container %s (%s)", container.name, container.short_id)
                container.remove(force=force)
                return True
            except DockerException as exc:
                logger.error("Failed to remove %s: %s", service_name, exc)
                return False

    def _sync_restart_container(self, service_name: str, timeout: int = 10) -> bool:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return False
        with self._lock:
            try:
                logger.info("Restarting container %s (%s)", container.name, container.short_id)
                container.restart(timeout=timeout)
                return True
            except DockerException as exc:
                logger.error("Failed to restart %s: %s", service_name, exc)
                return False

    def _sync_deploy_version(
        self,
        config: ContainerConfig,
        *,
        version_override: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> docker.models.containers.Container | None:
        labels = dict(config.labels)
        environment = list(config.environment)
        if version_override:
            labels[LABEL_VERSION] = version_override
        if env_overrides:
            env_dict = {}
            for item in environment:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env_dict[k] = v
            env_dict.update(env_overrides)
            environment = [f"{k}={v}" for k, v in env_dict.items()]
        name = config.name.lstrip("/")
        with self._lock:
            try:
                try:
                    existing = self._client.containers.get(name)
                    logger.info("Removing existing container %s before deploy", name)
                    existing.remove(force=True)
                except NotFound:
                    pass
                healthcheck_kwarg = None
                if config.healthcheck and config.healthcheck.get("test"):
                    healthcheck_kwarg = docker.types.Healthcheck(
                        test=config.healthcheck["test"],
                        interval=config.healthcheck.get("interval", 5_000_000_000),
                        timeout=config.healthcheck.get("timeout", 3_000_000_000),
                        retries=config.healthcheck.get("retries", 3),
                        start_period=config.healthcheck.get("start_period", 5_000_000_000),
                    )
                network = config.network
                container = self._client.containers.run(
                    image=config.image,
                    name=name,
                    labels=labels,
                    environment=environment,
                    ports=config.ports,
                    healthcheck=healthcheck_kwarg,
                    detach=True,
                    network=network if network else None,
                )
                for net in config.networks[1:]:
                    try:
                        net_obj = self._client.networks.get(net)
                        net_obj.connect(container)
                    except DockerException:
                        logger.warning("Could not connect container to network %s", net)
                logger.info(
                    "Deployed %s version=%s container=%s",
                    config.service, labels.get(LABEL_VERSION, "?"), container.short_id,
                )
                return container
            except DockerException as exc:
                logger.error("Failed to deploy %s: %s", config.service, exc)
                return None

    def _sync_exhaust_resources(
        self,
        service_name: str,
        cpu_quota_pct: int,
        workers: int,
        duration_seconds: int,
    ) -> dict[str, Any]:
        """Genuinely pin the container's CPU — not a simulated flag.

        Caps the container's overall CPU quota down to ``cpu_quota_pct`` % of
        a core (via the same cgroup mechanism Docker itself uses for
        ``--cpus``), then spawns ``workers`` detached CPU-burning processes
        *inside* the container via ``docker exec``. Competing for that
        artificially small quota is what makes the container's own request
        handling (the health check, ``/pay``, etc.) visibly degrade — capping
        the quota alone wouldn't do it, since an otherwise-idle container
        never hits its quota ceiling.

        Both effects revert on their own after ``duration_seconds`` (this
        scenario has no real remediation hookup yet — the busy-loop
        processes exit when their own deadline elapses, and a timer restores
        the original CPU quota) — a shared demo host shouldn't stay crippled
        indefinitely just because remediation didn't undo it.
        """
        container = self._sync_get_service_container(service_name)
        if container is None:
            return {"started": False, "reason": "container not found"}

        with self._lock:
            try:
                container.reload()
                host_config = container.attrs.get("HostConfig", {})
                previous = {
                    "cpu_quota": host_config.get("CpuQuota") or -1,
                    "cpu_period": host_config.get("CpuPeriod") or 100_000,
                }

                period = 100_000  # Docker's default CFS accounting period (µs)
                quota = max(1_000, int(period * cpu_quota_pct / 100))
                container.update(cpu_quota=quota, cpu_period=period)

                busy_loop = f"import time; end=time.time()+{duration_seconds}\nwhile time.time() < end: pass"
                for _ in range(workers):
                    container.exec_run(["python3", "-c", busy_loop], detach=True)

                logger.info(
                    "Exhausting resources on %s: capped to %d%% CPU, %d worker(s), %ds",
                    service_name, cpu_quota_pct, workers, duration_seconds,
                )
            except DockerException as exc:
                logger.error("Failed to exhaust resources for %s: %s", service_name, exc)
                return {"started": False, "reason": str(exc)}

        timer = threading.Timer(
            duration_seconds, self._sync_restore_resources, args=(service_name, previous)
        )
        timer.daemon = True
        timer.start()

        return {
            "started": True,
            "cpu_quota_pct": cpu_quota_pct,
            "workers": workers,
            "duration_seconds": duration_seconds,
        }

    def _sync_restore_resources(self, service_name: str, previous: dict[str, Any]) -> bool:
        container = self._sync_get_service_container(service_name)
        if container is None:
            return False
        with self._lock:
            try:
                container.update(
                    cpu_quota=previous.get("cpu_quota", -1),
                    cpu_period=previous.get("cpu_period", 100_000),
                )
                logger.info("Restored normal CPU limits for %s", service_name)
                return True
            except DockerException as exc:
                logger.error("Failed to restore CPU limits for %s: %s", service_name, exc)
                return False

    def _sync_check_health(self, service_name: str) -> dict[str, Any]:
        info = self._sync_get_container_info(service_name)
        if info is None:
            return {"service": service_name, "running": False, "health": "not_found", "version": ""}
        return {
            "service": service_name,
            "running": info.status == "running",
            "health": info.health,
            "version": info.version,
            "container_id": info.container_id,
            "image": info.image,
        }

    def _sync_wait_for_health(
        self,
        service_name: str,
        *,
        target_health: str = "healthy",
        retries: int = 15,
        delay: float = 2.0,
    ) -> bool:
        for attempt in range(1, retries + 1):
            status = self._sync_check_health(service_name)
            current = status.get("health", "unknown")
            logger.debug("Health poll %d/%d for %s: %s", attempt, retries, service_name, current)
            if current == target_health:
                logger.info("Service %s is %s after %d polls", service_name, target_health, attempt)
                return True
            time.sleep(delay)
        logger.warning("Service %s did not reach %s after %d attempts", service_name, target_health, retries)
        return False

    # ------------------------------------------------------------------
    # Public async methods — safe to call from the event loop.
    # ------------------------------------------------------------------

    async def get_service_container(self, service_name: str) -> docker.models.containers.Container | None:
        return await asyncio.to_thread(self._sync_get_service_container, service_name)

    async def get_container_info(self, service_name: str) -> ContainerInfo | None:
        return await asyncio.to_thread(self._sync_get_container_info, service_name)

    async def save_container_config(self, service_name: str) -> ContainerConfig | None:
        return await asyncio.to_thread(self._sync_save_container_config, service_name)

    async def stop_container(self, service_name: str, timeout: int = 10) -> bool:
        return await asyncio.to_thread(self._sync_stop_container, service_name, timeout)

    async def remove_container(self, service_name: str, force: bool = True) -> bool:
        return await asyncio.to_thread(self._sync_remove_container, service_name, force)

    async def restart_container(self, service_name: str, timeout: int = 10) -> bool:
        return await asyncio.to_thread(self._sync_restart_container, service_name, timeout)

    async def deploy_version(
        self,
        config: ContainerConfig,
        *,
        version_override: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> docker.models.containers.Container | None:
        return await asyncio.to_thread(
            self._sync_deploy_version, config,
            version_override=version_override,
            env_overrides=env_overrides,
        )

    async def check_health(self, service_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync_check_health, service_name)

    async def exhaust_resources(
        self,
        service_name: str,
        *,
        cpu_quota_pct: int = 25,
        workers: int = 2,
        duration_seconds: int = 60,
    ) -> dict[str, Any]:
        """Real CPU exhaustion — see ``_sync_exhaust_resources`` for how."""
        return await asyncio.to_thread(
            self._sync_exhaust_resources, service_name, cpu_quota_pct, workers, duration_seconds
        )

    async def restore_resources(self, service_name: str, previous: dict[str, Any]) -> bool:
        return await asyncio.to_thread(self._sync_restore_resources, service_name, previous)

    async def wait_for_health(
        self,
        service_name: str,
        *,
        target_health: str = "healthy",
        retries: int = 15,
        delay: float = 2.0,
    ) -> bool:
        """Poll until the container reaches *target_health*.

        Uses ``asyncio.sleep`` instead of ``time.sleep`` so the event loop
        is never blocked for long periods.
        """
        for attempt in range(1, retries + 1):
            status = await self.check_health(service_name)
            current = status.get("health", "unknown")
            logger.debug("Health poll %d/%d for %s: %s", attempt, retries, service_name, current)
            if current == target_health:
                logger.info("Service %s is %s after %d polls", service_name, target_health, attempt)
                return True
            await asyncio.sleep(delay)
        logger.warning("Service %s did not reach %s after %d attempts", service_name, target_health, retries)
        return False

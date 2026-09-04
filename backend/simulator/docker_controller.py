"""Docker controller for managing IRAS-labeled service containers.

Uses the Python Docker SDK to perform container lifecycle operations.
All containers are identified by the ``iras.service`` label.

Safety: Only operates on containers with IRAS labels — never on
arbitrary containers.
"""

from __future__ import annotations

import logging
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
    """

    def __init__(self):
        try:
            self._client = docker.from_env()
            self._client.ping()
            logger.info("DockerController: connected to Docker daemon")
        except DockerException as exc:
            logger.error("DockerController: failed to connect to Docker — %s", exc)
            raise

    # ----- discovery -----

    def get_service_container(self, service_name: str) -> docker.models.containers.Container | None:
        """Find the running container for *service_name* by IRAS label."""
        try:
            containers = self._client.containers.list(
                filters={"label": f"{LABEL_SERVICE}={service_name}"},
                all=True,  # include stopped containers
            )
            if not containers:
                logger.warning("No container found for service=%s", service_name)
                return None
            # Prefer a running container when multiple exist
            running = [c for c in containers if c.status == "running"]
            return running[0] if running else containers[0]
        except DockerException as exc:
            logger.error("Error finding container for %s: %s", service_name, exc)
            return None

    def get_container_info(self, service_name: str) -> ContainerInfo | None:
        """Return a structured snapshot of the container's current state."""
        container = self.get_service_container(service_name)
        if container is None:
            return None
        return self._inspect(container)

    def _inspect(self, container: docker.models.containers.Container) -> ContainerInfo:
        """Build a ContainerInfo from a live container object."""
        container.reload()
        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels", {})
        env = attrs.get("Config", {}).get("Env", [])

        # Extract health status
        health_state = attrs.get("State", {}).get("Health", {})
        health = health_state.get("Status", "none") if health_state else "none"

        # Extract networks
        net_settings = attrs.get("NetworkSettings", {}).get("Networks", {})
        networks = list(net_settings.keys())

        # Extract port bindings
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

    def save_container_config(self, service_name: str) -> ContainerConfig | None:
        """Capture the full configuration of a container for later restoration.

        This is the key to safe rollback — we save *everything* needed to
        recreate the container identically.
        """
        container = self.get_service_container(service_name)
        if container is None:
            return None

        container.reload()
        attrs = container.attrs
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})
        net_settings = attrs.get("NetworkSettings", {}).get("Networks", {})

        labels = config.get("Labels", {})
        networks = list(net_settings.keys())

        # Extract healthcheck config
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

    # ----- lifecycle -----

    def stop_container(self, service_name: str, timeout: int = 10) -> bool:
        """Stop the container for *service_name*."""
        container = self.get_service_container(service_name)
        if container is None:
            return False
        try:
            logger.info("Stopping container %s (%s)", container.name, container.short_id)
            container.stop(timeout=timeout)
            return True
        except DockerException as exc:
            logger.error("Failed to stop %s: %s", service_name, exc)
            return False

    def remove_container(self, service_name: str, force: bool = True) -> bool:
        """Remove the container for *service_name*."""
        container = self.get_service_container(service_name)
        if container is None:
            return False
        try:
            logger.info("Removing container %s (%s)", container.name, container.short_id)
            container.remove(force=force)
            return True
        except DockerException as exc:
            logger.error("Failed to remove %s: %s", service_name, exc)
            return False

    def restart_container(self, service_name: str, timeout: int = 10) -> bool:
        """Restart the container for *service_name*."""
        container = self.get_service_container(service_name)
        if container is None:
            return False
        try:
            logger.info("Restarting container %s (%s)", container.name, container.short_id)
            container.restart(timeout=timeout)
            return True
        except DockerException as exc:
            logger.error("Failed to restart %s: %s", service_name, exc)
            return False

    def deploy_version(
        self,
        config: ContainerConfig,
        *,
        version_override: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> docker.models.containers.Container | None:
        """Start a new container from *config*, optionally overriding version/env.

        This recreates a container with the exact same configuration as
        saved by ``save_container_config``, with optional overrides for
        the version label and environment variables.
        """
        labels = dict(config.labels)
        environment = list(config.environment)

        if version_override:
            labels[LABEL_VERSION] = version_override

        # Apply environment overrides
        if env_overrides:
            env_dict = {}
            for item in environment:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env_dict[k] = v
            env_dict.update(env_overrides)
            environment = [f"{k}={v}" for k, v in env_dict.items()]

        # Determine container name — strip leading slash if present
        name = config.name.lstrip("/")

        try:
            # Remove any existing container with the same name
            try:
                existing = self._client.containers.get(name)
                logger.info("Removing existing container %s before deploy", name)
                existing.remove(force=True)
            except NotFound:
                pass

            # Build healthcheck kwarg
            healthcheck_kwarg = None
            if config.healthcheck and config.healthcheck.get("test"):
                healthcheck_kwarg = docker.types.Healthcheck(
                    test=config.healthcheck["test"],
                    interval=config.healthcheck.get("interval", 5_000_000_000),
                    timeout=config.healthcheck.get("timeout", 3_000_000_000),
                    retries=config.healthcheck.get("retries", 3),
                    start_period=config.healthcheck.get("start_period", 5_000_000_000),
                )

            # Determine networking mode
            network = config.network
            # For compose-managed networks, connect after creation
            networking_config = None
            if network:
                networking_config = {network: self._client.api.create_endpoint_config()}

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

            # Connect to any additional networks beyond the first
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

    # ----- health -----

    def check_health(self, service_name: str) -> dict[str, Any]:
        """Check container status and Docker health for *service_name*."""
        info = self.get_container_info(service_name)
        if info is None:
            return {
                "service": service_name,
                "running": False,
                "health": "not_found",
                "version": "",
            }
        return {
            "service": service_name,
            "running": info.status == "running",
            "health": info.health,
            "version": info.version,
            "container_id": info.container_id,
            "image": info.image,
        }

    def wait_for_health(
        self,
        service_name: str,
        *,
        target_health: str = "healthy",
        retries: int = 15,
        delay: float = 2.0,
    ) -> bool:
        """Poll until the container reaches *target_health* or retries exhaust."""
        for attempt in range(1, retries + 1):
            status = self.check_health(service_name)
            current = status.get("health", "unknown")
            logger.debug(
                "Health poll %d/%d for %s: %s",
                attempt, retries, service_name, current,
            )
            if current == target_health:
                logger.info("Service %s is %s after %d polls", service_name, target_health, attempt)
                return True
            time.sleep(delay)
        logger.warning(
            "Service %s did not reach %s after %d attempts",
            service_name, target_health, retries,
        )
        return False

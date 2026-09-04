"""Fault injection scenarios.

Each function returns a list of TelemetryEvent signals that the pipeline
can process. The signals contain hints but NOT the answer — the root cause
must be determined by the investigation/arbiter pipeline.

Real Docker fault injection:
    When a ``DockerController`` is provided, ``inject_bad_deployment``
    also performs actual container replacement (healthy → bad).  The full
    container configuration is serialized into the telemetry metadata so
    that the remediation engine can restore it exactly. ``inject_resource_
    exhaustion`` similarly throttles the container's CPU quota and spawns
    real CPU-burning processes inside it (self-reverting on a timer, since
    this scenario has no real remediation hookup yet).

Mock mode (no controller):
    Returns deterministic TelemetryEvent signals only — unchanged from
    the original foundation implementation.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.contracts import TelemetryEvent

if TYPE_CHECKING:
    from backend.simulator.docker_controller import ContainerConfig, DockerController
    from backend.simulator.toxiproxy_client import ToxiproxyClient


def _toxic_is_live(
    toxiproxy_client: ToxiproxyClient | None,
    proxy_name: str,
    toxic_name: str,
) -> bool:
    """Confirm a fault is genuinely applied: proxy enabled AND toxic present.

    ``add_toxic`` returns a truthy payload even when a stale toxic with the
    same name already exists (HTTP 409) or when the proxy is disabled — in
    both cases no *new* fault takes effect on the service.  Checking the live
    proxy state keeps ``docker_performed`` honest so a simulated failure is
    never reported when nothing actually degraded.
    """
    if toxiproxy_client is None:
        return False
    proxy = toxiproxy_client.get_proxy(proxy_name)
    if not proxy or not proxy.get("enabled"):
        return False
    return any(t.get("name") == toxic_name for t in proxy.get("toxics", []))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type for real fault injection
# ---------------------------------------------------------------------------

class FaultInjectionResult:
    """Bundles telemetry signals with Docker-side metadata."""

    def __init__(
        self,
        signals: list[TelemetryEvent],
        *,
        docker_performed: bool = False,
        previous_config: dict[str, Any] | None = None,
        bad_version: str = "",
        service: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.signals = signals
        self.docker_performed = docker_performed
        self.previous_config = previous_config
        self.bad_version = bad_version
        self.service = service
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# Bad Deployment
# ---------------------------------------------------------------------------

async def inject_bad_deployment(
    service: str = "payment-service",
    version: str = "v2.4.1",
    deployed_seconds_ago: float = 30.0,
    docker_controller: DockerController | None = None,
) -> FaultInjectionResult:
    """Simulate a bad deployment — new version causes errors and latency spike.

    **Mock mode** (``docker_controller is None``):
        Returns deterministic telemetry signals only.

    **Real mode** (``docker_controller`` provided):
        1. Saves the complete configuration of the currently-healthy container.
        2. Removes the healthy container.
        3. Starts the same image with ``FORCE_UNHEALTHY=true``.
        4. Returns telemetry signals *plus* the saved config for rollback.

    Returns:
        ``FaultInjectionResult`` containing signals and optional Docker metadata.
    """
    now = datetime.now(timezone.utc)

    # ── Docker fault injection ──────────────────────────────────────────
    previous_config_dict: dict[str, Any] | None = None
    docker_performed = False

    if docker_controller is not None:
        saved = await docker_controller.save_container_config(service)
        if saved is None:
            logger.error("Cannot inject bad deployment: no container found for %s", service)
        else:
            previous_config_dict = dataclasses.asdict(saved)
            logger.info(
                "Saved config for %s (image=%s, version=%s)",
                service, saved.image, saved.version,
            )
            # Remove healthy container and start the bad one
            await docker_controller.remove_container(service, force=True)
            bad_container = await docker_controller.deploy_version(
                saved,
                version_override=version,
                env_overrides={"FORCE_UNHEALTHY": "true", "SERVICE_VERSION": version},
            )
            if bad_container is not None:
                docker_performed = True
                logger.info("Bad deployment injected: %s → %s", service, version)
            else:
                logger.error("Failed to start bad version for %s", service)

    # ── Telemetry signals (always generated) ────────────────────────────
    deploy_metadata: dict[str, Any] = {
        "log_message": f"Deployment {version} rolled out to {service}",
        "version": version,
        "deployed_seconds_ago": deployed_seconds_ago,
    }
    # Embed rollback config in the deploy event so the pipeline can pass
    # it through to the remediation stage.
    if previous_config_dict is not None:
        deploy_metadata["previous_config"] = previous_config_dict
        deploy_metadata["docker_performed"] = True

    signals = [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="deploy",
            value=version,
            metadata=deploy_metadata,
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="error_rate",
            value=0.45,
            metadata={
                "log_message": f"Payment API returned 500 — error rate 45% after {version}",
                "root_cause_hint": "bad_deployment",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="latency",
            value=2400,
            metadata={
                "log_message": f"Latency increased from 200ms to 2400ms after {version}",
                "root_cause_hint": "bad_deployment",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="log_error",
            value=None,
            metadata={
                "log_message": f"ERROR: NullPointerException in PaymentHandler after {version}",
                "stack_trace": f"at PaymentHandler.process(PaymentHandler.java:42) — introduced in {version}",
                "root_cause_hint": "bad_deployment",
            },
        ),
    ]

    return FaultInjectionResult(
        signals=signals,
        docker_performed=docker_performed,
        previous_config=previous_config_dict,
        bad_version=version,
        service=service,
    )


def inject_database_failure(
    service: str = "payment-service",
    database: str = "postgres-main",
    toxiproxy_client: ToxiproxyClient | None = None,
) -> FaultInjectionResult:
    """Simulate database connection pool exhaustion.

    Returns signals showing connection timeouts and pool exhaustion.
    """
    now = datetime.now(timezone.utc)
    
    docker_performed = False
    metadata = {}
    
    if toxiproxy_client:
        proxy_name = "payment-db-proxy"
        # Inject timeout toxic
        toxic = toxiproxy_client.add_toxic(
            proxy_name=proxy_name,
            toxic_name="db_timeout",
            toxic_type="timeout",
            attributes={"timeout": 30000}
        )
        if toxic and _toxic_is_live(toxiproxy_client, proxy_name, "db_timeout"):
            docker_performed = True
            metadata = {
                "proxy_name": proxy_name,
                "database": database,
                "toxic_name": "db_timeout",
                "toxic_type": "timeout",
            }
            logger.info("Injected database failure on %s", proxy_name)
        else:
            logger.error(
                "Database failure NOT applied on %s: toxic missing or proxy disabled",
                proxy_name,
            )
    
    signals = [
        TelemetryEvent(
            timestamp=now,
            source=database,
            event_type="active_connections",
            value=100.0,  # e.g., 100% of pool size
            metadata={
                "log_message": "WARNING: Connection pool exhausted, refusing new connections",
                "root_cause_hint": "database_failure",
                **metadata
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="error_rate",
            value=0.55,
            metadata={
                "log_message": f"FATAL: Database connection timeout connecting to {database}",
                "root_cause_hint": "database_failure",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="latency",
            value=5000,
            metadata={
                "log_message": f"Query timeout 5000ms — connections: 100/100",
                "root_cause_hint": "database_failure",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="log_error",
            value=None,
            metadata={
                "log_message": f"ERROR: ConnectionPoolTimeoutException — no available connections",
                "root_cause_hint": "database_failure",
            },
        ),
    ]
    
    return FaultInjectionResult(
        signals=signals,
        docker_performed=docker_performed,
        service=service,
        metadata=metadata,
    )


def inject_dependency_outage(
    service: str = "payment-service",
    dependency: str = "rpc-service-primary",
    toxiproxy_client: ToxiproxyClient | None = None,
) -> FaultInjectionResult:
    """Simulate upstream dependency outage — external gateway stops responding.

    The log investigator may initially blame payment-service,
    but the metric investigator should correlate with gateway timeouts.
    """
    now = datetime.now(timezone.utc)
    
    docker_performed = False
    metadata = {}
    
    if toxiproxy_client:
        proxy_name = "payment-rpc-proxy"
        # Inject timeout toxic
        toxic = toxiproxy_client.add_toxic(
            proxy_name=proxy_name,
            toxic_name="outage_timeout",
            toxic_type="timeout",
            attributes={"timeout": 30000}
        )
        if toxic and _toxic_is_live(toxiproxy_client, proxy_name, "outage_timeout"):
            docker_performed = True
            metadata = {
                "proxy_name": proxy_name,
                "dependency": dependency,
                "toxic_name": "outage_timeout",
                "toxic_type": "timeout",
                "previous_upstream": "rpc-service-primary:5000",
                "secondary_upstream": "rpc-service-secondary:5000"
            }
            logger.info("Injected dependency outage on %s", proxy_name)
        else:
            logger.error(
                "Dependency outage NOT applied on %s: toxic missing or proxy disabled",
                proxy_name,
            )
    
    signals = [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="error_rate",
            value=0.35,
            metadata={
                "log_message": f"Timeout calling {dependency} from {service}",
                "root_cause_hint": "dependency_outage",
                **metadata
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=dependency,
            event_type="latency",
            value=30000,
            metadata={
                "log_message": f"{dependency} not responding — HTTP 504 Gateway Timeout",
                "root_cause_hint": "dependency_outage",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="log_error",
            value=None,
            metadata={
                "log_message": f"Circuit breaker OPEN for {dependency} — fallback failed",
                "root_cause_hint": "dependency_outage",
            },
        ),
    ]
    
    return FaultInjectionResult(
        signals=signals,
        docker_performed=docker_performed,
        service=service,
        metadata=metadata,
    )


async def inject_resource_exhaustion(
    service: str = "payment-service",
    cpu_quota_pct: int = 25,
    workers: int = 2,
    duration_seconds: int = 60,
    docker_controller: DockerController | None = None,
) -> FaultInjectionResult:
    """Simulate gradual CPU/memory exhaustion — slow degradation, not a spike.

    **Mock mode** (``docker_controller is None``):
        Returns deterministic telemetry signals only.

    **Real mode** (``docker_controller`` provided):
        Caps the container's CPU quota and spawns real CPU-burning processes
        inside it via ``DockerController.exhaust_resources`` — see that
        method for why both are needed together. Self-reverts after
        ``duration_seconds`` (this scenario has no real remediation hookup
        yet, unlike the other three).

    Note: the previous default target was ``order-service``, which was never
    a real docker-compose service — there was nothing for this scenario to
    act on. Defaults to ``payment-service`` (a real service, same default as
    the other three ``inject_*`` functions) instead.
    """
    now = datetime.now(timezone.utc)

    docker_performed = False
    metadata: dict[str, Any] = {}

    if docker_controller is not None:
        result = await docker_controller.exhaust_resources(
            service,
            cpu_quota_pct=cpu_quota_pct,
            workers=workers,
            duration_seconds=duration_seconds,
        )
        docker_performed = result.get("started", False)
        if docker_performed:
            metadata = {
                "cpu_quota_pct": cpu_quota_pct,
                "workers": workers,
                "duration_seconds": duration_seconds,
            }
            logger.info("Injected real resource exhaustion on %s", service)
        else:
            logger.error(
                "Cannot inject resource exhaustion on %s: %s", service, result.get("reason")
            )

    signals = [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="cpu_usage",
            value=0.95,
            metadata={
                "log_message": f"CPU usage at 95% on {service} — memory leak detected",
                "root_cause_hint": "resource_exhaustion",
                **metadata,
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="latency",
            value=1800,
            metadata={
                "log_message": "Gradual latency increase: 200ms → 600ms → 1800ms over 2 hours",
                "root_cause_hint": "resource_exhaustion",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="log_error",
            value=None,
            metadata={
                "log_message": "OOM warning: heap usage 92% — GC pauses increasing",
                "root_cause_hint": "resource_exhaustion",
            },
        ),
    ]

    return FaultInjectionResult(
        signals=signals,
        docker_performed=docker_performed,
        service=service,
        metadata=metadata,
    )

"""Real URL health monitoring — the bridge from "user gives us a URL" to a
genuine Incident running through the existing, unmodified orchestrator.

Detection is deliberately deterministic, not LLM-based: a target only
becomes an incident after `failure_threshold` consecutive failed health
checks, never on a single blip. The investigation stage downstream still
does its own (LLM or mock) reasoning over the real telemetry this module
produces — this module's only job is "did the app genuinely stop
responding, for real, more than once in a row."
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

import httpx

from backend.contracts import Incident, IncidentState, MonitoredTarget, TelemetryEvent
from backend.monitoring import targets as target_store

if TYPE_CHECKING:
    from backend.orchestrator.pipeline import IncidentOrchestrator
    from backend.platform.storage import Storage

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {
    IncidentState.RESOLVED,
    IncidentState.ESCALATED,
    IncidentState.REJECTED,
    IncidentState.FAILED,
}


async def check_url_health(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """One real HTTP check against a user-supplied URL.

    Treats 2xx/3xx/4xx as reachable ("success") — a 404 on the root path
    doesn't mean the app is down, it just means that path doesn't exist.
    Only 5xx, timeouts, and connection failures count as unhealthy, matching
    standard uptime-monitoring convention.
    """
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": resp.status_code < 500,
            "status_code": resp.status_code,
            "latency_ms": round(latency_ms, 1),
            "error": None,
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "status_code": None,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
        }


def _build_incident_signals(target: MonitoredTarget, result: dict[str, Any]) -> list[TelemetryEvent]:
    """Real evidence only — never fabricate a hypothesis the check didn't
    actually observe. Root cause is always the generic "service_error"
    bucket: we have zero visibility into a black-box external URL's
    deployments/DB/CPU, so guessing a more specific root cause would be
    fabricated, not observed.
    """
    now = datetime.now(timezone.utc)

    if result["error"]:
        log_message = f"Health check for {target.url} failed: {result['error']}"
    elif result["status_code"] is not None and result["status_code"] >= 500:
        log_message = f"{target.url} returned HTTP {result['status_code']}"
    else:
        log_message = f"{target.url} did not respond within the health-check timeout"

    signals = [
        TelemetryEvent(
            timestamp=now,
            source=target.name,
            event_type="log_error",
            value=None,
            metadata={"log_message": log_message, "root_cause_hint": "service_error"},
        ),
        TelemetryEvent(
            timestamp=now,
            source=target.name,
            event_type="error_rate",
            value=1.0,
            metadata={
                "log_message": (
                    f"{target.consecutive_failures} consecutive failed health checks "
                    f"for {target.url}"
                ),
                "root_cause_hint": "service_error",
            },
        ),
    ]
    if result["latency_ms"] is not None:
        signals.append(
            TelemetryEvent(
                timestamp=now,
                source=target.name,
                event_type="latency",
                value=result["latency_ms"],
                metadata={
                    "log_message": f"Latency {result['latency_ms']}ms on last check of {target.url}"
                },
            )
        )
    return signals


class TargetMonitor:
    """Periodically health-checks every monitoring-enabled MonitoredTarget
    and creates a real Incident (run through the existing orchestrator)
    once one crosses the consecutive-failure threshold.

    `get_orchestrator` is a callable rather than an instance so this always
    dispatches through the same singleton app.py uses (get_orchestrator()),
    not a stale reference captured at TargetMonitor construction time.
    """

    def __init__(
        self,
        storage: "Storage",
        get_orchestrator: Callable[[], "IncidentOrchestrator"],
        *,
        interval_seconds: float = 15.0,
        failure_threshold: int = 3,
        check_timeout: float = 5.0,
    ):
        self._storage = storage
        self._get_orchestrator = get_orchestrator
        self._interval = interval_seconds
        self._failure_threshold = failure_threshold
        self._check_timeout = check_timeout

    async def check_once(self) -> None:
        """Check every monitoring-enabled target exactly once."""
        targets = await target_store.list_monitoring_enabled_targets(self._storage)
        for target in targets:
            await self._check_target(target)

    async def _check_target(self, target: MonitoredTarget) -> None:
        # Keep active_incident_id accurate (informational — "the incident
        # currently tracking this target's state, if any") but this no
        # longer gates whether we poll or whether a new incident can be
        # created — see incident_reported below. url_monitor incidents can
        # reach a terminal state in well under a second (recommendation-only
        # remediation, single HTTP verification), so a still-ongoing real
        # outage must not re-arm incident creation just because the previous
        # incident already finished its pipeline run.
        if target.active_incident_id:
            incident = await self._storage.get_incident(target.active_incident_id)
            if incident is None or incident.state in _TERMINAL_STATES:
                target.active_incident_id = None

        result = await check_url_health(target.url, timeout=self._check_timeout)
        target.last_checked_at = datetime.now(timezone.utc)
        target.last_status_code = result["status_code"]
        target.last_latency_ms = result["latency_ms"]
        target.last_error = result["error"]

        if result["success"]:
            target.consecutive_failures = 0
            target.health_status = "healthy"
            # Genuine recovery — re-arm. A future new failure streak is a
            # new outage and may create a new incident.
            target.incident_reported = False
        else:
            target.consecutive_failures += 1
            target.health_status = "unhealthy"

            if target.consecutive_failures >= self._failure_threshold and not target.incident_reported:
                incident = self._build_incident(target, result)
                target.active_incident_id = incident.id
                target.incident_reported = True
                logger.warning(
                    "MonitoredTarget %s (%s) failed %d consecutive checks — creating incident %s",
                    target.name,
                    target.url,
                    target.consecutive_failures,
                    incident.id,
                )
                asyncio.create_task(self._run_incident(incident))

        await target_store.save_target(self._storage, target)

    def _build_incident(self, target: MonitoredTarget, result: dict[str, Any]) -> Incident:
        incident = Incident(
            service_name=target.name,
            target_url=target.url,
            state=IncidentState.CREATED,
            source="url_monitor",
        )
        incident.signals = _build_incident_signals(target, result)
        return incident

    async def _run_incident(self, incident: Incident) -> None:
        try:
            await self._storage.save_incident(incident)
            orchestrator = self._get_orchestrator()
            await orchestrator.run_pipeline(incident)
        except Exception:
            logger.exception("URL-monitor incident pipeline failed for %s", incident.id)

    async def run_forever(self) -> None:
        """Bounded-failure polling loop — one bad iteration (e.g. a target
        URL that raises something unexpected) must never kill monitoring for
        every other target forever."""
        while True:
            try:
                await self.check_once()
            except Exception:
                logger.exception("TargetMonitor.check_once failed")
            await asyncio.sleep(self._interval)

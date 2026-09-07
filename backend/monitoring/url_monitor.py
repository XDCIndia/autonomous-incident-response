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
import json
import logging
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

import httpx

from backend.contracts import Incident, IncidentState, MonitoredTarget, TelemetryEvent
from backend.monitoring import targets as target_store
from backend.monitoring.ssrf_guard import build_safe_transport

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


# ---------------------------------------------------------------------------
# Check type implementations (Phase 2 — richer monitoring signals)
# ---------------------------------------------------------------------------


def _resolve_json_path(data: Any, path: str) -> Any:
    """Resolve a simple JSON path like "$.status" or "$.data.items[0].name".

    Supports dot notation and array indexing. Returns the value at the path,
    or raises KeyError/IndexError if the path doesn't exist.
    """
    parts = path.lstrip("$").split(".")
    current = data
    for part in parts:
        if not part:
            continue
        bracket_match = re.match(r"^(\w+)\[(\d+)\]$", part)
        if bracket_match:
            key, idx = bracket_match.groups()
            current = current[key]
            current = current[int(idx)]
        else:
            current = current[part]
    return current


async def check_tcp_port(host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    """Check TCP connectivity to a host:port.

    Returns success=True if the TCP connection is established within timeout.
    This is a genuine network check — not a synthetic or mocked result.
    """
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": True,
            "latency_ms": round(latency_ms, 1),
            "error": None,
            "failure_type": None,
        }
    except (asyncio.TimeoutError, OSError) as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
            "failure_type": "connection_error",
        }


async def check_dns_resolution(hostname: str) -> dict[str, Any]:
    """Verify that a hostname resolves via DNS.

    Uses the system resolver. Returns success=True if at least one address
    is returned. This is a real DNS lookup, not a cache or synthetic result.
    """
    start = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
        latency_ms = (time.monotonic() - start) * 1000
        addresses = [info[4][0] for info in infos]
        return {
            "success": len(addresses) > 0,
            "latency_ms": round(latency_ms, 1),
            "error": None if addresses else "No addresses resolved",
            "failure_type": None if addresses else "connection_error",
            "addresses": addresses,
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
            "failure_type": "connection_error",
            "addresses": [],
        }


async def check_tls_certificate(hostname: str, port: int = 443, warn_days: int = 30) -> dict[str, Any]:
    """Check TLS certificate expiry for a hostname:port.

    Returns success=True if the certificate is valid and not expiring within
    warn_days. This performs a real TLS handshake — not a synthetic check.
    """
    start = time.monotonic()
    try:
        loop = asyncio.get_event_loop()

        # Simple synchronous TLS check via asyncio's SSL support
        ssl_ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port, ssl=ssl_ctx),
            timeout=5.0,
        )

        # Get the cert from the transport
        ssl_obj = writer.transport.get_extra_info("ssl_object")
        cert = ssl_obj.getpeercert() if ssl_obj else None
        writer.close()
        await writer.wait_closed()

        if cert is None:
            latency_ms = (time.monotonic() - start) * 1000
            return {
                "success": False,
                "latency_ms": round(latency_ms, 1),
                "error": "No TLS certificate received",
                "failure_type": "connection_error",
                "cert_expires_at": None,
                "cert_days_remaining": None,
            }

        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        not_after = not_after.replace(tzinfo=timezone.utc)
        days_remaining = (not_after - datetime.now(timezone.utc)).days
        latency_ms = (time.monotonic() - start) * 1000

        return {
            "success": days_remaining > warn_days,
            "latency_ms": round(latency_ms, 1),
            "error": None if days_remaining > warn_days else f"Certificate expires in {days_remaining} days",
            "failure_type": None if days_remaining > warn_days else "http_error",
            "cert_expires_at": not_after.isoformat(),
            "cert_days_remaining": days_remaining,
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
            "failure_type": "connection_error",
            "cert_expires_at": None,
            "cert_days_remaining": None,
        }


async def check_url_health(
    url: str,
    timeout: float = 5.0,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    body: Optional[str] = None,
    expected_body_pattern: Optional[str] = None,
    json_path: Optional[str] = None,
    json_path_expected: Optional[str] = None,
) -> dict[str, Any]:
    """One real HTTP check against a user-supplied URL.

    Treats 2xx/3xx/4xx as reachable ("success") — a 404 on the root path
    doesn't mean the app is down, it just means that path doesn't exist.
    Only 5xx, timeouts, and connection failures count as unhealthy, matching
    standard uptime-monitoring convention.

    Supports configurable HTTP method, headers, and body for authenticated
    endpoints. Body assertions (keyword/regex and JSON path) are evaluated
    against the real response — never fabricated.
    """
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=build_safe_transport()
        ) as client:
            req_kwargs: dict[str, Any] = {"url": url}
            if headers:
                req_kwargs["headers"] = headers
            if body and method.upper() in ("POST", "PUT", "PATCH"):
                req_kwargs["content"] = body
            resp = await client.request(method, **req_kwargs)
        latency_ms = (time.monotonic() - start) * 1000
        success = resp.status_code < 500
        try:
            body_bytes = resp.content
            body_size_bytes = len(body_bytes)
        except AttributeError:
            body_bytes = None
            body_size_bytes = None

        # --- Keyword/regex body assertion ---
        assertion_error = None
        if success and expected_body_pattern and body_bytes is not None:
            try:
                body_text = body_bytes.decode("utf-8", errors="replace")
                if not re.search(expected_body_pattern, body_text):
                    success = False
                    assertion_error = f"Body does not match pattern: {expected_body_pattern}"
            except re.error as e:
                assertion_error = f"Invalid regex pattern: {e}"

        # --- JSON path assertion ---
        if success and json_path and json_path_expected is not None and body_bytes is not None:
            try:
                body_json = json.loads(body_bytes)
                actual = _resolve_json_path(body_json, json_path)
                if str(actual) != str(json_path_expected):
                    success = False
                    assertion_error = f"JSON path {json_path} = {actual!r}, expected {json_path_expected!r}"
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                success = False
                assertion_error = f"JSON path evaluation failed: {e}"

        return {
            "success": success,
            "status_code": resp.status_code,
            "latency_ms": round(latency_ms, 1),
            "error": assertion_error,
            "failure_type": None if success else "http_error",
            "body_size_bytes": body_size_bytes,
        }
    except httpx.TimeoutException as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "status_code": None,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
            "failure_type": "timeout",
            "body_size_bytes": None,
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "status_code": None,
            "latency_ms": round(latency_ms, 1),
            "error": str(exc),
            "failure_type": "connection_error",
            "body_size_bytes": None,
        }


# ---------------------------------------------------------------------------
# Incident signal construction
# ---------------------------------------------------------------------------


def _build_incident_signals(target: MonitoredTarget, result: dict[str, Any]) -> list[TelemetryEvent]:
    """Real evidence only — never fabricate a hypothesis the check didn't
    actually observe. Root cause is always the generic "service_error"
    bucket: we have zero visibility into a black-box external URL's
    deployments/DB/CPU, so guessing a more specific root cause would be
    fabricated, not observed.

    Every signal's metadata carries the same shared_metadata block —
    status_code, failure_type, body_size_bytes, consecutive_failures,
    checked_at, target_url — all deterministically observed by
    check_url_health, never inferred or guessed. This is what lets the
    incident detail UI (and any future, smarter investigator) show real
    operational evidence beyond a single log line.
    """
    now = datetime.now(timezone.utc)
    failure_type = result.get("failure_type")

    if failure_type == "timeout":
        log_message = f"Health check for {target.url} timed out: {result['error']}"
    elif failure_type == "http_error":
        log_message = f"{target.url} returned HTTP {result['status_code']}"
    elif failure_type == "connection_error":
        log_message = f"Health check for {target.url} failed: {result['error']}"
    else:
        log_message = f"Health check for {target.url} failed"

    shared_metadata = {
        "root_cause_hint": "service_error",
        "status_code": result.get("status_code"),
        "failure_type": failure_type,
        "body_size_bytes": result.get("body_size_bytes"),
        "consecutive_failures": target.consecutive_failures,
        "checked_at": now.isoformat(),
        "target_url": target.url,
    }

    signals = [
        TelemetryEvent(
            timestamp=now,
            source=target.name,
            event_type="log_error",
            value=None,
            metadata={"log_message": log_message, **shared_metadata},
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
                **shared_metadata,
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
                    "log_message": f"Latency {result['latency_ms']}ms on last check of {target.url}",
                    **shared_metadata,
                },
            )
        )
    return signals


# ---------------------------------------------------------------------------
# TargetMonitor — the background polling loop
# ---------------------------------------------------------------------------


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

    async def _run_all_checks(self, target: MonitoredTarget) -> dict[str, Any]:
        """Run all configured checks for a target and return combined results.

        The HTTP check is always run. TCP, DNS, TLS, keyword/regex, and JSON
        path checks are run only when their configuration is present on the
        target. Every check result is a real network observation — never
        fabricated.
        """
        # Always run the HTTP check
        http_result = await check_url_health(
            target.url,
            timeout=self._check_timeout,
            method=target.http_method,
            headers=target.http_headers or None,
            body=target.http_body,
            expected_body_pattern=target.expected_body_pattern,
            json_path=target.json_path,
            json_path_expected=target.json_path_expected,
        )

        combined = {
            "success": http_result["success"],
            "status_code": http_result.get("status_code"),
            "latency_ms": http_result["latency_ms"],
            "error": http_result["error"],
            "failure_type": http_result.get("failure_type"),
            "body_size_bytes": http_result.get("body_size_bytes"),
            "checks_run": ["http"],
        }

        # TCP port check
        if target.check_tcp_port:
            from urllib.parse import urlsplit
            parsed = urlsplit(target.url)
            hostname = parsed.hostname or "localhost"
            tcp_result = await check_tcp_port(hostname, target.check_tcp_port, timeout=self._check_timeout)
            combined["checks_run"].append("tcp")
            combined["tcp_result"] = tcp_result
            if not tcp_result["success"]:
                combined["success"] = False
                if not combined["failure_type"]:
                    combined["failure_type"] = tcp_result["failure_type"]
                    combined["error"] = tcp_result["error"]

        # DNS resolution check
        if target.check_dns:
            from urllib.parse import urlsplit
            parsed = urlsplit(target.url)
            hostname = parsed.hostname or "localhost"
            dns_result = await check_dns_resolution(hostname)
            combined["checks_run"].append("dns")
            combined["dns_result"] = dns_result
            if not dns_result["success"]:
                combined["success"] = False
                if not combined["failure_type"]:
                    combined["failure_type"] = dns_result["failure_type"]
                    combined["error"] = dns_result["error"]

        # TLS certificate check
        if target.check_tls_expiry_days is not None:
            from urllib.parse import urlsplit
            parsed = urlsplit(target.url)
            hostname = parsed.hostname or "localhost"
            port = parsed.port or 443
            tls_result = await check_tls_certificate(hostname, port, warn_days=target.check_tls_expiry_days)
            combined["checks_run"].append("tls")
            combined["tls_result"] = tls_result
            if not tls_result["success"]:
                combined["success"] = False
                if not combined["failure_type"]:
                    combined["failure_type"] = tls_result["failure_type"]
                    combined["error"] = tls_result["error"]

        return combined

    async def _check_target(self, target: MonitoredTarget) -> None:
        if target.active_incident_id:
            incident = await self._storage.get_incident(target.active_incident_id)
            if incident is None or incident.state in _TERMINAL_STATES:
                target.active_incident_id = None

        result = await self._run_all_checks(target)
        target.last_checked_at = datetime.now(timezone.utc)
        target.last_status_code = result.get("status_code")
        target.last_latency_ms = result.get("latency_ms")
        target.last_error = result.get("error")
        target.last_failure_type = result.get("failure_type")
        target.last_body_size_bytes = result.get("body_size_bytes")

        # Update historical data
        target.total_checks += 1
        check_record = {
            "timestamp": target.last_checked_at.isoformat(),
            "success": result["success"],
            "latency_ms": result.get("latency_ms"),
            "status_code": result.get("status_code"),
            "failure_type": result.get("failure_type"),
            "checks_run": result.get("checks_run", ["http"]),
        }
        target.check_history.append(check_record)
        # Keep last 100 checks
        if len(target.check_history) > 100:
            target.check_history = target.check_history[-100:]

        if result["success"]:
            was_unhealthy = target.health_status == "unhealthy" or target.consecutive_failures > 0
            target.consecutive_failures = 0
            target.health_status = "healthy"
            target.incident_reported = False
            if was_unhealthy:
                target.last_recovered_at = target.last_checked_at
        else:
            target.consecutive_failures += 1
            target.health_status = "unhealthy"
            target.total_failures += 1

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

        # Compute rolling uptime percentage
        if target.check_history:
            successes = sum(1 for c in target.check_history if c["success"])
            target.uptime_percentage = round(successes / len(target.check_history) * 100, 2)

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

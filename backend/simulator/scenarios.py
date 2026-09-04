"""Fault injection scenarios.

Each function returns a list of TelemetryEvent signals that the pipeline
can process. The signals contain hints but NOT the answer — the root cause
must be determined by the investigation/arbiter pipeline.

Person 3 implements full fault scenarios here.
For the foundation, one scenario is fully implemented; others are stubs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.contracts import TelemetryEvent


def inject_bad_deployment(
    service: str = "payment-service",
    version: str = "v2.4.1",
    deployed_seconds_ago: float = 30.0,
) -> list[TelemetryEvent]:
    """Simulate a bad deployment — new version causes errors and latency spike.

    Signals are ordered chronologically. The deploy event comes FIRST,
    then the errors/latency follow.

    Returns:
        List of TelemetryEvent signals representing the incident.
    """
    now = datetime.now(timezone.utc)
    signals = [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="deploy",
            value=version,
            metadata={
                "log_message": f"Deployment {version} rolled out to {service}",
                "version": version,
                "deployed_seconds_ago": deployed_seconds_ago,
            },
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
    return signals


def inject_database_failure(
    service: str = "inventory-service",
) -> list[TelemetryEvent]:
    """Simulate database connection pool exhaustion.

    Returns signals showing connection timeouts and pool exhaustion.
    """
    now = datetime.now(timezone.utc)
    return [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="error_rate",
            value=0.60,
            metadata={
                "log_message": f"Database connection pool exhausted on {service}",
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


def inject_dependency_outage(
    service: str = "payment-service",
    dependency: str = "external-gateway",
) -> list[TelemetryEvent]:
    """Simulate upstream dependency outage — external gateway stops responding.

    The log investigator may initially blame payment-service,
    but the metric investigator should correlate with gateway timeouts.
    """
    now = datetime.now(timezone.utc)
    return [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="error_rate",
            value=0.35,
            metadata={
                "log_message": f"Timeout calling {dependency} from {service}",
                "root_cause_hint": "dependency_outage",
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


def inject_resource_exhaustion(
    service: str = "order-service",
) -> list[TelemetryEvent]:
    """Simulate gradual memory leak leading to resource exhaustion.

    Slow degradation, not a sudden spike.
    """
    now = datetime.now(timezone.utc)
    return [
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="cpu_usage",
            value=0.95,
            metadata={
                "log_message": f"CPU usage at 95% on {service} — memory leak detected",
                "root_cause_hint": "resource_exhaustion",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="latency",
            value=1800,
            metadata={
                "log_message": f"Gradual latency increase: 200ms → 600ms → 1800ms over 2 hours",
                "root_cause_hint": "resource_exhaustion",
            },
        ),
        TelemetryEvent(
            timestamp=now,
            source=service,
            event_type="log_error",
            value=None,
            metadata={
                "log_message": f"OOM warning: heap usage 92% — GC pauses increasing",
                "root_cause_hint": "resource_exhaustion",
            },
        ),
    ]

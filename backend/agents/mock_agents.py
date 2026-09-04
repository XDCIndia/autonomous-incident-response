"""Mock agent implementations for the foundation skeleton.

These produce deterministic results so the pipeline can be tested end-to-end.
Person 1 will replace these with real LLM-backed agents.

ALL results are computed from the signals — no random values.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.agents.base import (
    Arbiter,
    LogInvestigator,
    MetricInvestigator,
    Reporter,
    SeverityAgent,
)
from backend.contracts import (
    ArbiterResult,
    Incident,
    LogInvestigationResult,
    MetricInvestigationResult,
    SeverityLevel,
    SeverityResult,
    TelemetryEvent,
)

logger = logging.getLogger(__name__)


class MockLogInvestigator(LogInvestigator):
    """Deterministic log investigation — inspects signal metadata for keywords."""

    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> LogInvestigationResult:
        evidence: list[str] = []
        hypothesis = "No anomalies detected in logs"
        confidence = 0.3
        root_cause = "unknown"

        for sig in signals:
            msg = sig.metadata.get("log_message", "")
            if not msg:
                continue
            evidence.append(f"[{sig.source}] {msg}")

            # Keyword-based root cause detection (deterministic).
            # Order matters: dependency-outage text must be recognised before
            # generic "timeout" text, which otherwise maps to database_failure
            # and would send the wrong remediation action to the real env.
            msg_lower = msg.lower()
            if (
                "timeout calling" in msg_lower
                or "gateway timeout" in msg_lower
                or "not responding" in msg_lower
                or "circuit breaker open" in msg_lower
                or " 504 " in msg_lower
            ):
                confidence = 0.87
                hypothesis = f"Upstream dependency timeout from {sig.source}"
                root_cause = sig.metadata.get("root_cause_hint", "dependency_outage")
            elif (
                "connection pool" in msg_lower
                or "no available connections" in msg_lower
                or "database connection" in msg_lower
                or "query timeout" in msg_lower
            ):
                confidence = 0.88
                hypothesis = f"Connection/timeout issues from {sig.source}"
                root_cause = sig.metadata.get("root_cause_hint", "database_failure")
            elif "deploy" in msg_lower or "version" in msg_lower:
                confidence = 0.90
                hypothesis = f"Deployment detected near error window for {sig.source}"
                root_cause = "bad_deployment"
            elif "500" in msg or "error" in msg_lower:
                confidence = 0.85
                hypothesis = f"Detected errors from {sig.source}"
                root_cause = sig.metadata.get("root_cause_hint", "service_error")
            elif "latency" in msg_lower and "ms" in msg_lower:
                confidence = 0.82
                hypothesis = f"High latency detected for {sig.source}"

        logger.info("MockLogInvestigator: confidence=%.2f root_cause=%s", confidence, root_cause)
        return LogInvestigationResult(
            hypothesis=hypothesis,
            evidence=evidence,
            confidence=confidence,
            suggested_root_cause=root_cause,
        )


class MockMetricInvestigator(MetricInvestigator):
    """Deterministic metric investigation — inspects signal values."""

    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> MetricInvestigationResult:
        evidence: list[str] = []
        hypothesis = "No metric anomalies detected"
        confidence = 0.3
        root_cause = "unknown"
        metrics_summary: dict = {}

        for sig in signals:
            if sig.event_type == "error_rate":
                metrics_summary["error_rate"] = sig.value
                if sig.value and sig.value > 0.1:
                    confidence = 0.88
                    hypothesis = f"Error rate spike ({sig.value}) on {sig.source}"
                    root_cause = sig.metadata.get("root_cause_hint", "service_error")
                    evidence.append(f"Error rate: {sig.value} on {sig.source}")
            elif sig.event_type == "latency":
                metrics_summary["latency_ms"] = sig.value
                if sig.value and sig.value > 1000:
                    confidence = 0.85
                    hypothesis = f"High latency ({sig.value}ms) on {sig.source}"
                    root_cause = sig.metadata.get("root_cause_hint", "resource_exhaustion")
                    evidence.append(f"Latency: {sig.value}ms on {sig.source}")
            elif sig.event_type == "cpu_usage":
                metrics_summary["cpu_usage"] = sig.value
                if sig.value and sig.value > 0.9:
                    confidence = 0.80
                    hypothesis = f"High CPU ({sig.value}) on {sig.source}"
                    evidence.append(f"CPU: {sig.value} on {sig.source}")

        logger.info("MockMetricInvestigator: confidence=%.2f root_cause=%s", confidence, root_cause)
        return MetricInvestigationResult(
            hypothesis=hypothesis,
            evidence=evidence,
            confidence=confidence,
            suggested_root_cause=root_cause,
            metrics_summary=metrics_summary,
        )


class MockArbiter(Arbiter):
    """Deterministic arbiter — merges both investigators' findings."""

    CONFIDENCE_THRESHOLD = 0.7

    async def analyze(
        self,
        log_result: LogInvestigationResult,
        metric_result: MetricInvestigationResult,
        incident: Incident,
    ) -> ArbiterResult:
        # Check agreement
        agreements = log_result.suggested_root_cause == metric_result.suggested_root_cause

        if agreements:
            merged_confidence = max(log_result.confidence, metric_result.confidence)
            root_cause = log_result.suggested_root_cause or metric_result.suggested_root_cause
            merged_hypothesis = (
                f"Both investigators agree: {log_result.hypothesis}"
            )
            conflict = None
        else:
            # Take higher-confidence hypothesis
            if log_result.confidence >= metric_result.confidence:
                root_cause = log_result.suggested_root_cause
                merged_hypothesis = log_result.hypothesis
                merged_confidence = log_result.confidence * 0.85  # slight penalty for disagreement
            else:
                root_cause = metric_result.suggested_root_cause
                merged_hypothesis = metric_result.hypothesis
                merged_confidence = metric_result.confidence * 0.85
            conflict = (
                f"Log-agent suspects '{log_result.suggested_root_cause}' "
                f"({log_result.confidence:.0%}), "
                f"Metric-agent suspects '{metric_result.suggested_root_cause}' "
                f"({metric_result.confidence:.0%})"
            )

        evidence = list(log_result.evidence) + list(metric_result.evidence)

        logger.info(
            "MockArbiter: root_cause=%s confidence=%.2f conflict=%s",
            root_cause,
            merged_confidence,
            conflict is not None,
        )
        return ArbiterResult(
            merged_hypothesis=merged_hypothesis,
            root_cause=root_cause,
            confidence=merged_confidence,
            log_hypothesis_agrees=agreements or log_result.confidence >= metric_result.confidence,
            metric_hypothesis_agrees=agreements or metric_result.confidence >= log_result.confidence,
            conflict_description=conflict,
            evidence=evidence,
            contributing_factors=[f for f in [root_cause] if f != "unknown"],
        )


class MockSeverityAgent(SeverityAgent):
    """Deterministic severity assessment based on signal count and type."""

    async def assess(
        self,
        arbiter_result: ArbiterResult,
        incident: Incident,
    ) -> SeverityResult:
        # Count affected services
        affected = list({s.source for s in incident.signals})

        # Determine severity from root cause and affected services
        root_cause = arbiter_result.root_cause
        n_services = len(affected)

        if root_cause == "bad_deployment" or n_services >= 3:
            severity = SeverityLevel.P1
        elif root_cause in ("database_failure", "dependency_outage") or n_services >= 2:
            severity = SeverityLevel.P2
        elif root_cause == "resource_exhaustion":
            severity = SeverityLevel.P3
        else:
            severity = SeverityLevel.P4

        justification = (
            f"Root cause '{root_cause}' affects {n_services} service(s): "
            f"{', '.join(affected)}. Confidence: {arbiter_result.confidence:.0%}."
        )

        logger.info("MockSeverityAgent: severity=%s services=%d", severity, n_services)
        return SeverityResult(
            severity=severity,
            blast_radius=n_services,
            affected_services=affected,
            justification=justification,
        )


class MockReporter(Reporter):
    """Generates the final incident report from all pipeline results."""

    async def generate(self, incident: Incident):
        from backend.contracts import IncidentReport

        # Calculate duration
        duration = 0.0
        if incident.timeline and len(incident.timeline) >= 2:
            first = incident.timeline[0].timestamp
            last = incident.timeline[-1].timestamp
            duration = (last - first).total_seconds()

        root_cause = incident.arbiter_result.root_cause if incident.arbiter_result else "unknown"
        confidence = incident.arbiter_result.confidence if incident.arbiter_result else 0.0
        severity = incident.severity or SeverityLevel.P3
        remediation_action = (
            incident.remediation_result.action if incident.remediation_result else "none"
        )

        # Build result metrics from before/after state
        result_metrics = {}
        if incident.remediation_result:
            before = incident.remediation_result.before_state
            after = incident.remediation_result.after_state
            for key in set(list(before.keys()) + list(after.keys())):
                result_metrics[key] = {"before": before.get(key), "after": after.get(key)}

        return IncidentReport(
            incident_id=incident.id,
            service=incident.service_name,
            severity=severity,
            duration_seconds=duration,
            root_cause=root_cause,
            impact=f"Service '{incident.service_name}' affected — severity {severity.value}",
            remediation_action=remediation_action,
            result_metrics=result_metrics,
            confidence=confidence,
            prevention=f"Review deployment process for {incident.service_name} to prevent recurrence.",
            timeline_summary=[e.message for e in incident.timeline if e.message],
        )

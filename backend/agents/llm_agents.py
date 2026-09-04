"""LLM-backed agent implementations — Person 1's real root-cause analysis.

Replaces the deterministic keyword/threshold matching in mock_agents.py with
actual calls to backend.platform.llm_client.LLMClient. Evidence extraction
stays deterministic (raw log lines / metric readings only — never
hallucinated); the LLM's job is purely interpretation: hypothesis, root-cause
classification, and confidence.

Each agent composes the equivalent Mock* agent as a fallback and delegates to
it whenever the LLM response can't be parsed into the expected JSON shape —
LLMClient itself never raises (it degrades to a mock string response when both
providers fail or no key is configured), but that mock string isn't valid
JSON, so this is the layer that guarantees the pipeline never crashes and
never receives a malformed result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.agents.base import Arbiter, LogInvestigator, MetricInvestigator
from backend.agents.mock_agents import MockArbiter, MockLogInvestigator, MockMetricInvestigator
from backend.contracts import (
    ArbiterResult,
    Incident,
    LogInvestigationResult,
    MetricInvestigationResult,
    TelemetryEvent,
)
from backend.platform.llm_client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)

# Closed vocabulary — backend.remediation.actions.ROOT_CAUSE_ACTION_MAP only
# knows how to remediate these exact strings, so any LLM output outside this
# set is coerced to "unknown" rather than trusted verbatim.
VALID_ROOT_CAUSES = {
    "bad_deployment",
    "database_failure",
    "dependency_outage",
    "resource_exhaustion",
    "service_error",
    "unknown",
}

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_response(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from an LLM completion.

    Handles the common non-conformance cases: markdown code fences around the
    JSON, or extra prose before/after it. Returns None if nothing usable is
    found — callers must have a deterministic fallback for that case.
    """
    stripped = _JSON_FENCE_RE.sub("", text.strip())
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    match = _JSON_OBJECT_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _clamp_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.3


def _validate_root_cause(value: object) -> str:
    return value if isinstance(value, str) and value in VALID_ROOT_CAUSES else "unknown"


_ROOT_CAUSE_SCHEMA = (
    '"root_cause" (one of "bad_deployment", "database_failure", "dependency_outage", '
    '"resource_exhaustion", "service_error", "unknown")'
)

_LOG_SYSTEM_PROMPT = (
    "You are an expert Site Reliability Engineer performing root-cause analysis "
    "on incident log lines. Respond with ONLY a single JSON object — no prose, "
    "no markdown code fences. Schema: {\"hypothesis\": string, " + _ROOT_CAUSE_SCHEMA +
    ', "confidence": number between 0.0 and 1.0}.'
)

_METRIC_SYSTEM_PROMPT = (
    "You are an expert Site Reliability Engineer performing root-cause analysis "
    "on incident metrics. Respond with ONLY a single JSON object — no prose, "
    "no markdown code fences. Schema: {\"hypothesis\": string, " + _ROOT_CAUSE_SCHEMA +
    ', "confidence": number between 0.0 and 1.0}.'
)

_ARBITER_SYSTEM_PROMPT = (
    "You are an incident commander reconciling two independent investigations "
    "of the same incident. Respond with ONLY a single JSON object — no prose, "
    "no markdown code fences. Schema: {\"merged_hypothesis\": string, " + _ROOT_CAUSE_SCHEMA +
    ', "confidence": number between 0.0 and 1.0, "conflict_description": '
    'string or null, "contributing_factors": array of strings}.'
)


class LLMLogInvestigator(LogInvestigator):
    """LLM-backed log investigation."""

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or get_llm_client()
        self._fallback = MockLogInvestigator()

    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> LogInvestigationResult:
        evidence = [
            f"[{sig.source}] {sig.metadata['log_message']}"
            for sig in signals
            if sig.metadata.get("log_message")
        ]
        if not evidence:
            return LogInvestigationResult(
                hypothesis="No anomalies detected in logs",
                evidence=[],
                confidence=0.3,
                suggested_root_cause="unknown",
            )

        prompt = f"Service: {incident.service_name}\nLog evidence:\n" + "\n".join(evidence)
        if extra_context:
            prompt += f"\n\nAdditional context: {extra_context}"
        prompt += "\n\nAnalyze the logs and return the JSON verdict."

        raw = await self._llm.generate(prompt, system=_LOG_SYSTEM_PROMPT, max_tokens=512)
        parsed = _parse_json_response(raw)
        if parsed is None:
            logger.warning("LLMLogInvestigator: unparseable LLM response, falling back to deterministic heuristic")
            fallback = await self._fallback.investigate(signals, incident, extra_context)
            fallback.evidence = evidence
            return fallback

        return LogInvestigationResult(
            hypothesis=str(parsed.get("hypothesis") or "No anomalies detected in logs"),
            evidence=evidence,
            confidence=_clamp_confidence(parsed.get("confidence")),
            suggested_root_cause=_validate_root_cause(parsed.get("root_cause")),
        )


class LLMMetricInvestigator(MetricInvestigator):
    """LLM-backed metric investigation."""

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or get_llm_client()
        self._fallback = MockMetricInvestigator()

    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> MetricInvestigationResult:
        metrics_summary: dict = {}
        evidence: list[str] = []
        for sig in signals:
            if sig.event_type == "error_rate" and sig.value is not None:
                metrics_summary["error_rate"] = sig.value
                evidence.append(f"Error rate: {sig.value} on {sig.source}")
            elif sig.event_type == "latency" and sig.value is not None:
                metrics_summary["latency_ms"] = sig.value
                evidence.append(f"Latency: {sig.value}ms on {sig.source}")
            elif sig.event_type == "cpu_usage" and sig.value is not None:
                metrics_summary["cpu_usage"] = sig.value
                evidence.append(f"CPU: {sig.value} on {sig.source}")

        if not metrics_summary:
            return MetricInvestigationResult(
                hypothesis="No metric anomalies detected",
                evidence=[],
                confidence=0.3,
                suggested_root_cause="unknown",
                metrics_summary={},
            )

        prompt = f"Service: {incident.service_name}\nMetrics:\n" + "\n".join(evidence)
        if extra_context:
            prompt += f"\n\nAdditional context: {extra_context}"
        prompt += "\n\nAnalyze the metrics and return the JSON verdict."

        raw = await self._llm.generate(prompt, system=_METRIC_SYSTEM_PROMPT, max_tokens=512)
        parsed = _parse_json_response(raw)
        if parsed is None:
            logger.warning("LLMMetricInvestigator: unparseable LLM response, falling back to deterministic heuristic")
            fallback = await self._fallback.investigate(signals, incident, extra_context)
            fallback.evidence = evidence
            fallback.metrics_summary = metrics_summary
            return fallback

        return MetricInvestigationResult(
            hypothesis=str(parsed.get("hypothesis") or "No metric anomalies detected"),
            evidence=evidence,
            confidence=_clamp_confidence(parsed.get("confidence")),
            suggested_root_cause=_validate_root_cause(parsed.get("root_cause")),
            metrics_summary=metrics_summary,
        )


class LLMArbiter(Arbiter):
    """LLM-backed reconciliation of the log and metric investigations."""

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or get_llm_client()
        self._fallback = MockArbiter()

    async def analyze(
        self,
        log_result: LogInvestigationResult,
        metric_result: MetricInvestigationResult,
        incident: Incident,
    ) -> ArbiterResult:
        prompt = (
            f"Log investigator: hypothesis='{log_result.hypothesis}' "
            f"root_cause='{log_result.suggested_root_cause}' confidence={log_result.confidence:.2f}\n"
            f"Metric investigator: hypothesis='{metric_result.hypothesis}' "
            f"root_cause='{metric_result.suggested_root_cause}' confidence={metric_result.confidence:.2f}\n\n"
            "Reconcile these findings into a single verdict and return the JSON."
        )

        raw = await self._llm.generate(prompt, system=_ARBITER_SYSTEM_PROMPT, max_tokens=512)
        parsed = _parse_json_response(raw)
        if parsed is None:
            logger.warning("LLMArbiter: unparseable LLM response, falling back to deterministic heuristic")
            return await self._fallback.analyze(log_result, metric_result, incident)

        root_cause = _validate_root_cause(parsed.get("root_cause"))
        conflict = parsed.get("conflict_description")
        contributing = parsed.get("contributing_factors")

        return ArbiterResult(
            merged_hypothesis=str(parsed.get("merged_hypothesis") or "Reconciled by LLM arbiter"),
            root_cause=root_cause,
            confidence=_clamp_confidence(parsed.get("confidence")),
            log_hypothesis_agrees=root_cause == log_result.suggested_root_cause,
            metric_hypothesis_agrees=root_cause == metric_result.suggested_root_cause,
            conflict_description=str(conflict) if conflict else None,
            evidence=list(log_result.evidence) + list(metric_result.evidence),
            contributing_factors=[str(f) for f in contributing] if isinstance(contributing, list) else [],
        )

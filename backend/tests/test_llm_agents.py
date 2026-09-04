"""Unit tests for the LLM-backed agent implementations.

No live network calls — a fake LLMClient is injected into every agent under
test (real network access is exercised only by test_llm_client.py). These
tests focus on: prompt-independent evidence extraction staying deterministic,
successful JSON-parse paths, and the fallback-to-mock-heuristic path when the
LLM returns something unparseable.
"""

from __future__ import annotations

import pytest

from backend.agents.llm_agents import (
    LLMArbiter,
    LLMLogInvestigator,
    LLMMetricInvestigator,
    _clamp_confidence,
    _parse_json_response,
    _validate_root_cause,
)
from backend.contracts import Incident, LogInvestigationResult, MetricInvestigationResult, TelemetryEvent


class FakeLLMClient:
    """Returns a canned response string, recording every call it receives."""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def generate(self, prompt: str, system: str = "", **kwargs):
        self.calls.append({"prompt": prompt, "system": system, **kwargs})
        return self.response


@pytest.fixture
def sample_incident():
    return Incident(service_name="payment-service")


@pytest.fixture
def sample_signals():
    return [
        TelemetryEvent(
            source="payment-service",
            event_type="error_rate",
            value=0.45,
            metadata={"log_message": "Payment API returned 500"},
        ),
        TelemetryEvent(
            source="payment-service",
            event_type="latency",
            value=2400,
            metadata={"log_message": "Latency increased to 2400ms"},
        ),
    ]


class TestJsonParsingHelpers:
    def test_parses_plain_json(self):
        assert _parse_json_response('{"a": 1}') == {"a": 1}

    def test_strips_markdown_fences(self):
        assert _parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_extracts_json_from_surrounding_prose(self):
        assert _parse_json_response('Sure, here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}

    def test_unparseable_text_returns_none(self):
        assert _parse_json_response("Based on the analysis... Confidence: 0.85.") is None

    def test_clamp_confidence_out_of_range(self):
        assert _clamp_confidence(1.5) == 1.0
        assert _clamp_confidence(-0.2) == 0.0

    def test_clamp_confidence_non_numeric(self):
        assert _clamp_confidence("high") == 0.3

    def test_validate_root_cause_accepts_known_value(self):
        assert _validate_root_cause("bad_deployment") == "bad_deployment"

    def test_validate_root_cause_rejects_unknown_value(self):
        assert _validate_root_cause("a_disgruntled_intern") == "unknown"
        assert _validate_root_cause(None) == "unknown"


class TestLLMLogInvestigator:
    @pytest.mark.asyncio
    async def test_empty_signals_skips_llm_call(self, sample_incident):
        fake = FakeLLMClient(response="should never be read")
        agent = LLMLogInvestigator(llm_client=fake)
        result = await agent.investigate([], sample_incident)
        assert result.confidence == 0.3
        assert result.suggested_root_cause == "unknown"
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_valid_json_response_is_used(self, sample_signals, sample_incident):
        fake = FakeLLMClient(
            response='{"hypothesis": "Bad rollout", "root_cause": "bad_deployment", "confidence": 0.92}'
        )
        agent = LLMLogInvestigator(llm_client=fake)
        result = await agent.investigate(sample_signals, sample_incident)
        assert result.hypothesis == "Bad rollout"
        assert result.suggested_root_cause == "bad_deployment"
        assert result.confidence == 0.92
        assert len(result.evidence) == 2
        assert len(fake.calls) == 1

    @pytest.mark.asyncio
    async def test_llm_supplied_root_cause_outside_vocabulary_is_coerced(self, sample_signals, sample_incident):
        fake = FakeLLMClient(response='{"hypothesis": "x", "root_cause": "cosmic_rays", "confidence": 0.9}')
        agent = LLMLogInvestigator(llm_client=fake)
        result = await agent.investigate(sample_signals, sample_incident)
        assert result.suggested_root_cause == "unknown"

    @pytest.mark.asyncio
    async def test_unparseable_response_falls_back_to_mock_heuristic(self, sample_signals, sample_incident):
        fake = FakeLLMClient(response="Based on the analysis... Confidence: 0.85.")
        agent = LLMLogInvestigator(llm_client=fake)
        result = await agent.investigate(sample_signals, sample_incident)
        # Same deterministic verdict MockLogInvestigator would produce for
        # these exact signals: the "500" line sets root_cause="service_error",
        # then the later "...ms" latency line overwrites confidence to 0.82
        # (its branch doesn't touch root_cause) — last-matching-signal wins.
        assert result.suggested_root_cause == "service_error"
        assert result.confidence == 0.82
        assert len(result.evidence) == 2

    @pytest.mark.asyncio
    async def test_extra_context_included_in_prompt(self, sample_signals, sample_incident):
        fake = FakeLLMClient(response='{"hypothesis": "h", "root_cause": "unknown", "confidence": 0.5}')
        agent = LLMLogInvestigator(llm_client=fake)
        await agent.investigate(sample_signals, sample_incident, extra_context="prior incident #42 was similar")
        assert "prior incident #42 was similar" in fake.calls[0]["prompt"]


class TestLLMMetricInvestigator:
    @pytest.mark.asyncio
    async def test_no_relevant_metrics_skips_llm_call(self, sample_incident):
        fake = FakeLLMClient(response="should never be read")
        agent = LLMMetricInvestigator(llm_client=fake)
        signals = [TelemetryEvent(source="x", event_type="deploy", value=None)]
        result = await agent.investigate(signals, sample_incident)
        assert result.confidence == 0.3
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_valid_json_response_is_used(self, sample_signals, sample_incident):
        fake = FakeLLMClient(
            response='{"hypothesis": "Error spike", "root_cause": "service_error", "confidence": 0.87}'
        )
        agent = LLMMetricInvestigator(llm_client=fake)
        result = await agent.investigate(sample_signals, sample_incident)
        assert result.suggested_root_cause == "service_error"
        assert result.confidence == 0.87
        assert result.metrics_summary["error_rate"] == 0.45
        assert result.metrics_summary["latency_ms"] == 2400

    @pytest.mark.asyncio
    async def test_unparseable_response_falls_back_to_mock_heuristic(self, sample_signals, sample_incident):
        fake = FakeLLMClient(response="not json at all")
        agent = LLMMetricInvestigator(llm_client=fake)
        result = await agent.investigate(sample_signals, sample_incident)
        # The later latency(2400) signal's branch overwrites root_cause to
        # "resource_exhaustion" after the error_rate(0.45) signal set it —
        # same last-matching-signal behavior as MockMetricInvestigator.
        assert result.suggested_root_cause == "resource_exhaustion"
        assert result.metrics_summary["error_rate"] == 0.45


class TestLLMArbiter:
    @pytest.mark.asyncio
    async def test_valid_json_response_is_used(self, sample_incident):
        log_result = LogInvestigationResult(
            hypothesis="Deploy detected", suggested_root_cause="bad_deployment", confidence=0.9, evidence=["log1"]
        )
        metric_result = MetricInvestigationResult(
            hypothesis="Errors spiked", suggested_root_cause="bad_deployment", confidence=0.85, evidence=["metric1"]
        )
        fake = FakeLLMClient(
            response=(
                '{"merged_hypothesis": "Bad deploy confirmed", "root_cause": "bad_deployment", '
                '"confidence": 0.95, "conflict_description": null, "contributing_factors": ["deploy v2.1"]}'
            )
        )
        agent = LLMArbiter(llm_client=fake)
        result = await agent.analyze(log_result, metric_result, sample_incident)
        assert result.merged_hypothesis == "Bad deploy confirmed"
        assert result.root_cause == "bad_deployment"
        assert result.confidence == 0.95
        assert result.conflict_description is None
        assert result.contributing_factors == ["deploy v2.1"]
        assert result.evidence == ["log1", "metric1"]

    @pytest.mark.asyncio
    async def test_unparseable_response_falls_back_to_mock_heuristic(self, sample_incident):
        log_result = LogInvestigationResult(
            hypothesis="a", suggested_root_cause="database_failure", confidence=0.80
        )
        metric_result = MetricInvestigationResult(
            hypothesis="b", suggested_root_cause="resource_exhaustion", confidence=0.75
        )
        fake = FakeLLMClient(response="I couldn't determine a root cause with confidence.")
        agent = LLMArbiter(llm_client=fake)
        result = await agent.analyze(log_result, metric_result, sample_incident)
        # Same deterministic disagreement-penalty verdict MockArbiter produces.
        assert result.conflict_description is not None
        assert result.confidence < 0.80

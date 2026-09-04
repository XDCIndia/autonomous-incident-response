"""Unit tests for mock agent implementations."""

import pytest
from backend.agents.mock_agents import (
    MockLogInvestigator,
    MockMetricInvestigator,
    MockArbiter,
    MockSeverityAgent,
    MockReporter,
)
from backend.contracts import (
    Incident,
    SeverityLevel,
    TelemetryEvent,
)


@pytest.fixture
def sample_signals():
    return [
        TelemetryEvent(
            source="payment-service",
            event_type="error_rate",
            value=0.45,
            metadata={"log_message": "Payment API returned 500", "root_cause_hint": "bad_deployment"},
        ),
        TelemetryEvent(
            source="payment-service",
            event_type="latency",
            value=2400,
            metadata={"log_message": "Latency increased to 2400ms", "root_cause_hint": "bad_deployment"},
        ),
    ]


@pytest.fixture
def sample_incident():
    return Incident(service_name="payment-service")


class TestMockLogInvestigator:
    @pytest.mark.asyncio
    async def test_investigate_with_signals(self, sample_signals, sample_incident):
        agent = MockLogInvestigator()
        result = await agent.investigate(sample_signals, sample_incident)
        assert result.confidence > 0.5
        assert result.suggested_root_cause == "bad_deployment"
        assert len(result.evidence) > 0

    @pytest.mark.asyncio
    async def test_investigate_empty_signals(self, sample_incident):
        agent = MockLogInvestigator()
        result = await agent.investigate([], sample_incident)
        assert result.confidence < 0.5


class TestMockMetricInvestigator:
    @pytest.mark.asyncio
    async def test_investigate_with_signals(self, sample_signals, sample_incident):
        agent = MockMetricInvestigator()
        result = await agent.investigate(sample_signals, sample_incident)
        assert result.confidence > 0.5
        assert "error_rate" in result.metrics_summary

    @pytest.mark.asyncio
    async def test_investigate_empty_signals(self, sample_incident):
        agent = MockMetricInvestigator()
        result = await agent.investigate([], sample_incident)
        assert result.confidence < 0.5


class TestMockArbiter:
    @pytest.mark.asyncio
    async def test_analyze_agreement(self, sample_incident):
        from backend.contracts import LogInvestigationResult, MetricInvestigationResult

        log_result = LogInvestigationResult(
            suggested_root_cause="bad_deployment",
            confidence=0.85,
            hypothesis="Detected errors",
            evidence=["Error line 1"],
        )
        metric_result = MetricInvestigationResult(
            suggested_root_cause="bad_deployment",
            confidence=0.88,
            hypothesis="High latency",
            evidence=["Latency spike"],
        )

        agent = MockArbiter()
        result = await agent.analyze(log_result, metric_result, sample_incident)
        assert result.root_cause == "bad_deployment"
        assert result.confidence >= 0.85
        assert result.conflict_description is None

    @pytest.mark.asyncio
    async def test_analyze_conflict(self, sample_incident):
        from backend.contracts import LogInvestigationResult, MetricInvestigationResult

        log_result = LogInvestigationResult(
            suggested_root_cause="database_failure",
            confidence=0.80,
        )
        metric_result = MetricInvestigationResult(
            suggested_root_cause="resource_exhaustion",
            confidence=0.75,
        )

        agent = MockArbiter()
        result = await agent.analyze(log_result, metric_result, sample_incident)
        assert result.conflict_description is not None
        assert result.confidence < 0.80  # penalty for disagreement


class TestMockSeverityAgent:
    @pytest.mark.asyncio
    async def test_p1_for_deployment(self, sample_incident):
        from backend.contracts import ArbiterResult

        sample_incident.signals = [
            TelemetryEvent(source="payment-service", event_type="error_rate", value=0.45),
        ]
        arbiter_result = ArbiterResult(root_cause="bad_deployment", confidence=0.9)

        agent = MockSeverityAgent()
        result = await agent.assess(arbiter_result, sample_incident)
        assert result.severity == SeverityLevel.P1

    @pytest.mark.asyncio
    async def test_p3_for_resource_exhaustion(self, sample_incident):
        from backend.contracts import ArbiterResult

        sample_incident.signals = [
            TelemetryEvent(source="order-service", event_type="cpu_usage", value=0.95),
        ]
        arbiter_result = ArbiterResult(root_cause="resource_exhaustion", confidence=0.8)

        agent = MockSeverityAgent()
        result = await agent.assess(arbiter_result, sample_incident)
        assert result.severity == SeverityLevel.P3


class TestMockReporter:
    @pytest.mark.asyncio
    async def test_generate_report(self, sample_incident):
        from backend.contracts import ArbiterResult, RemediationResult, TimelineEvent, PipelineStage

        sample_incident.severity = SeverityLevel.P1
        sample_incident.arbiter_result = ArbiterResult(
            root_cause="bad_deployment",
            confidence=0.90,
        )
        sample_incident.remediation_result = RemediationResult(
            action="rollback_deploy",
            success=True,
            message="Rolled back v2.4.1 → v2.4.0",
        )
        sample_incident.timeline = [
            TimelineEvent(stage=PipelineStage.DETECTION, message="Detected"),
            TimelineEvent(stage=PipelineStage.REPORT, message="Reported"),
        ]

        agent = MockReporter()
        report = await agent.generate(sample_incident)
        assert report.incident_id == sample_incident.id
        assert report.root_cause == "bad_deployment"
        assert report.severity == SeverityLevel.P1
        assert len(report.timeline_summary) == 2

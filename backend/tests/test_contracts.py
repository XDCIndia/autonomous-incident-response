"""Contract tests — verify shared Pydantic models work correctly.

These tests ensure the integration boundary is stable.
"""

import pytest
from datetime import datetime, timezone

from backend.contracts import (
    Incident,
    IncidentState,
    SeverityLevel,
    AutonomyLevel,
    PipelineStage,
    TelemetryEvent,
    LogInvestigationResult,
    MetricInvestigationResult,
    ArbiterResult,
    SeverityResult,
    RemediationRequest,
    RemediationResult,
    VerificationResult,
    IncidentReport,
    TimelineEvent,
    PipelineResult,
)


class TestIncident:
    def test_create_default(self):
        incident = Incident(service_name="test-service")
        assert incident.service_name == "test-service"
        assert incident.state == IncidentState.CREATED
        assert incident.severity is None
        assert incident.id  # UUID generated
        assert incident.created_at

    def test_serialization_roundtrip(self):
        incident = Incident(service_name="test-service")
        data = incident.model_dump(mode="json")
        restored = Incident.model_validate(data)
        assert restored.id == incident.id
        assert restored.service_name == incident.service_name


class TestTimelineEvent:
    def test_create_event(self):
        event = TimelineEvent(
            stage=PipelineStage.DETECTION,
            status="completed",
            message="Incident detected",
        )
        assert event.stage == PipelineStage.DETECTION
        assert event.status == "completed"
        assert event.id  # UUID generated

    def test_serialization(self):
        event = TimelineEvent(
            stage=PipelineStage.INVESTIGATION,
            status="started",
            message="Starting investigation",
        )
        data = event.model_dump(mode="json")
        assert data["stage"] == "investigation"
        assert data["status"] == "started"


class TestInvestigationResults:
    def test_log_result(self):
        result = LogInvestigationResult(
            hypothesis="Test hypothesis",
            evidence=["Evidence 1", "Evidence 2"],
            confidence=0.85,
            suggested_root_cause="bad_deployment",
        )
        assert result.confidence == 0.85
        assert len(result.evidence) == 2

    def test_metric_result(self):
        result = MetricInvestigationResult(
            hypothesis="High latency",
            confidence=0.80,
            metrics_summary={"error_rate": 0.45, "latency_ms": 2400},
        )
        assert result.metrics_summary["error_rate"] == 0.45


class TestArbiterResult:
    def test_create(self):
        result = ArbiterResult(
            merged_hypothesis="Both agree",
            root_cause="bad_deployment",
            confidence=0.90,
        )
        assert result.root_cause == "bad_deployment"

    def test_conflict(self):
        result = ArbiterResult(
            merged_hypothesis="Conflict resolved",
            root_cause="database_failure",
            confidence=0.75,
            conflict_description="Agents disagree",
        )
        assert result.conflict_description == "Agents disagree"


class TestSeverity:
    def test_severity_levels(self):
        for level in SeverityLevel:
            result = SeverityResult(severity=level)
            assert result.severity == level

    def test_autonomy_levels(self):
        for level in AutonomyLevel:
            assert level.value in ("assist", "semi", "autonomous")


class TestRemediation:
    def test_request(self):
        req = RemediationRequest(
            action="rollback_deploy",
            target_service="payment-service",
        )
        assert req.action == "rollback_deploy"

    def test_result(self):
        res = RemediationResult(
            action="rollback_deploy",
            success=True,
            message="Rolled back v2.4.1 → v2.4.0",
        )
        assert res.success is True


class TestPipelineResult:
    def test_create(self):
        incident = Incident(service_name="test")
        result = PipelineResult(incident=incident, success=True)
        assert result.success is True
        assert result.incident.service_name == "test"


class TestTelemetryEvent:
    def test_create(self):
        event = TelemetryEvent(
            source="payment-service",
            event_type="error_rate",
            value=0.45,
        )
        assert event.source == "payment-service"
        assert event.value == 0.45

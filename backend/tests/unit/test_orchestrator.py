"""Comprehensive unit tests for the orchestrator pipeline.

Tests cover:
1. Normal successful flow
2. Parallel investigation coordination
3. Arbiter confidence retry
4. Low confidence -> ASSIST
5. P1/P2 -> SEMI_AUTONOMOUS
6. P3/P4 -> AUTONOMOUS
7. Approval accepted -> remediation continues
8. Approval rejected -> remediation skipped
9. Remediation failure
10. Verification failure
11. Timeline generation
12. Final report state

All external dependencies (agents, remediation, storage, event bus) are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.base import (
    Arbiter,
    LogInvestigator,
    MetricInvestigator,
    Reporter,
    SeverityAgent,
)
from backend.contracts import (
    ArbiterResult,
    AutonomyLevel,
    Incident,
    IncidentReport,
    IncidentState,
    LogInvestigationResult,
    MetricInvestigationResult,
    PipelineStage,
    RemediationRequest,
    RemediationResult,
    SeverityLevel,
    SeverityResult,
    TelemetryEvent,
    VerificationResult,
)
from backend.orchestrator import IncidentOrchestrator
from backend.orchestrator.nodes import VerificationInterface
from backend.remediation.actions import RemediationEngine


# ---------------------------------------------------------------------------
# Fixtures: mock agents that produce deterministic results
# ---------------------------------------------------------------------------


class FakeLogInvestigator(LogInvestigator):
    """Fake log investigator — configurable confidence and root cause."""

    def __init__(self, confidence: float = 0.85, root_cause: str = "bad_deployment"):
        self._confidence = confidence
        self._root_cause = root_cause

    async def investigate(self, signals, incident, extra_context=None):
        return LogInvestigationResult(
            hypothesis=f"Fake log hypothesis for {self._root_cause}",
            evidence=[f"Log evidence from {len(signals)} signals"],
            confidence=self._confidence,
            suggested_root_cause=self._root_cause,
        )


class FakeMetricInvestigator(MetricInvestigator):
    """Fake metric investigator — configurable confidence and root cause."""

    def __init__(self, confidence: float = 0.88, root_cause: str = "bad_deployment"):
        self._confidence = confidence
        self._root_cause = root_cause

    async def investigate(self, signals, incident, extra_context=None):
        return MetricInvestigationResult(
            hypothesis=f"Fake metric hypothesis for {self._root_cause}",
            evidence=[f"Metric evidence from {len(signals)} signals"],
            confidence=self._confidence,
            suggested_root_cause=self._root_cause,
            metrics_summary={"error_rate": 0.45, "latency_ms": 2400},
        )


class FakeArbiter(Arbiter):
    """Fake arbiter — returns a fixed ArbiterResult."""

    def __init__(
        self,
        confidence: float = 0.90,
        root_cause: str = "bad_deployment",
        agree: bool = True,
    ):
        self._confidence = confidence
        self._root_cause = root_cause
        self._agree = agree

    async def analyze(self, log_result, metric_result, incident):
        return ArbiterResult(
            merged_hypothesis=f"Arbiter: root cause is {self._root_cause}",
            root_cause=self._root_cause,
            confidence=self._confidence,
            log_hypothesis_agrees=self._agree,
            metric_hypothesis_agrees=self._agree,
            conflict_description=None if self._agree else "Agents disagree",
            evidence=[f"Arbiter evidence for {self._root_cause}"],
            contributing_factors=[self._root_cause] if self._root_cause != "unknown" else [],
        )


class FakeSeverityAgent(SeverityAgent):
    """Fake severity agent — returns a fixed severity."""

    def __init__(self, severity: SeverityLevel = SeverityLevel.P3):
        self._severity = severity

    async def assess(self, arbiter_result, incident):
        affected = list({s.source for s in incident.signals}) if incident.signals else ["unknown"]
        return SeverityResult(
            severity=self._severity,
            blast_radius=len(affected),
            affected_services=affected,
            justification=f"Fake severity {self._severity.value}",
        )


class FakeReporter(Reporter):
    """Fake reporter — returns a minimal report."""

    async def generate(self, incident):
        return IncidentReport(
            incident_id=incident.id,
            service=incident.service_name,
            severity=incident.severity or SeverityLevel.P3,
            root_cause=incident.arbiter_result.root_cause if incident.arbiter_result else "unknown",
            impact=f"Service {incident.service_name} affected",
            remediation_action=(
                incident.remediation_result.action if incident.remediation_result else "none"
            ),
            confidence=incident.arbiter_result.confidence if incident.arbiter_result else 0.0,
            timeline_summary=[e.message for e in incident.timeline if e.message],
        )


class FakeVerification(VerificationInterface):
    """Fake verification — configurable result."""

    def __init__(self, verified: bool = True):
        self._verified = verified

    async def verify(self, incident):
        return VerificationResult(
            verified=self._verified,
            checks_passed=3 if self._verified else 1,
            checks_total=3,
            message="All checks passed" if self._verified else "Checks failed",
            recovered_metrics={"error_rate": 0.0 if self._verified else 0.35},
        )


class FakeRemediationEngine:
    """Fake remediation engine — records calls, returns configurable result."""

    def __init__(self, success: bool = True):
        self._success = success
        self.execute_calls: list[RemediationRequest] = []

    def get_recommended_action(self, root_cause: str) -> str:
        mapping = {
            "bad_deployment": "rollback_deploy",
            "database_failure": "reset_connection_pool",
            "dependency_outage": "circuit_break",
            "resource_exhaustion": "scale_up",
        }
        return mapping.get(root_cause, "restart_service")

    def requires_approval(self, severity: SeverityLevel) -> bool:
        return severity in (SeverityLevel.P1, SeverityLevel.P2)

    async def execute(self, request):
        self.execute_calls.append(request)
        return RemediationResult(
            action=request.action,
            success=self._success,
            message=f"Executed {request.action}" if self._success else f"Failed {request.action}",
            before_state={"version": "v2.4.1"},
            after_state={"version": "v2.4.0"} if self._success else {"version": "v2.4.1"},
        )


class FakeStorage:
    """Fake in-memory storage."""

    def __init__(self):
        self.incidents: dict[str, Incident] = {}
        self.timelines: dict[str, list] = {}

    async def init_db(self):
        pass

    async def save_incident(self, incident):
        self.incidents[incident.id] = incident

    async def get_incident(self, incident_id):
        return self.incidents.get(incident_id)

    async def list_incidents(self, limit=50):
        return list(self.incidents.values())[:limit]

    async def append_timeline_event(self, incident_id, event):
        self.timelines.setdefault(incident_id, []).append(event)

    async def get_timeline(self, incident_id):
        return self.timelines.get(incident_id, [])

    async def close(self):
        pass


class FakeEventBus:
    """Fake event bus — records published events."""

    def __init__(self):
        self.published: dict[str, list] = {}

    def subscribe(self, incident_id):
        return asyncio.Queue()

    def unsubscribe(self, incident_id, queue):
        pass

    async def publish(self, incident_id, event):
        self.published.setdefault(incident_id, []).append(event)

    async def get_next_event(self, incident_id, queue, timeout=30.0):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signals(service: str = "payment-service") -> list[TelemetryEvent]:
    """Create sample telemetry signals for an incident."""
    return [
        TelemetryEvent(
            source=service,
            event_type="error_rate",
            value=0.45,
            metadata={
                "log_message": f"Payment API returned 500 on {service}",
                "root_cause_hint": "bad_deployment",
            },
        ),
        TelemetryEvent(
            source=service,
            event_type="latency",
            value=2400,
            metadata={
                "log_message": f"Latency increased to 2400ms on {service}",
                "root_cause_hint": "bad_deployment",
            },
        ),
    ]


def make_incident(service: str = "payment-service") -> Incident:
    """Create a sample incident with signals."""
    incident = Incident(service_name=service)
    incident.signals = make_signals(service)
    return incident


def build_orchestrator(
    log_confidence: float = 0.85,
    metric_confidence: float = 0.88,
    arbiter_confidence: float = 0.90,
    root_cause: str = "bad_deployment",
    severity: SeverityLevel = SeverityLevel.P3,
    remediation_success: bool = True,
    verification_verified: bool = True,
    arbiter_agree: bool = True,
) -> IncidentOrchestrator:
    """Build an orchestrator with configurable fake dependencies."""
    return IncidentOrchestrator(
        log_investigator=FakeLogInvestigator(log_confidence, root_cause),
        metric_investigator=FakeMetricInvestigator(metric_confidence, root_cause),
        arbiter=FakeArbiter(arbiter_confidence, root_cause, arbiter_agree),
        severity_agent=FakeSeverityAgent(severity),
        reporter=FakeReporter(),
        remediation_engine=FakeRemediationEngine(remediation_success),
        verification=FakeVerification(verification_verified),
        storage=FakeStorage(),
        event_bus=FakeEventBus(),
    )


# ---------------------------------------------------------------------------
# Test 1: Normal successful flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_successful_flow():
    """Full pipeline runs successfully from detection to report."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P3,  # AUTONOMOUS (no approval needed)
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.state == IncidentState.RESOLVED
    assert result.incident.report is not None
    assert result.incident.report.root_cause == "bad_deployment"
    assert result.incident.severity == SeverityLevel.P3
    assert result.incident.autonomy_level == AutonomyLevel.AUTONOMOUS
    assert result.duration_seconds >= 0


# ---------------------------------------------------------------------------
# Test 2: Parallel investigation coordination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_investigation():
    """Both log and metric investigators are called and results are stored."""
    orchestrator = build_orchestrator()
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.log_result is not None
    assert result.incident.metric_result is not None
    assert result.incident.log_result.confidence == 0.85
    assert result.incident.metric_result.confidence == 0.88
    assert result.incident.log_result.suggested_root_cause == "bad_deployment"
    assert result.incident.metric_result.suggested_root_cause == "bad_deployment"


# ---------------------------------------------------------------------------
# Test 3: Arbiter confidence retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arbiter_confidence_retry():
    """When arbiter confidence is below threshold, investigation retries."""
    # First arbiter call returns low confidence, but we need to simulate
    # the retry improving things. Use a counter to track calls.
    call_count = 0

    class RetryArbiter(Arbiter):
        async def analyze(self, log_result, metric_result, incident):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ArbiterResult(
                    merged_hypothesis="Low confidence first try",
                    root_cause="unknown",
                    confidence=0.50,  # below threshold
                )
            return ArbiterResult(
                merged_hypothesis="Higher confidence on retry",
                    root_cause="bad_deployment",
                    confidence=0.90,  # above threshold
                )

    orchestrator = IncidentOrchestrator(
        log_investigator=FakeLogInvestigator(0.85, "bad_deployment"),
        metric_investigator=FakeMetricInvestigator(0.88, "bad_deployment"),
        arbiter=RetryArbiter(),
        severity_agent=FakeSeverityAgent(SeverityLevel.P3),
        reporter=FakeReporter(),
        remediation_engine=FakeRemediationEngine(True),
        verification=FakeVerification(True),
        storage=FakeStorage(),
        event_bus=FakeEventBus(),
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert call_count == 2  # initial + 1 retry
    assert result.incident.arbiter_result.confidence == 0.90


# ---------------------------------------------------------------------------
# Test 4: Low confidence -> ASSIST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_assist():
    """After max retries with low confidence, autonomy is ASSIST (no remediation)."""
    low_confidence_arbiter = FakeArbiter(confidence=0.40, root_cause="unknown")

    orchestrator = IncidentOrchestrator(
        log_investigator=FakeLogInvestigator(0.40, "unknown"),
        metric_investigator=FakeMetricInvestigator(0.35, "unknown"),
        arbiter=low_confidence_arbiter,
        severity_agent=FakeSeverityAgent(SeverityLevel.P4),
        reporter=FakeReporter(),
        remediation_engine=FakeRemediationEngine(True),
        verification=FakeVerification(True),
        storage=FakeStorage(),
        event_bus=FakeEventBus(),
    )
    # Set max_retries to 0 so it gives up immediately
    orchestrator._graph  # graph is already built, but we set state in run_pipeline
    incident = make_incident()

    # Patch the settings to set max_retries to 0
    with patch("backend.orchestrator.pipeline.get_settings") as mock_settings:
        mock_settings.return_value.rca_max_retries = 0
        mock_settings.return_value.rca_confidence_threshold = 0.7
        mock_settings.return_value.approval_timeout_minutes = 15
        result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.autonomy_level == AutonomyLevel.ASSIST
    assert result.incident.state == IncidentState.RESOLVED
    # Remediation should NOT have been executed
    assert result.incident.remediation_result is None
    assert result.incident.report is not None


# ---------------------------------------------------------------------------
# Test 5: P1/P2 -> SEMI_AUTONOMOUS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p1_p2_semi_autonomous():
    """High severity (P1/P2) with high confidence -> SEMI_AUTONOMOUS."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P1,
    )
    incident = make_incident()

    # The pipeline will pause for approval. Approve it.
    async def approve_after_delay():
        await asyncio.sleep(0.1)
        await orchestrator.approve(incident.id)

    approve_task = asyncio.create_task(approve_after_delay())
    result = await orchestrator.run_pipeline(incident)
    await approve_task

    assert result.success is True
    assert result.incident.autonomy_level == AutonomyLevel.SEMI_AUTONOMOUS
    assert result.incident.severity == SeverityLevel.P1
    assert result.incident.remediation_result is not None
    assert result.incident.state == IncidentState.RESOLVED


# ---------------------------------------------------------------------------
# Test 6: P3/P4 -> AUTONOMOUS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_p4_autonomous():
    """Low severity (P3/P4) with high confidence -> AUTONOMOUS (no approval needed)."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P4,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.autonomy_level == AutonomyLevel.AUTONOMOUS
    assert result.incident.severity == SeverityLevel.P4
    assert result.incident.remediation_result is not None
    assert result.incident.state == IncidentState.RESOLVED


# ---------------------------------------------------------------------------
# Test 7: Approval accepted -> remediation continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_accepted():
    """When human approves SEMI_AUTONOMOUS, remediation proceeds."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P2,
    )
    incident = make_incident()

    async def approve_after_delay():
        await asyncio.sleep(0.1)
        result = await orchestrator.approve(incident.id)
        assert result is True

    approve_task = asyncio.create_task(approve_after_delay())
    result = await orchestrator.run_pipeline(incident)
    await approve_task

    assert result.success is True
    assert result.incident.autonomy_level == AutonomyLevel.SEMI_AUTONOMOUS
    assert result.incident.remediation_result is not None
    assert result.incident.remediation_result.success is True


# ---------------------------------------------------------------------------
# Test 8: Approval rejected -> remediation skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_rejected():
    """When human rejects SEMI_AUTONOMOUS, remediation is skipped."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P2,
    )
    incident = make_incident()

    async def reject_after_delay():
        await asyncio.sleep(0.1)
        result = await orchestrator.reject(incident.id)
        assert result is True

    reject_task = asyncio.create_task(reject_after_delay())
    result = await orchestrator.run_pipeline(incident)
    await reject_task

    assert result.success is True
    assert result.incident.autonomy_level == AutonomyLevel.SEMI_AUTONOMOUS
    # Remediation should NOT have been executed
    assert result.incident.remediation_result is None
    assert result.incident.report is not None
    # The incident must NOT claim recovery — remediation was declined.
    assert result.incident.state == IncidentState.REJECTED
    assert result.incident.verification_result is None


# ---------------------------------------------------------------------------
# Test 9: Remediation failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remediation_failure():
    """When remediation fails, the pipeline still completes with a report."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P3,  # AUTONOMOUS
        remediation_success=False,
        verification_verified=False,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.remediation_result is not None
    assert result.incident.remediation_result.success is False
    # Verification should reflect the failure
    assert result.incident.verification_result is not None
    assert result.incident.verification_result.verified is False
    # Report should still be generated
    assert result.incident.report is not None


# ---------------------------------------------------------------------------
# Test 10: Verification failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verification_failure():
    """When verification fails, incident is escalated."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P3,
        remediation_success=True,
        verification_verified=False,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.verification_result is not None
    assert result.incident.verification_result.verified is False
    # Incident should be escalated, not resolved
    assert result.incident.state == IncidentState.ESCALATED
    assert result.incident.report is not None


# ---------------------------------------------------------------------------
# Test 11: Timeline generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_generation():
    """Every pipeline stage produces timeline events."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P3,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    timeline = result.incident.timeline
    assert len(timeline) >= 8  # At least one event per stage

    # Verify each stage is represented
    stages = {e.stage for e in timeline}
    expected_stages = {
        PipelineStage.DETECTION,
        PipelineStage.INVESTIGATION,
        PipelineStage.ARBITER,
        PipelineStage.SEVERITY,
        PipelineStage.AUTONOMY,
        PipelineStage.REMEDIATION,
        PipelineStage.VERIFICATION,
        PipelineStage.REPORT,
    }
    assert expected_stages.issubset(stages)

    # Verify events have proper status values
    for event in timeline:
        assert event.status in ("started", "completed", "failed")
        assert event.message  # every event has a message


# ---------------------------------------------------------------------------
# Test 12: Final report state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_report_state():
    """The final report contains all expected fields from the pipeline."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P1,
    )
    incident = make_incident()

    # Approve the SEMI_AUTONOMOUS incident
    async def approve_after_delay():
        await asyncio.sleep(0.1)
        await orchestrator.approve(incident.id)

    approve_task = asyncio.create_task(approve_after_delay())
    result = await orchestrator.run_pipeline(incident)
    await approve_task

    assert result.success is True
    report = result.incident.report

    assert report is not None
    assert report.incident_id == incident.id
    assert report.service == "payment-service"
    assert report.severity == SeverityLevel.P1
    assert report.root_cause == "bad_deployment"
    assert report.confidence == 0.90
    assert report.remediation_action == "rollback_deploy"
    assert len(report.timeline_summary) > 0
    assert report.impact  # non-empty


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_timeout_rejects():
    """When approval times out, remediation is rejected."""
    orchestrator = build_orchestrator(
        arbiter_confidence=0.90,
        severity=SeverityLevel.P1,
    )
    incident = make_incident()

    # Patch timeout to 0 seconds so it times out immediately
    with patch("backend.orchestrator.nodes.get_settings") as mock_settings:
        settings_mock = MagicMock()
        settings_mock.rca_max_retries = 1
        settings_mock.rca_confidence_threshold = 0.7
        settings_mock.approval_timeout_minutes = 0  # 0 minute timeout
        mock_settings.return_value = settings_mock

        # Rebuild graph with new settings
        from backend.orchestrator.graph import build_graph

        orchestrator._graph = build_graph(
            log_investigator=orchestrator.log_investigator,
            metric_investigator=orchestrator.metric_investigator,
            arbiter_agent=orchestrator.arbiter,
            severity_agent=orchestrator.severity_agent,
            reporter=orchestrator.reporter,
            remediation_engine=orchestrator.remediation_engine,
            verification=orchestrator.verification,
            storage=orchestrator.storage,
            event_bus=orchestrator.event_bus,
            approval_events=orchestrator._approval_events,
            approval_decisions=orchestrator._approval_decisions,
        )

        result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    # Timeout treated as rejection — no remediation, and no false recovery.
    assert result.incident.remediation_result is None
    assert result.incident.report is not None
    assert result.incident.state == IncidentState.REJECTED
    assert result.incident.verification_result is None


@pytest.mark.asyncio
async def test_approve_returns_false_when_no_pending():
    """approve() returns False when there is no pending approval."""
    orchestrator = build_orchestrator()
    result = await orchestrator.approve("nonexistent-id")
    assert result is False


@pytest.mark.asyncio
async def test_reject_returns_false_when_no_pending():
    """reject() returns False when there is no pending approval."""
    orchestrator = build_orchestrator()
    result = await orchestrator.reject("nonexistent-id")
    assert result is False


@pytest.mark.asyncio
async def test_conflicting_investigators():
    """When investigators disagree, arbiter resolves and pipeline continues."""
    orchestrator = build_orchestrator(
        log_confidence=0.85,
        metric_confidence=0.88,
        arbiter_confidence=0.80,  # penalty for disagreement
        root_cause="bad_deployment",
        arbiter_agree=False,  # agents disagree
        severity=SeverityLevel.P3,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    assert result.incident.arbiter_result.conflict_description is not None
    assert result.incident.arbiter_result.confidence == 0.80


@pytest.mark.asyncio
async def test_different_root_causes():
    """Different root causes produce different remediation actions."""
    test_cases = [
        ("bad_deployment", "rollback_deploy"),
        ("database_failure", "reset_connection_pool"),
        ("dependency_outage", "circuit_break"),
        ("resource_exhaustion", "scale_up"),
    ]

    for root_cause, expected_action in test_cases:
        orchestrator = build_orchestrator(
            arbiter_confidence=0.90,
            root_cause=root_cause,
            severity=SeverityLevel.P3,
        )
        incident = make_incident()

        result = await orchestrator.run_pipeline(incident)

        assert result.success is True
        assert result.incident.remediation_request.action == expected_action, (
            f"Root cause '{root_cause}' should map to action '{expected_action}'"
        )


@pytest.mark.asyncio
async def test_storage_saves_incident():
    """The storage receives the incident at each stage."""
    storage = FakeStorage()
    orchestrator = IncidentOrchestrator(
        log_investigator=FakeLogInvestigator(),
        metric_investigator=FakeMetricInvestigator(),
        arbiter=FakeArbiter(),
        severity_agent=FakeSeverityAgent(SeverityLevel.P3),
        reporter=FakeReporter(),
        remediation_engine=FakeRemediationEngine(True),
        verification=FakeVerification(True),
        storage=storage,
        event_bus=FakeEventBus(),
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    # Storage should have the incident
    saved = await storage.get_incident(incident.id)
    assert saved is not None
    assert saved.state == IncidentState.RESOLVED
    # Timeline events should be recorded
    assert incident.id in storage.timelines
    assert len(storage.timelines[incident.id]) >= 8


@pytest.mark.asyncio
async def test_event_bus_publishes_events():
    """The event bus receives timeline events for WebSocket streaming."""
    event_bus = FakeEventBus()
    orchestrator = IncidentOrchestrator(
        log_investigator=FakeLogInvestigator(),
        metric_investigator=FakeMetricInvestigator(),
        arbiter=FakeArbiter(),
        severity_agent=FakeSeverityAgent(SeverityLevel.P3),
        reporter=FakeReporter(),
        remediation_engine=FakeRemediationEngine(True),
        verification=FakeVerification(True),
        storage=FakeStorage(),
        event_bus=event_bus,
    )
    incident = make_incident()

    result = await orchestrator.run_pipeline(incident)

    assert result.success is True
    # Event bus should have received events for this incident
    assert incident.id in event_bus.published
    assert len(event_bus.published[incident.id]) >= 8

"""Incident response pipeline orchestrator.

Executes the full pipeline:
  detect → investigate → arbiter → severity → autonomy → remediate → verify → report

Every stage appends a TimelineEvent.
The orchestrator depends ONLY on shared contracts/interfaces.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from backend.agents.mock_agents import (
    MockArbiter,
    MockLogInvestigator,
    MockMetricInvestigator,
    MockReporter,
    MockSeverityAgent,
)
from backend.contracts import (
    AutonomyLevel,
    Incident,
    IncidentReport,
    IncidentState,
    PipelineResult,
    PipelineStage,
    RemediationRequest,
    SeverityLevel,
    TelemetryEvent,
    TimelineEvent,
)
from backend.platform.events import EventBus, get_event_bus
from backend.platform.storage import Storage, get_storage
from backend.remediation.actions import RemediationEngine

logger = logging.getLogger(__name__)

# Confidence threshold for retry
CONFIDENCE_THRESHOLD = 0.7
MAX_RETRIES = 1


class IncidentOrchestrator:
    """Orchestrates the full incident response pipeline.

    Person 2 replaces mock agents with real implementations.
    """

    def __init__(
        self,
        storage: Storage | None = None,
        event_bus: EventBus | None = None,
    ):
        self.storage = storage or get_storage()
        self.event_bus = event_bus or get_event_bus()

        # Agents (mock for foundation)
        self.log_investigator = MockLogInvestigator()
        self.metric_investigator = MockMetricInvestigator()
        self.arbiter = MockArbiter()
        self.severity_agent = MockSeverityAgent()
        self.reporter = MockReporter()
        self.remediation_engine = RemediationEngine()

    async def _emit(self, incident: Incident, stage: PipelineStage, status: str, message: str, metadata: dict | None = None):
        """Append a timeline event and broadcast via WebSocket."""
        event = TimelineEvent(
            stage=stage,
            status=status,
            message=message,
            metadata=metadata or {},
        )
        incident.timeline.append(event)
        incident.current_stage = stage
        incident.updated_at = event.timestamp

        # Save to storage
        await self.storage.save_incident(incident)
        await self.storage.append_timeline_event(incident.id, event)

        # Broadcast to WebSocket subscribers
        await self.event_bus.publish(incident.id, event)

    async def run_pipeline(self, incident: Incident) -> PipelineResult:
        """Execute the full incident response pipeline.

        This is the main entry point called by the trigger endpoint.
        """
        start_time = time.time()

        try:
            # Stage 1: Detection
            incident.state = IncidentState.DETECTED
            await self._emit(
                incident, PipelineStage.DETECTION, "started",
                f"Detecting incident for service '{incident.service_name}'",
            )
            await self._detect(incident)
            await self._emit(
                incident, PipelineStage.DETECTION, "completed",
                f"Incident detected — {len(incident.signals)} signal(s) received",
            )

            # Stage 2: Investigation (parallel)
            incident.state = IncidentState.INVESTIGATING
            await self._emit(
                incident, PipelineStage.INVESTIGATION, "started",
                "Starting log and metric investigation in parallel",
            )
            await self._investigate(incident)
            await self._emit(
                incident, PipelineStage.INVESTIGATION, "completed",
                f"Investigation complete — "
                f"log confidence={incident.log_result.confidence:.0%}, "
                f"metric confidence={incident.metric_result.confidence:.0%}",
            )

            # Stage 3: Arbiter
            incident.state = IncidentState.ANALYZING
            await self._emit(
                incident, PipelineStage.ARBITER, "started",
                "Arbiter reconciling investigator findings",
            )
            await self._arbiter(incident)
            await self._emit(
                incident, PipelineStage.ARBITER, "completed",
                f"Root cause: '{incident.arbiter_result.root_cause}' "
                f"(confidence: {incident.arbiter_result.confidence:.0%})"
                + (f" — CONFLICT: {incident.arbiter_result.conflict_description}"
                   if incident.arbiter_result.conflict_description else ""),
            )

            # Confidence-gated retry
            retry_count = 0
            while (
                incident.arbiter_result.confidence < CONFIDENCE_THRESHOLD
                and retry_count < MAX_RETRIES
            ):
                retry_count += 1
                await self._emit(
                    incident, PipelineStage.INVESTIGATION, "started",
                    f"Confidence below threshold ({CONFIDENCE_THRESHOLD:.0%}) — retry #{retry_count}",
                )
                incident.arbiter_result.retry_count = retry_count
                await self._investigate(incident)
                await self._arbiter(incident)
                await self._emit(
                    incident, PipelineStage.INVESTIGATION, "completed",
                    f"Retry #{retry_count} complete — confidence now {incident.arbiter_result.confidence:.0%}",
                )

            # Stage 4: Severity
            incident.state = IncidentState.SEVERITY_DETERMINED
            await self._emit(
                incident, PipelineStage.SEVERITY, "started",
                "Assessing severity and blast radius",
            )
            await self._severity(incident)
            await self._emit(
                incident, PipelineStage.SEVERITY, "completed",
                f"Severity: {incident.severity.value} — blast radius: {incident.severity_result.blast_radius} service(s)",
            )

            # Stage 5: Autonomy decision
            await self._emit(
                incident, PipelineStage.AUTONOMY, "started",
                "Determining autonomy level",
            )
            await self._autonomy(incident)
            await self._emit(
                incident, PipelineStage.AUTONOMY, "completed",
                f"Autonomy: {incident.autonomy_level.value} "
                + ("— requires human approval" if incident.autonomy_level == AutonomyLevel.SEMI_AUTONOMOUS else "— auto-executing"),
            )

            # Stage 6: Remediation
            incident.state = IncidentState.REMEDIATING
            await self._emit(
                incident, PipelineStage.REMEDIATION, "started",
                f"Executing remediation: {incident.remediation_request.action}",
            )
            await self._remediate(incident)
            await self._emit(
                incident, PipelineStage.REMEDIATION, "completed",
                f"Remediation {'succeeded' if incident.remediation_result.success else 'failed'}: "
                f"{incident.remediation_result.message}",
            )

            # Stage 7: Verification
            incident.state = IncidentState.VERIFYING
            await self._emit(
                incident, PipelineStage.VERIFICATION, "started",
                "Verifying remediation effectiveness",
            )
            await self._verify(incident)
            await self._emit(
                incident, PipelineStage.VERIFICATION, "completed",
                f"Verification: {'PASSED' if incident.verification_result.verified else 'FAILED'} "
                f"({incident.verification_result.checks_passed}/{incident.verification_result.checks_total} checks)",
            )

            # Stage 8: Report
            incident.state = IncidentState.RESOLVED
            await self._emit(
                incident, PipelineStage.REPORT, "started",
                "Generating incident report",
            )
            incident.report = await self.reporter.generate(incident)
            await self._emit(
                incident, PipelineStage.REPORT, "completed",
                f"Incident report generated — root cause: {incident.report.root_cause}",
            )

            await self.storage.save_incident(incident)

            elapsed = time.time() - start_time
            return PipelineResult(
                incident=incident,
                success=True,
                total_stages_completed=len(incident.timeline),
                duration_seconds=elapsed,
            )

        except Exception as e:
            logger.error("Pipeline failed for incident %s: %s", incident.id, e)
            incident.state = IncidentState.FAILED
            await self._emit(
                incident, incident.current_stage or PipelineStage.DETECTION, "failed",
                f"Pipeline failed: {str(e)}",
            )
            await self.storage.save_incident(incident)
            return PipelineResult(
                incident=incident,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )

    # -----------------------------------------------------------------------
    # Pipeline stages — each calls the appropriate agent
    # -----------------------------------------------------------------------

    async def _detect(self, incident: Incident):
        """Stage 1: Detection — signals are already attached."""
        pass  # Detection is instant — signals were injected by the simulator

    async def _investigate(self, incident: Incident):
        """Stage 2: Parallel log and metric investigation."""
        incident.log_result = await self.log_investigator.investigate(
            incident.signals, incident,
        )
        incident.metric_result = await self.metric_investigator.investigate(
            incident.signals, incident,
        )

    async def _arbiter(self, incident: Incident):
        """Stage 3: Arbiter reconciles both investigators."""
        incident.arbiter_result = await self.arbiter.analyze(
            incident.log_result, incident.metric_result, incident,
        )

    async def _severity(self, incident: Incident):
        """Stage 4: Determine severity and blast radius."""
        incident.severity_result = await self.severity_agent.assess(
            incident.arbiter_result, incident,
        )
        incident.severity = incident.severity_result.severity

    async def _autonomy(self, incident: Incident):
        """Stage 5: Decide autonomy level."""
        requires_approval = self.remediation_engine.requires_approval(incident.severity)

        if incident.arbiter_result.confidence < CONFIDENCE_THRESHOLD:
            incident.autonomy_level = AutonomyLevel.ASSIST
        elif requires_approval:
            incident.autonomy_level = AutonomyLevel.SEMI_AUTONOMOUS
        else:
            incident.autonomy_level = AutonomyLevel.AUTONOMOUS

        # Build remediation request
        recommended_action = self.remediation_engine.get_recommended_action(
            incident.arbiter_result.root_cause
        )
        incident.remediation_request = RemediationRequest(
            action=recommended_action,
            description=f"Remediation for '{incident.arbiter_result.root_cause}' on '{incident.service_name}'",
            target_service=incident.service_name,
            requires_approval=requires_approval,
        )

    async def _remediate(self, incident: Incident):
        """Stage 6: Execute remediation."""
        incident.remediation_result = await self.remediation_engine.execute(
            incident.remediation_request,
        )

    async def _verify(self, incident: Incident):
        """Stage 7: Verify remediation worked."""
        from backend.contracts import VerificationResult

        # Mock verification — check that remediation succeeded
        success = incident.remediation_result.success if incident.remediation_result else False
        incident.verification_result = VerificationResult(
            verified=success,
            checks_passed=3 if success else 1,
            checks_total=3,
            message="All checks passed" if success else "Some checks failed",
            recovered_metrics={
                "error_rate": 0.0 if success else 0.35,
                "latency_ms": 150 if success else 2400,
            },
        )

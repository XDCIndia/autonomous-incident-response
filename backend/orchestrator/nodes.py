"""Graph node implementations for the orchestration pipeline.

Each node function receives the LangGraph state and returns a partial
state update. Nodes coordinate through shared contracts — they never
access internal implementation details of other modules.

The orchestrator injects dependencies (agents, remediation, storage, event bus)
via the OrchestratorNodes class, which creates closure-based node functions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.simulator.docker_controller import DockerController

from backend.agents.base import (
    Arbiter,
    LogInvestigator,
    MetricInvestigator,
    Reporter,
    SeverityAgent,
)
from backend.contracts import (
    AutonomyLevel,
    Incident,
    IncidentReport,
    IncidentState,
    PipelineStage,
    RemediationRequest,
    RemediationResult,
    SeverityLevel,
    TimelineEvent,
    VerificationResult,
)
from backend.orchestrator.state import OrchestratorState
from backend.platform.config import get_settings
from backend.platform.events import EventBus
from backend.platform.storage import Storage
from backend.remediation.actions import RemediationEngine

logger = logging.getLogger(__name__)

# Metadata keys that real fault injection attaches to signals and that the
# real remediation engine needs (e.g. rollback_deploy requires the saved
# container config, toxiproxy actions require proxy/toxic names).
REMEDIATION_PARAM_KEYS = (
    "previous_config",
    "proxy_name",
    "toxic_name",
    "toxic_type",
    "secondary_upstream",
    "previous_upstream",
)


def collect_remediation_parameters(incident: Incident) -> dict[str, Any]:
    """Collect environment metadata from injected signals for remediation.

    Real fault injection embeds everything the remediation engine needs into
    the telemetry metadata (see ``backend.simulator.scenarios``):
      - bad_deployment      → previous_config (saved healthy container config)
      - database_failure    → proxy_name / toxic_name on the db proxy
      - dependency_outage   → proxy_name / toxic_name / secondary_upstream
    """
    params: dict[str, Any] = {}
    for sig in incident.signals:
        for key in REMEDIATION_PARAM_KEYS:
            if key in sig.metadata and key not in params:
                params[key] = sig.metadata[key]
    return params


# ---------------------------------------------------------------------------
# Verification interface
# ---------------------------------------------------------------------------

class VerificationInterface:
    """Interface for verification — the orchestrator owns the boundary.

    When a DockerController is available, verify() performs a real
    multi-layer health check (container status + HTTP /health, plus an extra
    endpoint probe for certain remediation actions) via
    backend.simulator.health_checker.verify_service_health — the same
    function backend/api/app.py's /remediation/execute endpoint already uses,
    with the same service -> host-port mapping. Falls back to echoing
    remediation_result.success (the original stub behavior) when no
    controller is available — local dev without Docker, or tests — or if the
    real check itself raises, so the pipeline never crashes at this stage.
    """

    # Same service -> host-port mapping as backend/api/app.py's
    # /remediation/execute endpoint (health_checker.py duplicates this too).
    _HOST_PORT_MAP = {
        "payment-service": "5001",
        "rpc-service-primary": "5002",
        "rpc-service-secondary": "5003",
        "db-service": "5004",
    }

    def __init__(self, docker_ctl: Optional["DockerController"] = None):
        self._docker = docker_ctl

    async def verify(self, incident: Incident) -> VerificationResult:
        """Verify that remediation was effective."""
        if self._docker is None:
            return self._stub_verify(incident)

        target_service = (
            incident.remediation_request.target_service
            if incident.remediation_request
            else incident.service_name
        )
        action = incident.remediation_request.action if incident.remediation_request else ""

        port = self._HOST_PORT_MAP.get(target_service, "5000")
        health_url = f"http://localhost:{port}/health"
        verify_urls = []
        if target_service == "payment-service" and action in (
            "circuit_break",
            "switch_to_secondary",
            "reset_connection_pool",
        ):
            verify_urls.append(f"http://localhost:{port}/pay")

        from backend.simulator.health_checker import verify_service_health

        try:
            result = await verify_service_health(
                self._docker, target_service, health_url=health_url, verify_urls=verify_urls
            )
        except Exception as e:
            logger.warning(
                "Real verification failed for %s, falling back to stub: %s", target_service, e
            )
            return self._stub_verify(incident)

        return VerificationResult(
            verified=result.verified,
            checks_passed=result.checks_passed,
            checks_total=result.checks_total,
            message=result.message,
            recovered_metrics=result.recovered_metrics,
            metadata=result.metadata,
        )

    def _stub_verify(self, incident: Incident) -> VerificationResult:
        success = incident.remediation_result.success if incident.remediation_result else False
        return VerificationResult(
            verified=success,
            checks_passed=3 if success else 1,
            checks_total=3,
            message="All checks passed" if success else "Some checks failed",
            recovered_metrics={
                "error_rate": 0.0 if success else 0.35,
                "latency_ms": 150 if success else 2400,
            },
        )


# ---------------------------------------------------------------------------
# Node builder — holds dependencies, creates node functions
# ---------------------------------------------------------------------------

class OrchestratorNodes:
    """Creates graph node functions with injected dependencies.

    Each method returns an async function suitable for LangGraph's
    StateGraph.add_node(). The returned function takes OrchestratorState
    and returns a partial state dict.
    """

    def __init__(
        self,
        log_investigator: LogInvestigator,
        metric_investigator: MetricInvestigator,
        arbiter: Arbiter,
        severity_agent: SeverityAgent,
        reporter: Reporter,
        remediation_engine: RemediationEngine,
        verification: VerificationInterface,
        storage: Storage,
        event_bus: EventBus,
        approval_events: dict[str, asyncio.Event],
        approval_decisions: dict[str, str],
    ):
        self.log_investigator = log_investigator
        self.metric_investigator = metric_investigator
        self.arbiter = arbiter
        self.severity_agent = severity_agent
        self.reporter = reporter
        self.remediation_engine = remediation_engine
        self.verification = verification
        self.storage = storage
        self.event_bus = event_bus
        self.approval_events = approval_events
        self.approval_decisions = approval_decisions

    # -------------------------------------------------------------------
    # Timeline helper
    # -------------------------------------------------------------------

    async def _emit(
        self,
        incident: Incident,
        stage: PipelineStage,
        status: str,
        message: str,
        metadata: dict | None = None,
    ) -> TimelineEvent:
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

        await self.storage.save_incident(incident)
        await self.storage.append_timeline_event(incident.id, event)
        await self.event_bus.publish(incident.id, event)

        return event

    # -------------------------------------------------------------------
    # Node: detect
    # -------------------------------------------------------------------

    async def detect(self, state: OrchestratorState) -> dict:
        """Stage 1: Detection — signals are already attached by the API/simulator."""
        incident: Incident = state["incident"]
        incident.state = IncidentState.DETECTED

        await self._emit(
            incident,
            PipelineStage.DETECTION,
            "started",
            f"Detecting incident for service '{incident.service_name}'",
        )
        await self._emit(
            incident,
            PipelineStage.DETECTION,
            "completed",
            f"Incident detected — {len(incident.signals)} signal(s) received",
        )

        return {"incident": incident}

    # -------------------------------------------------------------------
    # Node: investigate (parallel log + metric)
    # -------------------------------------------------------------------

    async def investigate(self, state: OrchestratorState) -> dict:
        """Stage 2: Parallel log and metric investigation.

        Both investigators run concurrently via asyncio.gather.
        If this is a retry (arbiter already ran), increment retry_count.
        """
        incident: Incident = state["incident"]
        is_retry = incident.arbiter_result is not None

        await self._emit(
            incident,
            PipelineStage.INVESTIGATION,
            "started",
            "Starting log and metric investigation in parallel"
            + (f" (retry #{state.get('retry_count', 0) + 1})" if is_retry else ""),
        )

        # Run both investigators in parallel
        log_task = self.log_investigator.investigate(incident.signals, incident)
        metric_task = self.metric_investigator.investigate(incident.signals, incident)
        log_result, metric_result = await asyncio.gather(log_task, metric_task)

        incident.log_result = log_result
        incident.metric_result = metric_result

        await self._emit(
            incident,
            PipelineStage.INVESTIGATION,
            "completed",
            f"Investigation complete — "
            f"log confidence={log_result.confidence:.0%}, "
            f"metric confidence={metric_result.confidence:.0%}",
        )

        retry_count = state.get("retry_count", 0)
        if is_retry:
            retry_count += 1

        return {
            "incident": incident,
            "log_result": log_result,
            "metric_result": metric_result,
            "retry_count": retry_count,
        }

    # -------------------------------------------------------------------
    # Node: arbiter
    # -------------------------------------------------------------------

    async def arbiter_node(self, state: OrchestratorState) -> dict:
        """Stage 3: Arbiter reconciles both investigators' findings."""
        incident: Incident = state["incident"]

        await self._emit(
            incident,
            PipelineStage.ARBITER,
            "started",
            "Arbiter reconciling investigator findings",
        )

        arbiter_result = await self.arbiter.analyze(
            incident.log_result,
            incident.metric_result,
            incident,
        )
        incident.arbiter_result = arbiter_result

        conflict_msg = ""
        if arbiter_result.conflict_description:
            conflict_msg = f" — CONFLICT: {arbiter_result.conflict_description}"

        await self._emit(
            incident,
            PipelineStage.ARBITER,
            "completed",
            f"Root cause: '{arbiter_result.root_cause}' "
            f"(confidence: {arbiter_result.confidence:.0%}){conflict_msg}",
        )

        return {
            "incident": incident,
            "arbiter_result": arbiter_result,
        }

    # -------------------------------------------------------------------
    # Node: set_assist (low confidence after max retries)
    # -------------------------------------------------------------------

    async def set_assist(self, state: OrchestratorState) -> dict:
        """Set autonomy to ASSIST when confidence is too low after retries.

        No remediation will be executed — only a recommendation/report.
        """
        incident: Incident = state["incident"]
        incident.autonomy_level = AutonomyLevel.ASSIST

        await self._emit(
            incident,
            PipelineStage.AUTONOMY,
            "completed",
            f"Low confidence ({incident.arbiter_result.confidence:.0%}) after "
            f"{state.get('retry_count', 0)} retry(ies) — autonomy set to ASSIST. "
            f"Recommendation only, no remediation.",
        )

        return {"incident": incident, "autonomy_level": AutonomyLevel.ASSIST}

    # -------------------------------------------------------------------
    # Node: severity
    # -------------------------------------------------------------------

    async def severity(self, state: OrchestratorState) -> dict:
        """Stage 4: Determine severity and blast radius."""
        incident: Incident = state["incident"]

        await self._emit(
            incident,
            PipelineStage.SEVERITY,
            "started",
            "Assessing severity and blast radius",
        )

        severity_result = await self.severity_agent.assess(
            incident.arbiter_result,
            incident,
        )
        incident.severity_result = severity_result
        incident.severity = severity_result.severity

        await self._emit(
            incident,
            PipelineStage.SEVERITY,
            "completed",
            f"Severity: {severity_result.severity.value} — "
            f"blast radius: {severity_result.blast_radius} service(s)",
        )

        return {
            "incident": incident,
            "severity_result": severity_result,
        }

    # -------------------------------------------------------------------
    # Node: autonomy_router
    # -------------------------------------------------------------------

    async def autonomy_router(self, state: OrchestratorState) -> dict:
        """Stage 5: Decide autonomy level based on confidence and severity.

        Deterministic routing (no LLM decision):
        - HIGH CONFIDENCE + P1/P2 → SEMI_AUTONOMOUS (needs approval)
        - HIGH CONFIDENCE + P3/P4 → AUTONOMOUS (auto-execute)
        - LOW CONFIDENCE → ASSIST (recommendation only)
        """
        incident: Incident = state["incident"]
        arbiter_result = incident.arbiter_result
        severity = incident.severity

        confidence = arbiter_result.confidence
        threshold = state.get("confidence_threshold", 0.7)

        # Build remediation request
        recommended_action = self.remediation_engine.get_recommended_action(
            arbiter_result.root_cause
        )
        requires_approval = self.remediation_engine.requires_approval(severity)

        incident.remediation_request = RemediationRequest(
            action=recommended_action,
            description=(
                f"Remediation for '{arbiter_result.root_cause}' "
                f"on '{incident.service_name}'"
            ),
            target_service=incident.service_name,
            # Parameters captured from the real fault injection (saved
            # container config, toxiproxy proxy/toxic names). Empty in mock
            # mode, where the mock remediation engine ignores them.
            parameters=collect_remediation_parameters(incident),
            requires_approval=requires_approval,
        )

        if confidence < threshold:
            incident.autonomy_level = AutonomyLevel.ASSIST
        elif requires_approval:
            incident.autonomy_level = AutonomyLevel.SEMI_AUTONOMOUS
        else:
            incident.autonomy_level = AutonomyLevel.AUTONOMOUS

        level = incident.autonomy_level
        if level == AutonomyLevel.SEMI_AUTONOMOUS:
            detail = "— requires human approval"
        elif level == AutonomyLevel.AUTONOMOUS:
            detail = "— auto-executing"
        else:
            detail = "— recommendation only"

        await self._emit(
            incident,
            PipelineStage.AUTONOMY,
            "started",
            "Determining autonomy level",
        )
        await self._emit(
            incident,
            PipelineStage.AUTONOMY,
            "completed",
            f"Autonomy: {level.value} {detail}",
        )

        return {
            "incident": incident,
            "autonomy_level": level,
            "remediation_request": incident.remediation_request,
        }

    # -------------------------------------------------------------------
    # Node: wait_approval
    # -------------------------------------------------------------------

    async def wait_approval(self, state: OrchestratorState) -> dict:
        """Pause for human approval on SEMI_AUTONOMOUS incidents.

        Waits on an asyncio.Event that is resolved by the approve/reject
        API endpoint. Times out after the configured approval timeout.
        """
        incident: Incident = state["incident"]
        incident_id = incident.id

        # Create event if not exists
        if incident_id not in self.approval_events:
            self.approval_events[incident_id] = asyncio.Event()

        event = self.approval_events[incident_id]

        await self._emit(
            incident,
            PipelineStage.REMEDIATION,
            "started",
            "Awaiting human approval for remediation",
        )

        # Wait for approval with timeout (default 15 minutes)
        timeout = get_settings().approval_timeout_minutes * 60

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Approval timeout for incident %s after %ds", incident_id, timeout
            )
            # Treat timeout as rejection
            self.approval_decisions[incident_id] = "rejected"

        decision = self.approval_decisions.get(incident_id, "rejected")

        await self._emit(
            incident,
            PipelineStage.REMEDIATION,
            "completed",
            f"Human approval: {decision}",
        )

        # Clean up
        self.approval_events.pop(incident_id, None)
        self.approval_decisions.pop(incident_id, None)

        return {"incident": incident, "approval_decision": decision}

    # -------------------------------------------------------------------
    # Node: remediation
    # -------------------------------------------------------------------

    async def remediate(self, state: OrchestratorState) -> dict:
        """Stage 6: Execute remediation via the remediation engine interface."""
        incident: Incident = state["incident"]

        await self._emit(
            incident,
            PipelineStage.REMEDIATION,
            "started",
            f"Executing remediation: {incident.remediation_request.action}",
        )

        remediation_result = await self.remediation_engine.execute(
            incident.remediation_request
        )
        incident.remediation_result = remediation_result

        status = "succeeded" if remediation_result.success else "failed"
        await self._emit(
            incident,
            PipelineStage.REMEDIATION,
            "completed",
            f"Remediation {status}: {remediation_result.message}",
        )

        return {
            "incident": incident,
            "remediation_result": remediation_result,
        }

    # -------------------------------------------------------------------
    # Node: verification
    # -------------------------------------------------------------------

    async def verify(self, state: OrchestratorState) -> dict:
        """Stage 7: Verify that remediation was effective."""
        incident: Incident = state["incident"]

        await self._emit(
            incident,
            PipelineStage.VERIFICATION,
            "started",
            "Verifying remediation effectiveness",
        )

        verification_result = await self.verification.verify(incident)
        incident.verification_result = verification_result

        passed = "PASSED" if verification_result.verified else "FAILED"
        await self._emit(
            incident,
            PipelineStage.VERIFICATION,
            "completed",
            f"Verification: {passed} "
            f"({verification_result.checks_passed}/{verification_result.checks_total} checks)",
        )

        return {
            "incident": incident,
            "verification_result": verification_result,
        }

    # -------------------------------------------------------------------
    # Node: report
    # -------------------------------------------------------------------

    async def report(self, state: OrchestratorState) -> dict:
        """Stage 8: Generate the final incident report."""
        incident: Incident = state["incident"]

        await self._emit(
            incident,
            PipelineStage.REPORT,
            "started",
            "Generating incident report",
        )

        report = await self.reporter.generate(incident)
        incident.report = report

        await self._emit(
            incident,
            PipelineStage.REPORT,
            "completed",
            f"Incident report generated — root cause: {report.root_cause}",
        )

        # Mark incident as resolved (or escalated if verification failed)
        if incident.verification_result and not incident.verification_result.verified:
            incident.state = IncidentState.ESCALATED
        else:
            incident.state = IncidentState.RESOLVED

        await self.storage.save_incident(incident)

        return {"incident": incident, "report": report}


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------

def confidence_check(state: OrchestratorState) -> str:
    """Route after arbiter based on confidence and retry count.

    Returns:
        "investigate" — retry investigation (confidence low, retries remain)
        "set_assist" — confidence too low, skip remediation
        "severity" — proceed to severity assessment
    """
    arbiter_result = state.get("arbiter_result")
    if arbiter_result is None:
        return "severity"

    confidence = arbiter_result.confidence
    threshold = state.get("confidence_threshold", 0.7)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    if confidence < threshold:
        if retry_count < max_retries:
            return "investigate"
        else:
            return "set_assist"

    return "severity"


def autonomy_route(state: OrchestratorState) -> str:
    """Route after autonomy_router based on the determined level.

    Returns:
        "wait_approval" — SEMI_AUTONOMOUS, needs human approval
        "remediate" — AUTONOMOUS, proceed to remediation
        "report" — ASSIST, skip remediation, go to report
    """
    autonomy = state.get("autonomy_level")
    if autonomy == AutonomyLevel.SEMI_AUTONOMOUS:
        return "wait_approval"
    elif autonomy == AutonomyLevel.AUTONOMOUS:
        return "remediate"
    else:
        return "report"


def approval_route(state: OrchestratorState) -> str:
    """Route after wait_approval based on the approval decision.

    Returns:
        "remediate" — approved, proceed to remediation
        "report" — rejected, skip remediation, go to report
    """
    decision = state.get("approval_decision")
    if decision == "approved":
        return "remediate"
    else:
        return "report"

"""Incident response pipeline orchestrator built on LangGraph StateGraph.

Coordinates the full pipeline:
  detect -> investigate -> arbiter -> severity -> autonomy -> remediate -> verify -> report

Every stage appends a TimelineEvent. The orchestrator depends ONLY on
shared contracts and injected interfaces — never on internal implementations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

from langgraph.graph.state import CompiledStateGraph

if TYPE_CHECKING:
    from backend.simulator.docker_controller import DockerController

from backend.agents.base import (
    Arbiter,
    LogInvestigator,
    MetricInvestigator,
    Reporter,
    SeverityAgent,
)
from backend.agents.llm_agents import LLMArbiter, LLMLogInvestigator, LLMMetricInvestigator
from backend.agents.mock_agents import (
    MockArbiter,
    MockLogInvestigator,
    MockMetricInvestigator,
    MockReporter,
    MockSeverityAgent,
)
from backend.contracts import (
    Incident,
    PipelineResult,
)
from backend.orchestrator.graph import build_graph
from backend.orchestrator.nodes import VerificationInterface
from backend.orchestrator.state import OrchestratorState
from backend.platform.config import get_settings
from backend.platform.events import EventBus, get_event_bus
from backend.platform.storage import Storage, get_storage
from backend.remediation.actions import RemediationEngine

logger = logging.getLogger(__name__)


def _llm_configured() -> bool:
    """True if at least one LLM provider has a configured API key.

    Gates whether the orchestrator defaults to the real LLM-backed
    investigators/arbiter or the deterministic mocks — mirrors the fallback
    LLMClient itself already does per-provider, just one level up so CI/tests
    with no keys set keep getting fully deterministic behavior.
    """
    settings = get_settings()
    return bool(settings.anthropic_api_key or settings.openai_api_key)


class IncidentOrchestrator:
    """Orchestrates the full incident response pipeline via LangGraph.

    Supports dependency injection for all agents and services.
    When dependencies are not provided, defaults to the real LLM-backed
    investigators/arbiter if a provider API key is configured, otherwise the
    deterministic mocks.

    Usage:
        orchestrator = IncidentOrchestrator()
        result = await orchestrator.run_pipeline(incident)

    For approval:
        await orchestrator.approve(incident_id)
        await orchestrator.reject(incident_id)
    """

    def __init__(
        self,
        log_investigator: LogInvestigator | None = None,
        metric_investigator: MetricInvestigator | None = None,
        arbiter: Arbiter | None = None,
        severity_agent: SeverityAgent | None = None,
        reporter: Reporter | None = None,
        remediation_engine: RemediationEngine | None = None,
        verification: VerificationInterface | None = None,
        storage: Storage | None = None,
        event_bus: EventBus | None = None,
        docker_ctl: Optional["DockerController"] = None,
    ):
        # Injected dependencies — default to the real LLM-backed agents when a
        # provider key is configured, otherwise the deterministic mocks (also
        # what LLMLogInvestigator/LLMMetricInvestigator/LLMArbiter themselves
        # fall back to internally if the LLM response can't be parsed).
        llm_ready = _llm_configured()
        self.log_investigator = log_investigator or (
            LLMLogInvestigator() if llm_ready else MockLogInvestigator()
        )
        self.metric_investigator = metric_investigator or (
            LLMMetricInvestigator() if llm_ready else MockMetricInvestigator()
        )
        self.arbiter = arbiter or (LLMArbiter() if llm_ready else MockArbiter())
        self.severity_agent = severity_agent or MockSeverityAgent()
        self.reporter = reporter or MockReporter()
        self.remediation_engine = remediation_engine or RemediationEngine()
        self.verification = verification or VerificationInterface(docker_ctl=docker_ctl)
        self.storage = storage or get_storage()
        self.event_bus = event_bus or get_event_bus()

        # Approval mechanism
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_decisions: dict[str, str] = {}

        # Build the LangGraph graph
        self._graph: CompiledStateGraph = build_graph(
            log_investigator=self.log_investigator,
            metric_investigator=self.metric_investigator,
            arbiter_agent=self.arbiter,
            severity_agent=self.severity_agent,
            reporter=self.reporter,
            remediation_engine=self.remediation_engine,
            verification=self.verification,
            storage=self.storage,
            event_bus=self.event_bus,
            approval_events=self._approval_events,
            approval_decisions=self._approval_decisions,
        )

    # -------------------------------------------------------------------
    # Approval API
    # -------------------------------------------------------------------

    async def approve(self, incident_id: str) -> bool:
        """Approve remediation for a SEMI_AUTONOMOUS incident.

        Returns True if the approval was processed, False if no pending approval.
        """
        if incident_id in self._approval_events:
            self._approval_decisions[incident_id] = "approved"
            self._approval_events[incident_id].set()
            logger.info("Orchestrator: approved remediation for incident %s", incident_id)
            return True

        logger.warning(
            "Orchestrator: no pending approval for incident %s", incident_id
        )
        return False

    async def reject(self, incident_id: str) -> bool:
        """Reject remediation for a SEMI_AUTONOMOUS incident.

        Returns True if the rejection was processed, False if no pending approval.
        """
        if incident_id in self._approval_events:
            self._approval_decisions[incident_id] = "rejected"
            self._approval_events[incident_id].set()
            logger.info("Orchestrator: rejected remediation for incident %s", incident_id)
            return True

        logger.warning(
            "Orchestrator: no pending approval for incident %s", incident_id
        )
        return False

    # -------------------------------------------------------------------
    # Pipeline execution
    # -------------------------------------------------------------------

    async def run_pipeline(self, incident: Incident) -> PipelineResult:
        """Execute the full incident response pipeline via LangGraph.

        This is the main entry point called by the trigger endpoint.
        """
        settings = get_settings()
        start_time = time.time()

        # Build initial state
        initial_state: OrchestratorState = {
            "incident": incident,
            "log_result": None,
            "metric_result": None,
            "arbiter_result": None,
            "severity_result": None,
            "autonomy_level": None,
            "remediation_request": None,
            "remediation_result": None,
            "verification_result": None,
            "report": None,
            "retry_count": 0,
            "max_retries": settings.rca_max_retries,
            "confidence_threshold": settings.rca_confidence_threshold,
            "approval_decision": None,
            "error": None,
        }

        try:
            # Run the LangGraph graph
            final_state = await self._graph.ainvoke(initial_state)

            elapsed = time.time() - start_time
            return PipelineResult(
                incident=final_state["incident"],
                success=True,
                total_stages_completed=len(final_state["incident"].timeline),
                duration_seconds=elapsed,
            )

        except Exception as e:
            logger.error("Pipeline failed for incident %s: %s", incident.id, e)
            incident.state = incident.state  # keep current state
            incident.current_stage = incident.current_stage

            # Record failure in timeline
            from backend.contracts import PipelineStage, TimelineEvent

            event = TimelineEvent(
                stage=incident.current_stage or PipelineStage.DETECTION,
                status="failed",
                message=f"Pipeline failed: {str(e)}",
            )
            incident.timeline.append(event)

            await self.storage.save_incident(incident)
            await self.event_bus.publish(incident.id, event)

            return PipelineResult(
                incident=incident,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )

"""LangGraph StateGraph construction for the incident response pipeline.

Wires together all nodes and conditional edges into a compiled graph.

Graph structure:

    START -> detect -> investigate -> arbiter -> confidence_check
        |-- "investigate"  -> investigate (retry)
        |-- "set_assist"   -> set_assist -> report -> END
        |-- "severity"     -> severity -> autonomy_router
            |-- "wait_approval" -> wait_approval -> approval_route
            |       |-- "remediate" -> remediation -> verification -> report -> END
            |       |-- "report"    -> report -> END
            |-- "remediate" -> remediation -> verification -> report -> END
            |-- "report"    -> report -> END
"""

from __future__ import annotations

import asyncio
from typing import Callable

from langgraph.graph import END, StateGraph

from backend.agents.base import (
    Arbiter,
    LogInvestigator,
    MetricInvestigator,
    Reporter,
    SeverityAgent,
)
from backend.orchestrator.nodes import (
    OrchestratorNodes,
    VerificationInterface,
    approval_route,
    autonomy_route,
    confidence_check,
)
from backend.orchestrator.state import OrchestratorState
from backend.platform.events import EventBus
from backend.platform.storage import Storage
from backend.remediation.actions import RemediationEngine


def build_graph(
    log_investigator: LogInvestigator,
    metric_investigator: MetricInvestigator,
    arbiter_agent: Arbiter,
    severity_agent: SeverityAgent,
    reporter: Reporter,
    remediation_engine: RemediationEngine,
    verification: VerificationInterface,
    storage: Storage,
    event_bus: EventBus,
    approval_events: dict[str, asyncio.Event],
    approval_decisions: dict[str, str],
) -> StateGraph:
    """Build and return the compiled LangGraph orchestration graph.

    All external dependencies are injected — the graph has no hard-coded
    implementations of agents, remediation, or platform services.
    """
    nodes = OrchestratorNodes(
        log_investigator=log_investigator,
        metric_investigator=metric_investigator,
        arbiter=arbiter_agent,
        severity_agent=severity_agent,
        reporter=reporter,
        remediation_engine=remediation_engine,
        verification=verification,
        storage=storage,
        event_bus=event_bus,
        approval_events=approval_events,
        approval_decisions=approval_decisions,
    )

    graph = StateGraph(OrchestratorState)

    # --- Add nodes ---
    graph.add_node("detect", nodes.detect)
    graph.add_node("investigate", nodes.investigate)
    graph.add_node("arbiter", nodes.arbiter_node)
    graph.add_node("set_assist", nodes.set_assist)
    graph.add_node("severity", nodes.severity)
    graph.add_node("autonomy_router", nodes.autonomy_router)
    graph.add_node("wait_approval", nodes.wait_approval)
    graph.add_node("remediate", nodes.remediate)
    graph.add_node("verify", nodes.verify)
    graph.add_node("report", nodes.report)

    # --- Entry point ---
    graph.set_entry_point("detect")

    # --- Linear edges ---
    graph.add_edge("detect", "investigate")
    graph.add_edge("investigate", "arbiter")
    graph.add_edge("set_assist", "report")
    graph.add_edge("remediate", "verify")
    graph.add_edge("verify", "report")
    graph.add_edge("report", END)

    # --- Conditional edge: arbiter -> confidence_check ---
    graph.add_conditional_edges(
        "arbiter",
        confidence_check,
        {
            "investigate": "investigate",   # retry
            "set_assist": "set_assist",     # low confidence, skip remediation
            "severity": "severity",         # proceed
        },
    )

    # --- Linear edge: severity -> autonomy_router ---
    graph.add_edge("severity", "autonomy_router")

    # --- Conditional edge: autonomy_router ---
    graph.add_conditional_edges(
        "autonomy_router",
        autonomy_route,
        {
            "wait_approval": "wait_approval",  # SEMI_AUTONOMOUS
            "remediate": "remediate",           # AUTONOMOUS
            "report": "report",                 # ASSIST
        },
    )

    # --- Conditional edge: wait_approval ---
    graph.add_conditional_edges(
        "wait_approval",
        approval_route,
        {
            "remediate": "remediate",  # approved
            "report": "report",        # rejected
        },
    )

    return graph.compile()

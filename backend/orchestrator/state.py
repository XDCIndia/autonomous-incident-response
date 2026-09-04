"""LangGraph state for the incident response orchestration pipeline.

The state carries all data through the graph. Each node reads from
and writes to this shared state.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from backend.contracts import (
    ArbiterResult,
    AutonomyLevel,
    Incident,
    IncidentReport,
    LogInvestigationResult,
    MetricInvestigationResult,
    RemediationRequest,
    RemediationResult,
    SeverityResult,
    VerificationResult,
)


class OrchestratorState(TypedDict, total=False):
    """State carried through the LangGraph orchestration graph.

    All fields are optional to allow partial updates from nodes.
    """

    # Core incident being processed
    incident: Incident

    # Investigation results
    log_result: Optional[LogInvestigationResult]
    metric_result: Optional[MetricInvestigationResult]

    # Arbiter result
    arbiter_result: Optional[ArbiterResult]

    # Severity result
    severity_result: Optional[SeverityResult]

    # Autonomy decision
    autonomy_level: Optional[AutonomyLevel]

    # Remediation
    remediation_request: Optional[RemediationRequest]
    remediation_result: Optional[RemediationResult]

    # Verification
    verification_result: Optional[VerificationResult]

    # Final report
    report: Optional[IncidentReport]

    # Retry / confidence gating
    retry_count: int
    max_retries: int
    confidence_threshold: float

    # Approval (for SEMI_AUTONOMOUS)
    approval_decision: Optional[str]  # "approved" | "rejected" | None

    # Error tracking
    error: Optional[str]

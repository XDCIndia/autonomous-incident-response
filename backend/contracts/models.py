"""Shared Pydantic contracts for the incident response pipeline.

These models are the ONLY integration boundary between developers.
Do NOT import internal implementation details from other modules.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SeverityLevel(str, Enum):
    """Incident severity levels."""
    P1 = "P1"  # Critical — complete outage
    P2 = "P2"  # High — major degradation
    P3 = "P3"  # Medium — partial degradation
    P4 = "P4"  # Low — warning / informational


class AutonomyLevel(str, Enum):
    """How much autonomy the system has for remediation."""
    ASSIST = "assist"               # Low confidence — recommend only
    SEMI_AUTONOMOUS = "semi"        # High confidence + high severity — needs approval
    AUTONOMOUS = "autonomous"       # High confidence + low severity — auto-execute


class IncidentState(str, Enum):
    """Lifecycle states of an incident."""
    CREATED = "created"
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    ANALYZING = "analyzing"         # Arbiter stage
    SEVERITY_DETERMINED = "severity_determined"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    REJECTED = "rejected"      # Human rejected remediation — not recovered
    FAILED = "failed"


class PipelineStage(str, Enum):
    """Stages of the processing pipeline."""
    DETECTION = "detection"
    INVESTIGATION = "investigation"
    ARBITER = "arbiter"
    SEVERITY = "severity"
    AUTONOMY = "autonomy"
    REMEDIATION = "remediation"
    VERIFICATION = "verification"
    REPORT = "report"


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimelineEvent(BaseModel):
    """A single event in the incident timeline.

    Every pipeline stage MUST append a TimelineEvent.
    """
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    stage: PipelineStage
    status: str = "completed"  # "started" | "completed" | "failed"
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    """Raw telemetry signal from the simulator or monitoring system."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    source: str  # e.g. "payment-service", "order-service"
    event_type: str  # e.g. "error_rate", "latency", "deploy", "log_error"
    value: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Incident(BaseModel):
    """The central incident record.

    Created when an alert fires; updated as the pipeline progresses.
    """
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # Target
    target_url: Optional[str] = None  # website/API/service URL
    service_name: str = "unknown"
    service_url: Optional[str] = None

    # Where this incident came from — "simulator" (a scenario injected via
    # /incidents/trigger or /faults/inject) or "url_monitor" (a real,
    # user-registered MonitoredTarget genuinely failing its health checks).
    # Downstream stages use this to decide what's safe to do automatically:
    # a simulator incident targets our own docker-managed services, so real
    # remediation/verification make sense; a url_monitor incident targets an
    # arbitrary external URL we have no control-plane integration for, so
    # remediation must stay recommendation-only (see VerificationInterface /
    # OrchestratorNodes.remediate in backend/orchestrator/nodes.py).
    source: str = "simulator"

    # State
    state: IncidentState = IncidentState.CREATED
    severity: Optional[SeverityLevel] = None
    autonomy_level: Optional[AutonomyLevel] = None
    current_stage: Optional[PipelineStage] = None

    # Signals that triggered this incident
    signals: list[TelemetryEvent] = Field(default_factory=list)

    # Results from each pipeline stage
    log_result: Optional[LogInvestigationResult] = None
    metric_result: Optional[MetricInvestigationResult] = None
    arbiter_result: Optional[ArbiterResult] = None
    severity_result: Optional[SeverityResult] = None
    remediation_request: Optional[RemediationRequest] = None
    remediation_result: Optional[RemediationResult] = None
    verification_result: Optional[VerificationResult] = None
    report: Optional[IncidentReport] = None

    # Timeline
    timeline: list[TimelineEvent] = Field(default_factory=list)


class LogInvestigationResult(BaseModel):
    """Result from the Log Investigator agent."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    suggested_root_cause: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricInvestigationResult(BaseModel):
    """Result from the Metric Investigator agent."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    suggested_root_cause: str = ""
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArbiterResult(BaseModel):
    """Result from the Arbiter — merges both investigators' findings."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    merged_hypothesis: str = ""
    root_cause: str = ""
    confidence: float = 0.0
    log_hypothesis_agrees: bool = True
    metric_hypothesis_agrees: bool = True
    conflict_description: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    contributing_factors: list[str] = Field(default_factory=list)
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SeverityResult(BaseModel):
    """Result from the Severity agent."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    severity: SeverityLevel = SeverityLevel.P3
    blast_radius: int = 1  # number of services affected
    affected_services: list[str] = Field(default_factory=list)
    justification: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationRequest(BaseModel):
    """Request to remediate — what action to take."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    action: str = ""  # from allowed actions list
    description: str = ""
    target_service: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationResult(BaseModel):
    """Result of executing a remediation action."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    action: str = ""
    success: bool = False
    message: str = ""
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    """Result of verifying that remediation worked."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    verified: bool = False
    checks_passed: int = 0
    checks_total: int = 0
    message: str = ""
    recovered_metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentReport(BaseModel):
    """Final incident report — the output of the Report stage."""
    id: str = Field(default_factory=_uuid)
    timestamp: datetime = Field(default_factory=_now)
    incident_id: str = ""
    service: str = ""
    severity: SeverityLevel = SeverityLevel.P3
    duration_seconds: float = 0.0
    root_cause: str = ""
    impact: str = ""
    remediation_action: str = ""
    result_metrics: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    prevention: str = ""
    timeline_summary: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    """Summary result returned after the full pipeline executes."""
    incident: Incident
    success: bool = True
    error: Optional[str] = None
    total_stages_completed: int = 0
    duration_seconds: float = 0.0


class MonitoredTarget(BaseModel):
    """A user-registered application URL the system actively health-checks.

    When monitoring detects a genuine, sustained failure (see
    backend/monitoring/url_monitor.py's deterministic consecutive-failure
    policy), it creates a real Incident with source="url_monitor" and runs
    it through the same orchestrator every simulator scenario uses.
    """
    id: str = Field(default_factory=_uuid)
    name: str
    url: str
    monitoring_enabled: bool = True
    health_status: str = "unknown"  # "unknown" | "healthy" | "unhealthy"
    consecutive_failures: int = 0
    last_checked_at: Optional[datetime] = None
    last_status_code: Optional[int] = None
    last_latency_ms: Optional[float] = None
    last_error: Optional[str] = None
    active_incident_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

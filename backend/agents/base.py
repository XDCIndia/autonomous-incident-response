"""Base agent interfaces for the incident response pipeline.

Each agent receives inputs via shared contracts and returns results via shared contracts.
The orchestrator depends ONLY on these interfaces.

Person 1 (AI/Agents) implements the real logic here.
For the foundation, mock implementations are provided.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from backend.contracts import (
    ArbiterResult,
    Incident,
    LogInvestigationResult,
    MetricInvestigationResult,
    SeverityLevel,
    SeverityResult,
    TelemetryEvent,
)


class BaseAgent(ABC):
    """Base class for all pipeline agents."""

    @property
    def name(self) -> str:
        return self.__class__.__name__


class LogInvestigator(BaseAgent):
    """Investigates log-based evidence for root cause analysis.

    Input: list of TelemetryEvent signals
    Output: LogInvestigationResult with hypothesis, evidence, confidence
    """

    @abstractmethod
    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> LogInvestigationResult:
        ...


class MetricInvestigator(BaseAgent):
    """Investigates metric-based evidence for root cause analysis.

    Input: list of TelemetryEvent signals
    Output: MetricInvestigationResult with hypothesis, evidence, confidence
    """

    @abstractmethod
    async def investigate(
        self,
        signals: list[TelemetryEvent],
        incident: Incident,
        extra_context: Optional[str] = None,
    ) -> MetricInvestigationResult:
        ...


class Arbiter(BaseAgent):
    """Reconciles findings from both investigators.

    Input: LogInvestigationResult + MetricInvestigationResult
    Output: ArbiterResult with merged hypothesis, confidence, conflict info
    """

    @abstractmethod
    async def analyze(
        self,
        log_result: LogInvestigationResult,
        metric_result: MetricInvestigationResult,
        incident: Incident,
    ) -> ArbiterResult:
        ...


class SeverityAgent(BaseAgent):
    """Determines incident severity and blast radius.

    Input: ArbiterResult + incident signals
    Output: SeverityResult with severity level, affected services
    """

    @abstractmethod
    async def assess(
        self,
        arbiter_result: ArbiterResult,
        incident: Incident,
    ) -> SeverityResult:
        ...


class Reporter(BaseAgent):
    """Generates the final incident report.

    Input: Fully populated Incident
    Output: IncidentReport
    """

    @abstractmethod
    async def generate(self, incident: Incident):
        from backend.contracts import IncidentReport

        # Default implementation — returns a minimal report
        # Real implementation (Person 1) will use LLM to generate rich reports
        return IncidentReport(
            incident_id=incident.id,
            service=incident.service_name,
            severity=incident.severity or SeverityLevel.P3,
            root_cause=incident.arbiter_result.root_cause if incident.arbiter_result else "",
            impact=f"Service {incident.service_name} affected",
            confidence=incident.arbiter_result.confidence if incident.arbiter_result else 0.0,
            timeline_summary=[e.message for e in incident.timeline if e.message],
        )

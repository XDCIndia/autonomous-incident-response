"""Agent base classes and interfaces.

Each agent processes inputs via shared contracts.
The orchestrator depends ONLY on these interfaces, not internal implementations.
"""

from backend.agents.base import (
    BaseAgent,
    LogInvestigator,
    MetricInvestigator,
    Arbiter,
    SeverityAgent,
    Reporter,
)

__all__ = [
    "BaseAgent",
    "LogInvestigator",
    "MetricInvestigator",
    "Arbiter",
    "SeverityAgent",
    "Reporter",
]

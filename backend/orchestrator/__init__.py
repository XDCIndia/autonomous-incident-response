"""Orchestrator module — manages the incident response pipeline via LangGraph.

The orchestrator coordinates the full flow:
  detect -> investigate -> arbiter -> severity -> autonomy -> remediate -> verify -> report

It depends ONLY on shared contracts and injected interfaces.
"""

from backend.orchestrator.pipeline import IncidentOrchestrator
from backend.orchestrator.nodes import VerificationInterface

# Module-level singleton for API integration
_orchestrator: IncidentOrchestrator | None = None


def get_orchestrator() -> IncidentOrchestrator:
    """Get or create the singleton orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = IncidentOrchestrator()
    return _orchestrator


__all__ = ["IncidentOrchestrator", "VerificationInterface", "get_orchestrator"]

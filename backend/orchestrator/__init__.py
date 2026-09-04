"""Orchestrator module — manages the incident response pipeline via LangGraph.

The orchestrator coordinates the full flow:
  detect -> investigate -> arbiter -> severity -> autonomy -> remediate -> verify -> report

It depends ONLY on shared contracts and injected interfaces.
"""

from typing import TYPE_CHECKING, Optional

from backend.orchestrator.pipeline import IncidentOrchestrator
from backend.orchestrator.nodes import VerificationInterface
from backend.platform.events import EventBus
from backend.platform.storage import Storage
from backend.remediation.actions import RemediationEngine

if TYPE_CHECKING:
    from backend.simulator.docker_controller import DockerController

# Module-level singleton for API integration
_orchestrator: IncidentOrchestrator | None = None


def get_orchestrator(docker_ctl: Optional["DockerController"] = None) -> IncidentOrchestrator:
    """Get or create the singleton orchestrator instance.

    `docker_ctl`, when given, is only used the first time the singleton is
    constructed (so the verification stage can perform real health checks
    instead of its no-Docker stub) — it has no effect on later calls once the
    singleton already exists.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = IncidentOrchestrator(docker_ctl=docker_ctl)
    return _orchestrator


def configure_orchestrator(
    remediation_engine: Optional[RemediationEngine] = None,
    verification: Optional[VerificationInterface] = None,
    storage: Optional[Storage] = None,
    event_bus: Optional[EventBus] = None,
) -> IncidentOrchestrator:
    """Replace the singleton with an orchestrator wired to real dependencies.

    Used by the API layer when the real Docker/Toxiproxy environment is
    available, so that incidents triggered via ``/incidents/trigger`` run
    real remediation and real health verification.  Approvals keep working
    because the approve/reject endpoints use the same module singleton.

    Returns the newly created orchestrator.
    """
    global _orchestrator
    _orchestrator = IncidentOrchestrator(
        remediation_engine=remediation_engine or RemediationEngine(),
        verification=verification or VerificationInterface(),
        storage=storage,
        event_bus=event_bus,
    )
    return _orchestrator


__all__ = [
    "IncidentOrchestrator",
    "VerificationInterface",
    "get_orchestrator",
    "configure_orchestrator",
]

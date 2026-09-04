"""Simulator module — generates deterministic fault signals.

Supports both mock mode (telemetry signals only) and real Docker
fault injection (actual container replacement).
"""

from backend.simulator.scenarios import (
    FaultInjectionResult,
    inject_bad_deployment,
    inject_dependency_outage,
    inject_database_failure,
    inject_resource_exhaustion,
)
from backend.simulator.service_graph import ServiceGraph, get_default_graph

__all__ = [
    "FaultInjectionResult",
    "inject_bad_deployment",
    "inject_dependency_outage",
    "inject_database_failure",
    "inject_resource_exhaustion",
    "ServiceGraph",
    "get_default_graph",
]

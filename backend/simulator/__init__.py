"""Simulator module — generates deterministic fault signals.

Person 3 implements real fault scenarios here.
For the foundation, one mock scenario is fully implemented.
"""

from backend.simulator.scenarios import (
    inject_bad_deployment,
    inject_dependency_outage,
    inject_database_failure,
    inject_resource_exhaustion,
)
from backend.simulator.service_graph import ServiceGraph, get_default_graph

__all__ = [
    "inject_bad_deployment",
    "inject_dependency_outage",
    "inject_database_failure",
    "inject_resource_exhaustion",
    "ServiceGraph",
    "get_default_graph",
]

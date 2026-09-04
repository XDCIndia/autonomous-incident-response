"""Service dependency graph for blast radius calculation.

Person 3 implements the full dependency graph here.
For the foundation, a simple default graph is provided.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceNode:
    """A single service in the dependency graph."""
    name: str
    url: str = ""
    dependencies: list[str] = field(default_factory=list)  # upstream services it calls
    health: str = "healthy"  # "healthy" | "degraded" | "down"

    @property
    def is_down(self) -> bool:
        return self.health == "down"


@dataclass
class ServiceGraph:
    """Simple service dependency graph."""
    services: dict[str, ServiceNode] = field(default_factory=dict)

    def add_service(self, name: str, url: str = "", dependencies: list[str] | None = None):
        self.services[name] = ServiceNode(
            name=name,
            url=url,
            dependencies=dependencies or [],
        )

    def set_health(self, name: str, health: str):
        if name in self.services:
            self.services[name].health = health

    def get_affected_services(self, source: str) -> list[str]:
        """Find all downstream services affected by a failure in `source`."""
        affected: list[str] = [source]
        # Simple BFS — services that depend on the failed service
        for svc_name, svc in self.services.items():
            if source in svc.dependencies:
                affected.append(svc_name)
        return list(set(affected))

    def get_all_services(self) -> list[str]:
        return list(self.services.keys())


def get_default_graph() -> ServiceGraph:
    """Default service topology for the foundation.

    gateway → order-service → payment-service → inventory-service
                          ↘ shipping-service
    """
    graph = ServiceGraph()
    graph.add_service("gateway", "https://api.example.com")
    graph.add_service("order-service", "https://order.example.com", ["gateway"])
    graph.add_service("payment-service", "https://payment.example.com", ["order-service"])
    graph.add_service("shipping-service", "https://shipping.example.com", ["order-service"])
    graph.add_service("inventory-service", "https://inventory.example.com", ["payment-service"])
    return graph

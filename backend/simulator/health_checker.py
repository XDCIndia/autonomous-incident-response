"""Health checker for IRAS-managed services.

Provides multi-layer health verification:
  1. Container running status (Docker)
  2. Docker HEALTHCHECK status (Docker)
  3. HTTP /health endpoint (network)
  4. Extra verify URLs (optional)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.simulator.docker_controller import DockerController

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of a health verification check."""
    verified: bool = False
    checks_passed: int = 0
    checks_total: int = 0
    message: str = ""
    recovered_metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return asdict(self)


async def check_container_health(
    docker_ctl: DockerController,
    service_name: str,
    verify_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Multi-layer health check for a service.

    Returns a dict with:
      - running: bool
      - health: str (from Docker HEALTHCHECK)
      - http_health: dict mapping URL -> status code (or error string)
      - overall: "healthy" | "degraded" | "unhealthy"
    """
    result: dict[str, Any] = {
        "service": service_name,
        "running": False,
        "health": "not_found",
        "http_health": {},
        "overall": "unhealthy",
    }

    # Layer 1 & 2: Docker container status + HEALTHCHECK
    # DockerController.check_health is now async-safe
    status = await docker_ctl.check_health(service_name)
    result["running"] = status.get("running", False)
    result["health"] = status.get("health", "not_found")
    result["version"] = status.get("version", "")
    result["container_id"] = status.get("container_id", "")
    result["image"] = status.get("image", "")

    if not result["running"]:
        result["overall"] = "unhealthy"
        return result

    # Layer 3: HTTP health endpoint
    # The payment-service is exposed on host port 5001
    port = _get_host_port(service_name)
    if port:
        url = f"http://localhost:{port}/health"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                result["http_health"][url] = resp.status_code
        except Exception as exc:
            result["http_health"][url] = f"error: {exc}"

    # Layer 4: Extra verify URLs (sync httpx — offload to thread)
    if verify_urls:
        async def _check_url(url: str) -> None:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(url)
                    result["http_health"][url] = resp.status_code
            except Exception as exc:
                result["http_health"][url] = f"error: {exc}"

        await asyncio.gather(*[_check_url(u) for u in verify_urls])

    # Determine overall status
    if result["health"] == "healthy":
        http_ok = all(
            v == 200 for v in result["http_health"].values()
            if isinstance(v, int)
        )
        result["overall"] = "healthy" if http_ok else "degraded"
    elif result["health"] == "starting":
        result["overall"] = "degraded"
    else:
        result["overall"] = "unhealthy"

    return result


async def verify_service_health(
    docker_ctl: DockerController,
    service_name: str,
    health_url: str = "",
    verify_urls: list[str] | None = None,
) -> VerificationResult:
    """Verify service health after remediation.

    Checks container health and optional HTTP endpoints, returning
    a VerificationResult compatible with the API response format.
    """
    checks_passed = 0
    checks_total = 0
    recovered_metrics: dict[str, Any] = {}
    messages: list[str] = []

    # Check 1: Docker container health
    checks_total += 1
    status = await docker_ctl.check_health(service_name)
    if status.get("health") == "healthy" or status.get("running"):
        checks_passed += 1
        recovered_metrics["docker_health"] = status.get("health", "unknown")
    else:
        messages.append(f"Container health: {status.get('health', 'not_found')}")

    # Check 2: HTTP health endpoint
    all_verify = []
    if health_url:
        all_verify.append(health_url)
    if verify_urls:
        all_verify.extend(verify_urls)

    for url in all_verify:
        checks_total += 1
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                recovered_metrics[f"verify_{url}"] = resp.status_code
                if resp.status_code == 200:
                    checks_passed += 1
                else:
                    messages.append(f"{url} returned {resp.status_code}")
        except Exception as exc:
            recovered_metrics[f"verify_{url}"] = f"error: {exc}"
            messages.append(f"{url}: {exc}")

    verified = checks_passed == checks_total and checks_total > 0
    message = "All checks passed" if verified else "; ".join(messages) if messages else "No checks performed"

    return VerificationResult(
        verified=verified,
        checks_passed=checks_passed,
        checks_total=checks_total,
        message=message,
        recovered_metrics=recovered_metrics,
    )


def _get_host_port(service_name: str) -> str | None:
    """Map Docker service name to its host-mapped port."""
    port_map = {
        "payment-service": "5001",
        "rpc-service-primary": "5002",
        "rpc-service-secondary": "5003",
        "db-service": "5004",
    }
    return port_map.get(service_name)

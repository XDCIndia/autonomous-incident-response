"""Health verification for services after remediation.

Uses the DockerController to check container state and the service
health endpoint to confirm full recovery.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.contracts import VerificationResult
from backend.simulator.docker_controller import DockerController

logger = logging.getLogger(__name__)


async def verify_service_health(
    docker_ctl: DockerController,
    service_name: str,
    *,
    health_url: str | None = None,
    retries: int = 15,
    delay: float = 2.0,
) -> VerificationResult:
    """Verify that a service has recovered after remediation.

    Checks:
        1. Container is running
        2. Docker HEALTHCHECK reports "healthy"
        3. HTTP health endpoint returns 200 (if reachable)

    Returns the existing ``VerificationResult`` contract.
    """
    checks_total = 3
    checks_passed = 0
    recovered_metrics: dict[str, Any] = {}
    messages: list[str] = []

    # --- Check 1: container is running ---
    container_status = docker_ctl.check_health(service_name)
    is_running = container_status.get("running", False)
    if is_running:
        checks_passed += 1
        messages.append(f"Container is running (id={container_status.get('container_id', '?')[:12]})")
    else:
        messages.append("Container is NOT running")
    recovered_metrics["container_running"] = is_running

    # --- Check 2: Docker HEALTHCHECK ---
    docker_health = container_status.get("health", "unknown")
    if docker_health != "healthy":
        # Wait for health to converge
        logger.info("Waiting for Docker HEALTHCHECK on %s …", service_name)
        healthy = docker_ctl.wait_for_health(
            service_name, target_health="healthy", retries=retries, delay=delay,
        )
        if healthy:
            docker_health = "healthy"
    if docker_health == "healthy":
        checks_passed += 1
        messages.append("Docker HEALTHCHECK: healthy")
    else:
        messages.append(f"Docker HEALTHCHECK: {docker_health}")
    recovered_metrics["docker_health"] = docker_health

    # --- Check 3: HTTP health endpoint ---
    if health_url and is_running:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    checks_passed += 1
                    body = resp.json()
                    messages.append(f"HTTP health: 200 — {body.get('status', 'ok')}")
                    recovered_metrics["http_status"] = resp.status_code
                    recovered_metrics["http_body"] = body
                else:
                    messages.append(f"HTTP health: {resp.status_code}")
                    recovered_metrics["http_status"] = resp.status_code
        except Exception as exc:
            messages.append(f"HTTP health check failed: {exc}")
            recovered_metrics["http_error"] = str(exc)
    elif not health_url:
        # No URL provided — skip HTTP check, adjust total
        checks_total = 2
        messages.append("HTTP health check skipped (no URL configured)")

    verified = checks_passed == checks_total

    return VerificationResult(
        verified=verified,
        checks_passed=checks_passed,
        checks_total=checks_total,
        message=" | ".join(messages),
        recovered_metrics=recovered_metrics,
    )

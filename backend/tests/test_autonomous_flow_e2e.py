"""End-to-end proof that /incidents/trigger drives the REAL environment.

Verifies the fix for issue #14:
  A. bad_deployment  → the payment-service container is actually replaced
     (real fault), and the pipeline's rollback restores it.
  B. dependency_outage → a real Toxiproxy toxic is injected and the
     pipeline's circuit_break remediation disables the proxy.
  C/D/E. Investigation runs on telemetry of the real fault; remediation and
     verification operate on the real environment (verified=True only after
     real health checks).

Requires the docker-compose stack (services only) to be running and the
backend to be reachable at API_URL (default http://localhost:8000) — same
setup as test_bad_deployment_e2e.py / test_database_failure_e2e.py.

Scenarios are SEMI_AUTONOMOUS (P1/P2), so the tests approve them via the API.
"""

import os
import time

import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000")
TOXIPROXY_API = os.environ.get("TOXIPROXY_API", "http://localhost:8474")


def _payment_health(client: httpx.Client) -> dict:
    resp = client.get(f"{API_URL}/services/health?service=payment-service", timeout=5.0)
    assert resp.status_code == 200, f"backend health check failed: {resp.text}"
    return resp.json()


def _env_ready(client: httpx.Client) -> bool:
    """payment-service container running + backend reachable."""
    try:
        data = _payment_health(client)
        return bool(data.get("running"))
    except Exception:
        return False


def _wait_for_pending_approval(client: httpx.Client, incident_id: str, deadline: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < deadline:
        resp = client.get(f"{API_URL}/incidents/{incident_id}/approval", timeout=5.0)
        if resp.status_code == 200 and resp.json().get("has_pending_approval"):
            return True
        time.sleep(0.5)
    return False


def _wait_for_terminal_state(client: httpx.Client, incident_id: str, deadline: float = 180.0) -> dict:
    start = time.time()
    while time.time() - start < deadline:
        resp = client.get(f"{API_URL}/incidents/{incident_id}", timeout=5.0)
        assert resp.status_code == 200
        data = resp.json()
        if data["state"] in ("resolved", "escalated", "failed"):
            return data
        time.sleep(1.0)
    raise AssertionError(f"incident {incident_id} never reached a terminal state")


@pytest.mark.asyncio
async def test_autonomous_bad_deployment_drives_real_docker():
    """A: /incidents/trigger bad_deployment actually breaks + restores the
    real payment-service container through the autonomous pipeline."""
    with httpx.Client(timeout=10.0) as client:
        # Wait for the environment to be up (same pattern as the other E2Es).
        for _ in range(3):
            if _env_ready(client):
                break
            time.sleep(2)
        else:
            pytest.skip("payment-service container not running — skipping autonomous E2E")

        # Baseline: healthy known-good version
        baseline = _payment_health(client)
        assert baseline["health"] == "healthy", f"expected healthy baseline, got {baseline}"

        trigger = client.post(
            f"{API_URL}/incidents/trigger",
            json={"service_name": "payment-service", "scenario": "bad_deployment"},
        )
        assert trigger.status_code == 200, trigger.text
        incident_id = trigger.json()["incident_id"]

        # Right after the trigger the real fault should already be applied:
        # the healthy container was replaced by the FORCE_UNHEALTHY version.
        deadline = time.time() + 60
        while time.time() < deadline:
            status = _payment_health(client)
            if status.get("health") != "healthy":
                break
            time.sleep(1.0)
        else:
            pytest.fail("payment-service never became unhealthy after /incidents/trigger")

        # bad_deployment is P1 → SEMI_AUTONOMOUS; approve to continue.
        assert _wait_for_pending_approval(client, incident_id), "approval never became pending"
        approve = client.post(f"{API_URL}/incidents/{incident_id}/approve")
        assert approve.status_code == 200, approve.text

        incident = _wait_for_terminal_state(client, incident_id)
        assert incident["state"] == "resolved", (
            f"expected resolved, got {incident['state']}; timeline messages: "
            f"{[e['message'] for e in incident['timeline']]}"
        )
        assert incident["remediation_result"]["action"] == "rollback_deploy"
        assert incident["remediation_result"]["success"] is True
        assert incident["verification_result"]["verified"] is True, incident["verification_result"]
        assert incident["report"]["root_cause"] == "bad_deployment"

        # The environment must genuinely be restored to the pre-fault config.
        # (Rollback restores whatever was running before the fault, so compare
        # against the baseline captured before the trigger — not a constant.)
        final = _payment_health(client)
        assert final["health"] == "healthy", f"service not restored: {final}"
        assert final.get("version") == baseline.get("version"), (
            f"expected rollback to baseline {baseline.get('version')}, got {final.get('version')}"
        )


@pytest.mark.asyncio
async def test_autonomous_dependency_outage_drives_real_toxiproxy():
    """B: /incidents/trigger dependency_outage applies a real Toxiproxy toxic
    and the pipeline's circuit_break remediation disables the proxy."""
    with httpx.Client(timeout=10.0) as client:
        for _ in range(3):
            if _env_ready(client):
                break
            time.sleep(2)
        else:
            pytest.skip("payment-service container not running — skipping autonomous E2E")

        # Toxiproxy + proxies must exist (created at backend startup).
        try:
            proxy = client.get(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy", timeout=5.0)
            if proxy.status_code != 200:
                pytest.skip("payment-rpc-proxy not present in Toxiproxy")
        except Exception:
            pytest.skip("Toxiproxy not reachable")

        # Restore a clean baseline so the test is repeatable even after a
        # previous circuit_break left the proxy disabled.
        proxy_data = proxy.json()
        if not proxy_data.get("enabled", True) or proxy_data.get("upstream") != "rpc-service-primary:5000":
            proxy_data["enabled"] = True
            proxy_data["upstream"] = "rpc-service-primary:5000"
            client.post(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy", json=proxy_data, timeout=5.0)
        client.delete(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy/toxics/outage_timeout", timeout=5.0)

        trigger = client.post(
            f"{API_URL}/incidents/trigger",
            json={"service_name": "payment-service", "scenario": "dependency_outage"},
        )
        assert trigger.status_code == 200, trigger.text
        incident_id = trigger.json()["incident_id"]

        # P2 → SEMI_AUTONOMOUS; approve to continue.
        assert _wait_for_pending_approval(client, incident_id), "approval never became pending"
        approve = client.post(f"{API_URL}/incidents/{incident_id}/approve")
        assert approve.status_code == 200, approve.text

        incident = _wait_for_terminal_state(client, incident_id)
        assert incident["state"] == "resolved", (
            f"expected resolved, got {incident['state']}; timeline messages: "
            f"{[e['message'] for e in incident['timeline']]}"
        )
        assert incident["remediation_result"]["action"] == "circuit_break"
        assert incident["remediation_result"]["success"] is True
        assert incident["verification_result"]["verified"] is True, incident["verification_result"]
        assert incident["report"]["root_cause"] == "dependency_outage"

        # The proxy must genuinely be disabled by the circuit breaker.
        proxy = client.get(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy", timeout=5.0)
        assert proxy.status_code == 200
        assert proxy.json().get("enabled") is False, "proxy was not disabled by circuit_break"

"""Regression test for issue #16: Toxiproxy state must reset between
incidents so repeated dependency_outage runs genuinely fail the service.

Run the full inject -> circuit_break cycle TWICE against ONE backend
process.  After the first run the circuit breaker has disabled the RPC
proxy, so on the unfixed code the second injection cannot degrade the
service (payment keeps serving the fallback_cache 200) yet the API still
reports ``docker_performed=true`` and remediation/verification succeed.

Each run therefore asserts /pay is genuinely degraded right after the
injection and genuinely recovered after circuit_break — this fails on the
unfixed code at the second run's degradation check.

Requires the docker-compose services to be running and the backend to be
reachable at API_URL (same setup as test_dependency_outage_e2e.py).
"""

import os

import pytest
import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")
TOXIPROXY_API = os.environ.get("TOXIPROXY_API", "http://localhost:8474")
# payment-service host-mapped port (repo E2E convention: services in
# compose, backend/tests on the host).
PAY_URL = os.environ.get("PAY_URL", "http://localhost:5001/pay")


async def _run_dependency_outage_cycle(client: httpx.AsyncClient, run: int) -> None:
    """Inject dependency_outage, prove the service degraded, then run the
    circuit_break remediation and prove recovery via fallback."""
    label = f"run {run}"

    # 1. Inject the dependency outage (real timeout toxic on the RPC proxy).
    inject = await client.post(
        f"{API_URL}/faults/inject",
        json={
            "scenario": "dependency_outage",
            "service_name": "payment-service",
            "parameters": {},
        },
    )
    assert inject.status_code == 200, f"{label}: inject failed: {inject.text}"
    data = inject.json()
    assert data["status"] == "success", f"{label}: {data}"
    assert data["docker_performed"] is True, f"{label}: no real fault was applied"

    # 2. The service must genuinely degrade BEFORE remediation.  Without the
    #    per-incident reset (issue #16), the second run's proxy is already
    #    disabled so /pay keeps returning 200 via fallback_cache here.
    degraded = await client.get(PAY_URL)
    assert degraded.status_code != 200, (
        f"{label}: /pay returned {degraded.status_code} after injection — no real "
        "fault applied (stale/disabled proxy?)"
    )

    # 3. Circuit break: disable the proxy -> payment falls back to cache.
    rem = await client.post(
        f"{API_URL}/remediation/execute",
        json={
            "incident_id": f"toxiproxy-reset-{run}",
            "action": "circuit_break",
            "target_service": "payment-service",
            "parameters": data["metadata"],
        },
        timeout=30.0,
    )
    assert rem.status_code == 200, f"{label}: remediation failed: {rem.text}"
    rem_data = rem.json()
    assert rem_data["result"]["success"] is True, f"{label}: circuit_break failed: {rem_data}"
    assert rem_data["verification"]["verified"] is True, f"{label}: verification failed: {rem_data}"

    # 4. Service recovered (fallback engaged).
    recovered = await client.get(PAY_URL)
    assert recovered.status_code == 200, f"{label}: service not recovered: {recovered.text}"


@pytest.mark.asyncio
async def test_repeated_dependency_outage_runs_apply_real_fault_each_time():
    """Two consecutive dependency-outage cycles against one backend process.

    The second cycle only works if the environment is reset per incident;
    without the fix the RPC proxy stays disabled from the first cycle's
    circuit_break and the second injection never actually fails the service.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        health = await client.get(f"{API_URL}/services/health?service=payment-service")
        if health.status_code != 200 or not health.json().get("running"):
            pytest.skip("payment-service container not running — skipping test")

        for run in (1, 2):
            await _run_dependency_outage_cycle(client, run)


@pytest.mark.asyncio
async def test_missing_proxy_is_recreated_before_injection():
    """Issue #17: an injection must recreate a missing RPC proxy instead of
    silently no-op'ing.

    Simulates a backend that booted before Toxiproxy was reachable (or a
    Toxiproxy restart that dropped its proxies): the proxy is deleted, then a
    dependency_outage injection must recreate it and genuinely degrade the
    service.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        health = await client.get(f"{API_URL}/services/health?service=payment-service")
        if health.status_code != 200 or not health.json().get("running"):
            pytest.skip("payment-service container not running — skipping test")

        # Remove the RPC proxy entirely.
        deleted = await client.delete(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy")
        assert deleted.status_code in (200, 204), f"could not delete proxy: {deleted.text}"
        assert (
            await client.get(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy")
        ).status_code == 404, "proxy still present after delete"

        # Inject — the pre-injection prepare must recreate the proxy first.
        inject = await client.post(
            f"{API_URL}/faults/inject",
            json={
                "scenario": "dependency_outage",
                "service_name": "payment-service",
                "parameters": {},
            },
        )
        assert inject.status_code == 200, f"inject failed: {inject.text}"
        data = inject.json()
        assert data["docker_performed"] is True, f"no real fault applied: {data}"
        assert data["metadata"]["proxy_name"] == "payment-rpc-proxy"

        # The proxy exists again and the fault genuinely degraded the service.
        proxy = await client.get(f"{TOXIPROXY_API}/proxies/payment-rpc-proxy")
        assert proxy.status_code == 200, "proxy was not recreated"
        assert proxy.json().get("enabled") is True

        degraded = await client.get(PAY_URL)
        assert degraded.status_code != 200, "service not degraded after recreated injection"

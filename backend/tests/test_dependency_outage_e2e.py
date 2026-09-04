import os
import time
import pytest
import httpx

# Assuming the backend is reachable at localhost:8000 when docker-compose is running
API_URL = os.environ.get("API_URL", "http://localhost:8000")

@pytest.mark.asyncio
async def test_dependency_outage_e2e():
    """End-to-end test for Dependency Outage scenario.
    
    Flow:
    1. Verify service and Toxiproxy are healthy
    2. Inject dependency outage (timeout) using Toxiproxy
    3. Verify service /pay fails (or acts degraded)
    4. Execute circuit_break remediation
    5. Verify service /pay works with fallback
    """
    
    # Optional: Wait a moment for docker-compose to be fully up if running locally
    # time.sleep(2)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Check health
        health_resp = await client.get(f"{API_URL}/services/health?service=payment-service")
        assert health_resp.status_code == 200, f"Backend failed: {health_resp.text}"
        data = health_resp.json()
        
        # Ensure it's running before we start
        if not data.get("running"):
            pytest.skip("payment-service container not running, skipping test.")
            
        # 2. Inject Dependency Outage
        inject_resp = await client.post(
            f"{API_URL}/faults/inject",
            json={
                "scenario": "dependency_outage",
                "service_name": "payment-service",
                "parameters": {}
            }
        )
        assert inject_resp.status_code == 200
        inject_data = inject_resp.json()
        assert inject_data["status"] == "success"
        assert inject_data["docker_performed"] is True
        
        metadata = inject_data["metadata"]
        proxy_name = metadata.get("proxy_name")
        assert proxy_name == "payment-rpc-proxy"
        
        print("Injected Dependency Outage successfully.")

        # 3. The fault must genuinely degrade the service BEFORE remediation:
        #    with the 30s timeout toxic on the RPC proxy, /pay must not return
        #    200.  (Regression check for #16 — on a stale/disabled proxy from a
        #    previous incident /pay keeps returning 200 via fallback_cache and
        #    this test used to pass spuriously.)
        pay_before = await client.get("http://localhost:5001/pay", timeout=10.0)
        assert pay_before.status_code != 200, (
            f"/pay returned {pay_before.status_code} after injection — no real fault "
            "was applied (proxy already disabled / stale toxics?)"
        )

        # 4. Execute Remediation (circuit_break)
        rem_resp = await client.post(
            f"{API_URL}/remediation/execute",
            json={
                "incident_id": "test-inc",
                "action": "circuit_break",
                "target_service": "payment-service",
                "parameters": metadata, # Pass metadata containing proxy_name
            },
            timeout=30.0 # Circuit break should be fast, but wait for verification
        )
        
        assert rem_resp.status_code == 200
        rem_data = rem_resp.json()
        
        assert rem_data["status"] == "success"
        result = rem_data["result"]
        assert result["success"] is True
        assert result["action"] == "circuit_break"
        
        # 5. Check Verification
        verification = rem_data.get("verification")
        assert verification is not None
        assert verification["verified"] is True
        
        metrics = verification["recovered_metrics"]

        # Expecting our extra verify check for /pay to be present.  The probe
        # URL depends on where the backend runs: a host-side backend verifies
        # through the published port (verify_http://localhost:5001/pay), while
        # a backend inside the compose network verifies through Docker DNS
        # (verify_http://iras-payment-service:5000/pay) — accept either.
        pay_metric = next(
            (
                value
                for key, value in metrics.items()
                if key.startswith("verify_") and key.endswith("/pay")
            ),
            None,
        )
        assert pay_metric is not None, f"expected a /pay verification probe, got: {metrics}"
        assert pay_metric == 200, f"expected /pay verification probe to pass, got: {metrics}"

        print("Remediation and verification succeeded.")

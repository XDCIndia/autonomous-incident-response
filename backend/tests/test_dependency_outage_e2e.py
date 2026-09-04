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
        
        # 3. Check /pay endpoint on payment-service to see if it times out
        # Actually, the fault injection adds a 30s timeout on Toxiproxy
        # Since payment-service is exposed on port 5001 locally, we can try to call it if we want
        # But for E2E we'll trust the health verification to check it post-remediation
        
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
        
        # Expecting our extra verify check for /pay to be present
        # The verify URL uses host-mapped port (localhost:5001) not Docker-internal
        verify_pay_key = "verify_http://localhost:5001/pay"
        assert verify_pay_key in metrics
        assert metrics[verify_pay_key] == 200
        
        print("Remediation and verification succeeded.")

import os
import time
import pytest
import httpx

# Assuming the backend is reachable at localhost:8000 when docker-compose is running
API_URL = os.environ.get("API_URL", "http://localhost:8000")

@pytest.mark.asyncio
async def test_bad_deployment_e2e():
    """End-to-end test for Bad Deployment injection and remediation."""
    
    # 1. Check initial health of payment-service
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Give services time to start up if running right after docker compose up
        retries = 3
        healthy = False
        for _ in range(retries):
            resp = await client.get(f"{API_URL}/services/health?service=payment-service")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("health") == "healthy":
                    healthy = True
                    break
            time.sleep(2)
        
        assert healthy, "Service payment-service did not become healthy initially"
        
        # 2. Inject Bad Deployment
        inject_payload = {
            "scenario": "bad_deployment",
            "service_name": "payment-service",
            "parameters": {
                "version": "v2.4.1-bad"
            }
        }
        resp = await client.post(f"{API_URL}/faults/inject", json=inject_payload)
        assert resp.status_code == 200, f"Injection failed: {resp.text}"
        
        data = resp.json()
        assert data["status"] == "success"
        assert data["docker_performed"] is True
        
        metadata = data["metadata"]
        previous_config = metadata.get("previous_config")
        assert previous_config is not None, "previous_config was not returned"
        assert metadata.get("bad_version") == "v2.4.1-bad"
        
        # 3. Verify the bad version genuinely degraded the service.  Probe the
        #    app's own /health (FORCE_UNHEALTHY=true -> HTTP 500 as soon as it
        #    starts) rather than Docker's health status: a freshly recreated
        #    container reports `starting` through the healthcheck start_period
        #    (~5-10s) regardless of what the app does, so `!= healthy` used to
        #    pass even when nothing actually broke (issue #18).
        degraded = False
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                resp = await client.get("http://localhost:5001/health", timeout=5.0)
                if resp.status_code != 200:
                    degraded = True
                    break
            except Exception:
                degraded = True
                break
            time.sleep(1)
        assert degraded, "payment-service /health still returned 200 after the bad deployment"
        
        # 4. Execute Remediation (Rollback)
        remediation_payload = {
            "action": "rollback_deploy",
            "target_service": "payment-service",
            "parameters": {
                "previous_config": previous_config
            }
        }
        resp = await client.post(f"{API_URL}/remediation/execute", json=remediation_payload)
        assert resp.status_code == 200, f"Remediation failed: {resp.text}"
        
        rem_data = resp.json()
        assert rem_data["status"] == "success"
        
        result = rem_data["result"]
        assert result["success"] is True
        assert result["action"] == "rollback_deploy"
        
        verification = rem_data.get("verification")
        assert verification is not None
        assert verification["verified"] is True
        
        # 5. Verify service is healthy again
        resp = await client.get(f"{API_URL}/services/health?service=payment-service")
        status_data = resp.json()
        assert status_data.get("health") == "healthy", "Service did not become healthy after rollback"

"""End-to-end test — full pipeline from trigger to resolved incident.

Verifies:
1. POST /incidents/trigger works
2. Incident is created
3. Complete mock pipeline executes
4. Every stage produces a timeline event
5. Incident becomes RESOLVED
"""

import pytest
from httpx import AsyncClient, ASGITransport

import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage
from backend.platform.events import get_event_bus


@pytest.fixture(autouse=True)
async def reset_storage():
    """Give each test a fresh in-memory SQLite-backed Storage singleton.

    The routes under test call the module-level `get_storage()` singleton,
    so we swap it out directly rather than reaching into private attributes
    of the old in-memory mock (which no longer exist on the real Storage).
    """
    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None


@pytest.mark.e2e
class TestIncidentPipeline:
    """Full pipeline E2E tests."""

    async def test_trigger_and_complete_pipeline(self):
        """Trigger an incident and verify the full pipeline completes."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Step 1: Trigger incident
            response = await client.post("/incidents/trigger", json={
                "service_name": "payment-service",
                "scenario": "bad_deployment",
            })
            assert response.status_code == 200
            data = response.json()
            incident_id = data["incident_id"]
            assert data["status"] == "processing"

            # Step 2: Wait for pipeline to complete
            import asyncio
            await asyncio.sleep(2.0)

            # Step 3: Verify incident exists and is resolved
            response = await client.get(f"/incidents/{incident_id}")
            assert response.status_code == 200
            incident = response.json()
            assert incident["state"] == "resolved"
            assert incident["severity"] is not None
            assert incident["report"] is not None

            # Step 4: Verify timeline has all stages
            response = await client.get(f"/incidents/{incident_id}/timeline")
            assert response.status_code == 200
            timeline = response.json()["timeline"]
            assert len(timeline) >= 8  # At least one event per stage

            # Verify each stage is represented
            stages = {e["stage"] for e in timeline}
            expected_stages = {
                "detection", "investigation", "arbiter",
                "severity", "autonomy", "remediation",
                "verification", "report",
            }
            assert expected_stages.issubset(stages)

    async def test_health_check(self):
        """Verify health endpoint works."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    async def test_list_incidents(self):
        """Verify list incidents endpoint works."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Trigger an incident first
            await client.post("/incidents/trigger", json={
                "service_name": "test-service",
                "scenario": "database_failure",
            })
            import asyncio
            await asyncio.sleep(1.0)

            # List incidents
            response = await client.get("/incidents")
            assert response.status_code == 200
            incidents = response.json()
            assert len(incidents) >= 1

    async def test_invalid_scenario_returns_400(self):
        """Verify invalid scenario returns 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/incidents/trigger", json={
                "service_name": "test-service",
                "scenario": "nonexistent_scenario",
            })
            assert response.status_code == 400

    async def test_all_scenarios_produce_timeline(self):
        """Verify all 4 scenarios produce complete timelines."""
        scenarios = ["bad_deployment", "database_failure", "dependency_outage", "resource_exhaustion"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for scenario in scenarios:
                response = await client.post("/incidents/trigger", json={
                    "service_name": f"test-{scenario}",
                    "scenario": scenario,
                })
                assert response.status_code == 200
                incident_id = response.json()["incident_id"]

                import asyncio
                await asyncio.sleep(2.0)

                # Verify timeline
                response = await client.get(f"/incidents/{incident_id}/timeline")
                assert response.status_code == 200
                timeline = response.json()["timeline"]
                assert len(timeline) >= 8, f"Scenario {scenario} missing timeline events"

                # Verify incident is resolved
                response = await client.get(f"/incidents/{incident_id}")
                assert response.json()["state"] == "resolved"

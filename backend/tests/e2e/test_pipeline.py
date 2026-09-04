"""End-to-end test — full pipeline from trigger to resolved incident.

Verifies:
1. POST /incidents/trigger works
2. Incident is created
3. Complete mock pipeline executes
4. Every stage produces a timeline event
5. Incident becomes RESOLVED
"""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport

import backend.orchestrator as orchestrator_module
import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage
from backend.platform.events import get_event_bus


@pytest.fixture(autouse=True)
async def reset_storage(monkeypatch):
    """Give each test a fresh in-memory SQLite-backed Storage singleton.

    The routes under test call the module-level `get_storage()` singleton,
    so we swap it out directly rather than reaching into private attributes
    of the old in-memory mock (which no longer exist on the real Storage).

    Also blanks any LLM provider keys from the local `.env` for the duration
    of the test: this suite asserts deterministic severity/approval behavior
    that depends on the mock agents' keyword-based root-cause detection —
    IncidentOrchestrator() defaults to the real LLM-backed agents whenever a
    key is configured, which would make these tests non-deterministic (and
    make real, paid API calls) if a developer's `.env` has keys set.
    """
    import backend.platform.config as config_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    config_module._settings = None

    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None
    orchestrator_module._orchestrator = None
    config_module._settings = None


# Scenarios that produce P1/P2 severity (require approval)
REQUIRES_APPROVAL = {"bad_deployment", "database_failure", "dependency_outage"}


async def _trigger_and_wait(client, service_name, scenario, wait_for_approval=True):
    """Trigger an incident, approve if needed, and wait for completion."""
    response = await client.post("/incidents/trigger", json={
        "service_name": service_name,
        "scenario": scenario,
    })
    assert response.status_code == 200
    incident_id = response.json()["incident_id"]

    if wait_for_approval and scenario in REQUIRES_APPROVAL:
        # Wait for pipeline to reach the approval point
        await asyncio.sleep(0.5)
        # Approve the incident
        approve_response = await client.post(f"/incidents/{incident_id}/approve")
        assert approve_response.status_code == 200

    # Wait for pipeline to complete
    await asyncio.sleep(2.0)
    return incident_id


@pytest.mark.e2e
class TestIncidentPipeline:
    """Full pipeline E2E tests."""

    async def test_trigger_and_complete_pipeline(self):
        """Trigger an incident and verify the full pipeline completes."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            incident_id = await _trigger_and_wait(
                client, "payment-service", "bad_deployment"
            )

            # Verify incident exists and is resolved
            response = await client.get(f"/incidents/{incident_id}")
            assert response.status_code == 200
            incident = response.json()
            assert incident["state"] == "resolved"
            assert incident["severity"] is not None
            assert incident["report"] is not None

            # Verify timeline has all stages
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
            # Trigger an incident first (resource_exhaustion is P3, no approval needed)
            await client.post("/incidents/trigger", json={
                "service_name": "test-service",
                "scenario": "resource_exhaustion",
            })
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
                incident_id = await _trigger_and_wait(
                    client, f"test-{scenario}", scenario
                )

                # Verify timeline
                response = await client.get(f"/incidents/{incident_id}/timeline")
                assert response.status_code == 200
                timeline = response.json()["timeline"]
                assert len(timeline) >= 8, f"Scenario {scenario} missing timeline events"

                # Verify incident is resolved
                response = await client.get(f"/incidents/{incident_id}")
                assert response.json()["state"] == "resolved"

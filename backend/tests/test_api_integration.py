"""Integration tests for Platform/Integration's own additions to the API:
the knowledge-base search endpoint, and the WebSocket live-timeline stream
against the real (now persistent) storage backend."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage


@pytest.fixture(autouse=True)
async def reset_storage():
    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None


class TestKnowledgeBaseEndpoint:
    @pytest.mark.asyncio
    async def test_search_returns_relevant_results(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/knowledge-base/search", params={"query": "bad deployment payment-service errors"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "bad deployment payment-service errors"
            assert len(data["results"]) > 0
            assert data["results"][0]["root_cause"] == "bad_deployment"

    @pytest.mark.asyncio
    async def test_search_requires_query_param(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/knowledge-base/search")
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_rejects_empty_query(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/knowledge-base/search", params={"query": ""})
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_top_k_respected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/knowledge-base/search", params={"query": "service incident", "top_k": 1}
            )
            assert response.status_code == 200
            assert len(response.json()["results"]) <= 1

    @pytest.mark.asyncio
    async def test_search_top_k_zero_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/knowledge-base/search", params={"query": "service incident", "top_k": 0}
            )
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_top_k_above_max_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/knowledge-base/search", params={"query": "service incident", "top_k": 11}
            )
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_no_match_returns_empty_results_not_error(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/knowledge-base/search", params={"query": "xyzzy quantum banana zebra"}
            )
            assert response.status_code == 200
            assert response.json()["results"] == []

    @pytest.mark.asyncio
    async def test_search_reflects_newly_resolved_incidents(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.post(
                "/incidents/trigger",
                json={"service_name": "payment-service", "scenario": "bad_deployment"},
            )
            incident_id = trigger.json()["incident_id"]
            await asyncio.sleep(2.0)

            response = await client.get(
                "/knowledge-base/search", params={"query": "payment-service deployment rollback"}
            )
            results = response.json()["results"]
            assert any(r["incident_id"] == incident_id for r in results)


class TestHealthAndBasicEndpointsStillWork:
    """Not new endpoints, but worth confirming Platform/Integration's storage
    swap didn't regress anything the other roles depend on."""

    @pytest.mark.asyncio
    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "service": "autonomous-incident-response"}

    @pytest.mark.asyncio
    async def test_get_nonexistent_incident_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/incidents/does-not-exist")
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_timeline_of_nonexistent_incident_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/incidents/does-not-exist/timeline")
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_incidents_empty(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/incidents")
            assert response.status_code == 200
            assert response.json() == []

    @pytest.mark.asyncio
    async def test_trigger_with_all_four_scenarios_and_verify_persistence(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for scenario in ("bad_deployment", "database_failure", "dependency_outage", "resource_exhaustion"):
                response = await client.post(
                    "/incidents/trigger",
                    json={"service_name": f"svc-{scenario}", "scenario": scenario},
                )
                assert response.status_code == 200
            await asyncio.sleep(2.0)

            response = await client.get("/incidents")
            assert len(response.json()) == 4


class TestWebSocketLiveTimeline:
    """Uses one sync starlette TestClient for the whole test — its background
    portal thread keeps the app's event loop (and the pipeline's background
    task) running between calls, whereas juggling a second async client here
    would trigger a second, data-wiping `lifespan` startup on re-entry."""

    def test_ws_streams_events_as_pipeline_runs(self):
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            trigger = client.post(
                "/incidents/trigger",
                json={"service_name": "payment-service", "scenario": "bad_deployment"},
            )
            incident_id = trigger.json()["incident_id"]

            with client.websocket_connect(f"/ws/incidents/{incident_id}") as ws:
                stages_seen = set()
                # Drain events until we see the pipeline finish or hit a
                # generous cap — never hang the test suite if something
                # regresses.
                for _ in range(60):
                    data = ws.receive_json()
                    if data.get("type") == "ping":
                        continue
                    stages_seen.add(data.get("stage"))
                    if "report" in stages_seen:
                        break
                assert "report" in stages_seen

    def test_ws_late_connect_replays_existing_timeline(self):
        import time

        from starlette.testclient import TestClient

        with TestClient(app) as client:
            trigger = client.post(
                "/incidents/trigger",
                json={"service_name": "payment-service", "scenario": "bad_deployment"},
            )
            incident_id = trigger.json()["incident_id"]
            # Let the pipeline finish BEFORE connecting — this is the "late
            # connect" case: the WS handler must replay history, not just
            # stream from the point of connection onward.
            time.sleep(2.0)

            with client.websocket_connect(f"/ws/incidents/{incident_id}") as ws:
                first_event = ws.receive_json()
                assert first_event.get("stage") == "detection"

    def test_ws_replays_full_history_in_order(self):
        import time

        from starlette.testclient import TestClient

        with TestClient(app) as client:
            trigger = client.post(
                "/incidents/trigger",
                json={"service_name": "payment-service", "scenario": "bad_deployment"},
            )
            incident_id = trigger.json()["incident_id"]
            time.sleep(2.0)

            with client.websocket_connect(f"/ws/incidents/{incident_id}") as ws:
                events = []
                for _ in range(20):
                    data = ws.receive_json()
                    if data.get("type") == "ping":
                        break
                    events.append(data)
                stages = [e["stage"] for e in events]
                assert stages == sorted(stages, key=lambda s: events[stages.index(s)].get("timestamp", ""))
                assert stages[0] == "detection"
                assert stages[-1] == "report"

    def test_ws_unknown_incident_id_does_not_crash_server(self):
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/ws/incidents/totally-unknown-id") as ws:
                # No history to replay, but the connection must stay open and
                # eventually send a keepalive rather than erroring out.
                data = ws.receive_json()
                assert data.get("type") == "ping"

    def test_two_concurrent_ws_subscribers_both_get_full_stream(self):
        import threading
        import time

        from starlette.testclient import TestClient

        with TestClient(app) as client:
            trigger = client.post(
                "/incidents/trigger",
                json={"service_name": "payment-service", "scenario": "bad_deployment"},
            )
            incident_id = trigger.json()["incident_id"]

            results = {}

            def _listen(name):
                with client.websocket_connect(f"/ws/incidents/{incident_id}") as ws:
                    stages = set()
                    for _ in range(60):
                        data = ws.receive_json()
                        if data.get("type") == "ping":
                            continue
                        stages.add(data.get("stage"))
                        if "report" in stages:
                            break
                    results[name] = stages

            t1 = threading.Thread(target=_listen, args=("a",))
            t2 = threading.Thread(target=_listen, args=("b",))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert "report" in results.get("a", set())
            assert "report" in results.get("b", set())

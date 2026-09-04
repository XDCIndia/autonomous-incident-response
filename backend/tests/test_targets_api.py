"""Integration tests for the /targets endpoints (issue #36)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import backend.orchestrator as orchestrator_module
import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage


@pytest.fixture(autouse=True)
async def reset_storage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("API_KEY", "")

    import backend.platform.config as config_module

    config_module._settings = None

    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None
    orchestrator_module._orchestrator = None
    config_module._settings = None


class TestCreateAndListTargets:
    @pytest.mark.asyncio
    async def test_create_target_returns_it(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/targets", json={"name": "My App", "url": "http://example.com"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "My App"
            assert data["url"] == "http://example.com"
            assert data["monitoring_enabled"] is True
            assert data["health_status"] == "unknown"
            assert "id" in data

    @pytest.mark.asyncio
    async def test_list_targets_includes_created_one(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/targets", json={"name": "My App", "url": "http://example.com"}
            )
            target_id = created.json()["id"]

            listed = await client.get("/targets")
            assert listed.status_code == 200
            ids = [t["id"] for t in listed.json()]
            assert target_id in ids

    @pytest.mark.asyncio
    async def test_get_single_target(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/targets", json={"name": "My App", "url": "http://example.com"}
            )
            target_id = created.json()["id"]

            resp = await client.get(f"/targets/{target_id}")
            assert resp.status_code == 200
            assert resp.json()["id"] == target_id

    @pytest.mark.asyncio
    async def test_get_unknown_target_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/targets/does-not-exist")
            assert resp.status_code == 404


class TestDeleteAndToggleTarget:
    @pytest.mark.asyncio
    async def test_delete_target(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/targets", json={"name": "My App", "url": "http://example.com"}
            )
            target_id = created.json()["id"]

            resp = await client.delete(f"/targets/{target_id}")
            assert resp.status_code == 200

            resp = await client.get(f"/targets/{target_id}")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_unknown_target_returns_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/targets/does-not-exist")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_monitoring_off_and_on(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/targets", json={"name": "My App", "url": "http://example.com"}
            )
            target_id = created.json()["id"]

            resp = await client.post(f"/targets/{target_id}/monitoring", json={"enabled": False})
            assert resp.status_code == 200
            assert resp.json()["monitoring_enabled"] is False

            resp = await client.post(f"/targets/{target_id}/monitoring", json={"enabled": True})
            assert resp.status_code == 200
            assert resp.json()["monitoring_enabled"] is True


class TestTargetsRequireApiKeyWhenConfigured:
    @pytest.mark.asyncio
    async def test_create_rejected_without_key_when_configured(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sekret")
        import backend.platform.config as config_module

        config_module._settings = None
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/targets", json={"name": "My App", "url": "http://example.com"}
                )
                assert resp.status_code == 401
        finally:
            monkeypatch.setenv("API_KEY", "")
            config_module._settings = None

    @pytest.mark.asyncio
    async def test_get_targets_stays_open_without_key(self, monkeypatch):
        # GET is read-only — same posture as GET /incidents, not gated.
        monkeypatch.setenv("API_KEY", "sekret")
        import backend.platform.config as config_module

        config_module._settings = None
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/targets")
                assert resp.status_code == 200
        finally:
            monkeypatch.setenv("API_KEY", "")
            config_module._settings = None

"""Tests for dashboard login (Phase 5): POST /auth/login, /auth/logout,
GET /auth/session, and the require_auth gate applied to every previously-
open read endpoint plus every existing mutating endpoint.

Uses ASGITransport(app=app) throughout — this repo's usual hermetic
pattern — with httpx.AsyncClient's built-in cookie jar so a login's
Set-Cookie response is automatically resent on subsequent requests from the
same client, exactly like a real browser.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import backend.api.app as app_module
import backend.orchestrator as orchestrator_module
import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage


@pytest.fixture(autouse=True)
async def reset_state(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")

    import backend.platform.config as config_module

    config_module._settings = None
    app_module._session_store = None
    app_module._target_creation_limiter = None

    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None
    orchestrator_module._orchestrator = None
    config_module._settings = None
    app_module._session_store = None
    app_module._target_creation_limiter = None


class TestLoginDisabledByDefault:
    @pytest.mark.asyncio
    async def test_login_endpoint_404s_when_auth_password_unset(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"password": "anything"})
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_session_endpoint_reports_auth_not_required(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/session")
            assert resp.status_code == 200
            body = resp.json()
            assert body["auth_required"] is False
            assert body["authenticated"] is True

    @pytest.mark.asyncio
    async def test_reads_and_writes_unaffected_when_nothing_configured(self):
        """The whole point of Phase 5 being off-by-default: every existing
        endpoint keeps behaving exactly as it did before this feature."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/targets")).status_code == 200
            assert (await client.get("/incidents")).status_code == 200
            created = await client.post(
                "/targets", json={"name": "x", "url": "https://example.com"}
            )
            assert created.status_code == 200


class TestLoginFlow:
    @pytest.mark.asyncio
    async def test_correct_password_logs_in_and_sets_cookie(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"password": "letmein"})
            assert resp.status_code == 200
            assert resp.json() == {"authenticated": True, "auth_required": True}
            assert "sb_session" in resp.cookies

    @pytest.mark.asyncio
    async def test_wrong_password_is_rejected_and_sets_no_cookie(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"password": "wrong"})
            assert resp.status_code == 401
            assert "sb_session" not in resp.cookies

    @pytest.mark.asyncio
    async def test_session_cookie_from_login_authorizes_subsequent_requests(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Before login: every read is gated now that AUTH_PASSWORD is set.
            assert (await client.get("/targets")).status_code == 401
            assert (await client.get("/incidents")).status_code == 401

            login = await client.post("/auth/login", json={"password": "letmein"})
            assert login.status_code == 200

            # httpx.AsyncClient's cookie jar resends the Set-Cookie automatically.
            assert (await client.get("/targets")).status_code == 200
            assert (await client.get("/incidents")).status_code == 200

            session = await client.get("/auth/session")
            assert session.json() == {"authenticated": True, "auth_required": True}

    @pytest.mark.asyncio
    async def test_logout_invalidates_the_session(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/auth/login", json={"password": "letmein"})
            assert (await client.get("/targets")).status_code == 200

            logout = await client.post("/auth/logout")
            assert logout.status_code == 200
            assert logout.json()["authenticated"] is False

            # The (now-invalidated) cookie the client still holds must not work.
            assert (await client.get("/targets")).status_code == 401

    @pytest.mark.asyncio
    async def test_forged_cookie_value_is_rejected(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", cookies={"sb_session": "made-up-token"}
        ) as client:
            resp = await client.get("/targets")
            assert resp.status_code == 401


class TestApiKeyAndSessionAreIndependent:
    """A valid X-API-Key satisfies require_auth without ever logging in —
    programmatic/API clients must keep working exactly as before, unaffected
    by AUTH_PASSWORD being configured for human dashboard users."""

    @pytest.mark.asyncio
    async def test_api_key_alone_still_works_when_auth_password_also_set(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "progkey")
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/targets", headers={"X-API-Key": "progkey"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_neither_key_nor_session_is_rejected(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "progkey")
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/targets")).status_code == 401


class TestWebSocketGating:
    @pytest.mark.asyncio
    async def test_websocket_rejected_without_auth_when_configured(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/incidents/does-not-matter"):
                    pass

    @pytest.mark.asyncio
    async def test_websocket_allowed_with_valid_api_key_header(self, monkeypatch):
        monkeypatch.setenv("AUTH_PASSWORD", "letmein")
        monkeypatch.setenv("API_KEY", "progkey")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect(
                "/ws/incidents/does-not-matter", headers={"X-API-Key": "progkey"}
            ) as ws:
                # A successful handshake is the assertion — connecting at all
                # proves _websocket_authorized accepted the API key.
                ws.close()

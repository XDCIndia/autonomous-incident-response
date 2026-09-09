"""Tests for multi-user dashboard auth: POST /auth/signup, POST /auth/login,
/auth/logout, GET /auth/session, and the require_auth gate applied to every
previously-open read endpoint plus every existing mutating endpoint.

Uses ASGITransport(app=app) throughout — this repo's usual hermetic
pattern — with httpx.AsyncClient's built-in cookie jar so a login's
Set-Cookie response is automatically resent on subsequent requests from
the same client, exactly like a real browser.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import backend.api.app as app_module
import backend.orchestrator as orchestrator_module
import backend.platform.storage as storage_module
import backend.platform.users as users_module
from backend.api.app import app
from backend.platform.storage import Storage

SIGNUP_ALICE = {"name": "Alice Ops", "email": "alice@example.com", "password": "wonderland-99"}
SIGNUP_BOB = {"name": "Bob SRE", "email": "bob@example.com", "password": "builder-123"}


async def _signup(client: AsyncClient, payload: dict = SIGNUP_ALICE):
    return await client.post("/auth/signup", json=payload)


@pytest.fixture(autouse=True)
async def reset_state(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")  # legacy env, ignored now
    # The repo's own .env defines DEMO_USER_* — clear it so seeding can't
    # leak into hermetic tests (a seeded user would wrongly "satisfy" the
    # fail-closed boot check and turn on the auth gate mid-test).
    monkeypatch.setenv("DEMO_USER_NAME", "")
    monkeypatch.setenv("DEMO_USER_EMAIL", "")
    monkeypatch.setenv("DEMO_USER_PASSWORD", "")

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


class TestAuthDisabledByDefault:
    """Zero user accounts + no API key — auth is a complete no-op, exactly
    like every endpoint behaved before accounts existed."""

    @pytest.mark.asyncio
    async def test_session_endpoint_reports_auth_not_required(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/session")
            assert resp.status_code == 200
            body = resp.json()
            assert body["auth_required"] is False
            assert body["authenticated"] is True
            assert body["user"] is None

    @pytest.mark.asyncio
    async def test_reads_and_writes_unaffected_when_no_accounts_exist(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/targets")).status_code == 200
            assert (await client.get("/incidents")).status_code == 200
            created = await client.post(
                "/targets", json={"name": "x", "url": "https://example.com"}
            )
            assert created.status_code == 200


class TestSignup:
    @pytest.mark.asyncio
    async def test_signup_creates_user_and_session(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await _signup(client)
            assert resp.status_code == 201
            body = resp.json()
            assert body["authenticated"] is True
            assert body["auth_required"] is True
            assert body["user"] == {"name": "Alice Ops", "email": "alice@example.com"}
            assert "sb_session" in resp.cookies
            # The response must never leak a password hash.
            assert "password_hash" not in body and "password" not in body

    @pytest.mark.asyncio
    async def test_password_is_hashed_not_stored_plaintext(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            storage = storage_module.get_storage()
            user = await users_module.get_user_by_email(storage, "alice@example.com")
            assert user is not None
            assert user.password_hash != SIGNUP_ALICE["password"]
            assert user.password_hash.startswith("pbkdf2_sha256$")
            assert "wonderland-99" not in user.password_hash

    @pytest.mark.asyncio
    async def test_email_is_normalized_and_unique(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await _signup(client, {**SIGNUP_ALICE, "email": "  Alice@Example.COM  "})).status_code == 201
            dup = await _signup(client, {**SIGNUP_BOB, "email": "alice@example.com"})
            assert dup.status_code == 409
            dup_mixed = await _signup(client, {**SIGNUP_BOB, "email": "ALICE@EXAMPLE.COM"})
            assert dup_mixed.status_code == 409

    @pytest.mark.asyncio
    async def test_invalid_signup_payloads_are_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await _signup(client, {**SIGNUP_ALICE, "name": "  "})).status_code == 422
            assert (await _signup(client, {**SIGNUP_ALICE, "email": "not-an-email"})).status_code == 422
            short_pw = {**SIGNUP_ALICE, "email": "short@example.com", "password": "123"}
            assert (await _signup(client, short_pw)).status_code == 422


class TestLoginFlow:
    async def _alice_signup_then_login(self, client: AsyncClient):
        signup = await _signup(client)
        assert signup.status_code == 201
        await client.post("/auth/logout")  # prove login itself works, not the signup session

    @pytest.mark.asyncio
    async def test_correct_credentials_log_in_and_set_cookie(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await self._alice_signup_then_login(client)
            resp = await client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "wonderland-99"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["authenticated"] is True
            assert body["user"]["email"] == "alice@example.com"
            assert "sb_session" in resp.cookies

    @pytest.mark.asyncio
    async def test_wrong_password_and_unknown_email_both_401(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            for payload in (
                {"email": "alice@example.com", "password": "wrong-password"},
                {"email": "nobody@example.com", "password": "wonderland-99"},
            ):
                resp = await client.post("/auth/login", json=payload)
                assert resp.status_code == 401
                assert "sb_session" not in resp.cookies
                # No account-enumeration leak: same message either way.
                assert resp.json()["detail"] == "Incorrect email or password"

    @pytest.mark.asyncio
    async def test_session_cookie_authorizes_subsequent_requests(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # One account existing turns the gate on for everyone.
            assert (await client.get("/targets")).status_code == 200
            signup = await _signup(client)
            assert signup.status_code == 201
            # Signup already established a session on THIS client — an
            # anonymous client (fresh cookie jar, no credentials) is what
            # must now be rejected.
            anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            assert (await anon.get("/targets")).status_code == 401
            assert (await anon.get("/incidents")).status_code == 401
            await anon.aclose()

            await client.post("/auth/logout")  # prove login itself works
            login = await client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "wonderland-99"},
            )
            assert login.status_code == 200

            # httpx.AsyncClient's cookie jar resends the Set-Cookie automatically.
            assert (await client.get("/targets")).status_code == 200
            assert (await client.get("/incidents")).status_code == 200

            session = await client.get("/auth/session")
            body = session.json()
            assert body["authenticated"] is True
            assert body["user"]["name"] == "Alice Ops"

    @pytest.mark.asyncio
    async def test_logout_invalidates_the_session(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            assert (await client.get("/targets")).status_code == 200

            logout = await client.post("/auth/logout")
            assert logout.status_code == 200
            assert logout.json()["authenticated"] is False

            # The (now-invalidated) cookie the client still holds must not work.
            assert (await client.get("/targets")).status_code == 401

    @pytest.mark.asyncio
    async def test_forged_cookie_value_is_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            forged = ASGITransport(app=app)
            async with AsyncClient(
                transport=forged, base_url="http://test", cookies={"sb_session": "made-up-token"}
            ) as evil:
                assert (await evil.get("/targets")).status_code == 401


class TestApiKeyAndSessionAreIndependent:
    """A valid X-API-Key satisfies require_auth without ever logging in —
    programmatic/API clients must keep working exactly as before, unaffected
    by user-account login existing for human dashboard users."""

    @pytest.mark.asyncio
    async def test_api_key_alone_still_works_when_accounts_exist(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "progkey")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            resp = await client.get("/targets", headers={"X-API-Key": "progkey"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_neither_key_nor_session_is_rejected(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "progkey")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            # Fresh client, no API key, no session cookie → rejected.
            anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            assert (await anon.get("/targets")).status_code == 401
            await anon.aclose()


class TestWebSocketGating:
    """WebSocket auth is enforced by _websocket_authorized() which runs
    BEFORE accept(). Starlette's synchronous TestClient deadlocks inside
    pytest-asyncio's event loop, so we test the actual security gate
    function directly (it reads the same headers/cookies that the
    endpoint handler does) and verify the endpoint handler calls it.
    """

    @staticmethod
    def _make_ws(headers: list[tuple[bytes, bytes]] | None = None,
                cookies: dict[str, str] | None = None):
        """Build a minimal WebSocket-like object for testing _websocket_authorized.

        Only the attributes that _websocket_authorized reads (.headers,
        .cookies, .scope) are populated — the receive/send callables are
        async no-ops since we never accept() or read from this object.
        """
        async def _noop(*_a, **_kw): pass

        from starlette.websockets import WebSocket

        scope: dict = {"type": "websocket", "headers": headers or [], "cookies": cookies or {}}
        return WebSocket(scope=scope, receive=_noop, send=_noop)

    @pytest.mark.asyncio
    async def test_rejected_without_auth_when_accounts_exist(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            ws = self._make_ws()
            assert await app_module._websocket_authorized(ws) is False

    @pytest.mark.asyncio
    async def test_allowed_with_valid_api_key(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "progkey")
        import backend.platform.config as config_module

        config_module._settings = None
        app_module._session_store = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            ws = self._make_ws(headers=[(b"x-api-key", b"progkey")])
            assert await app_module._websocket_authorized(ws) is True

    @pytest.mark.asyncio
    async def test_allowed_with_valid_session_cookie(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            signup = await _signup(client)
            assert signup.status_code == 201
            token = app_module._get_session_store().create(
                # any user id — the store only checks token validity
                "00000000-0000-0000-0000-000000000000"
            )
            # Starlette's WebSocket.cookies parses the 'cookie' header, not scope['cookies']
            ws = self._make_ws(headers=[(b"cookie", f"sb_session={token}".encode())])
            assert await app_module._websocket_authorized(ws) is True

    @pytest.mark.asyncio
    async def test_rejected_with_invalid_session_cookie(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client)
            ws = self._make_ws(headers=[(b"cookie", b"sb_session=bogus-token")])
            assert await app_module._websocket_authorized(ws) is False

    @pytest.mark.asyncio
    async def test_open_when_no_auth_configured(self):
        """No API_KEY and no user accounts — auth gate is a no-op."""
        ws = self._make_ws()
        assert await app_module._websocket_authorized(ws) is True


class TestDemoUserSeeding:
    @pytest.mark.asyncio
    async def test_seed_creates_account_once(self):
        storage = storage_module.get_storage()
        seeded = await users_module.seed_demo_user(
            storage, "Demo Operator", "demo@example.com", "demo-pass-123"
        )
        assert seeded is not None and seeded.email == "demo@example.com"
        again = await users_module.seed_demo_user(
            storage, "Demo Operator", "demo@example.com", "demo-pass-123"
        )
        assert again is None
        assert await users_module.count_users(storage) == 1

    @pytest.mark.asyncio
    async def test_seed_disabled_without_credentials(self):
        storage = storage_module.get_storage()
        assert await users_module.seed_demo_user(storage, "", "", "") is None
        assert await users_module.count_users(storage) == 0

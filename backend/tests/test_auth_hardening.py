"""Tests for Phase 1b auth hardening: API_KEY becomes required (not
optional) outside APP_ENV=development.

Uses starlette's TestClient(app), same as the WebSocket tests in
test_api_integration.py — unlike this repo's usual ASGITransport(app=app)
pattern, TestClient genuinely runs the app's lifespan() startup/shutdown,
which is where the fail-closed check lives (same place the existing
REAL_ENV=on fail-loud check lives).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import backend.orchestrator as orchestrator_module
import backend.platform.config as config_module
import backend.platform.storage as storage_module
from backend.api.app import app
from backend.platform.storage import Storage


@pytest.fixture(autouse=True)
async def reset_state(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Keep every boot in this file fast and hermetic, same reasoning as
    # test_api_integration.py's identical fixture: REAL_ENV != "off" makes
    # lifespan() retry Toxiproxy readiness for up to 40s.
    monkeypatch.setenv("REAL_ENV", "off")
    config_module._settings = None

    storage = Storage(db_path=":memory:")
    await storage.init_db()
    storage_module._storage = storage
    yield
    await storage.close()
    storage_module._storage = None
    orchestrator_module._orchestrator = None
    config_module._settings = None


class TestFailsClosedOutsideDevelopment:
    def test_production_without_api_key_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_KEY", "")
        config_module._settings = None

        with pytest.raises(RuntimeError, match="API_KEY is not set"):
            with TestClient(app):
                pass

    def test_staging_without_api_key_refuses_to_start(self, monkeypatch):
        """Any non-development value is treated the same way — this isn't
        a hardcoded 'production' special-case."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("API_KEY", "")
        config_module._settings = None

        with pytest.raises(RuntimeError, match="API_KEY is not set"):
            with TestClient(app):
                pass

    def test_production_with_api_key_starts_fine(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_KEY", "some-real-secret")
        config_module._settings = None

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200


class TestDevelopmentStaysConvenient:
    def test_development_without_api_key_starts_fine(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("API_KEY", "")
        config_module._settings = None

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_default_app_env_without_api_key_starts_fine(self, monkeypatch):
        """APP_ENV unset at all must behave like the documented default
        (development) — not an accidental lockout for anyone who hasn't
        set it."""
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("API_KEY", "")
        config_module._settings = None

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

"""Tests for Settings — env var loading, defaults, and the extra="ignore"
fix (the shared .env carries frontend NEXT_PUBLIC_* vars Settings must
tolerate without crashing)."""

from __future__ import annotations

import pytest

from backend.platform.config import Settings, get_settings


def _clear_cache():
    import backend.platform.config as config_module

    config_module._settings = None


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    _clear_cache()
    yield
    _clear_cache()


class TestDefaults:
    def test_default_values(self, monkeypatch):
        for key in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER",
            "DATABASE_URL", "RCA_CONFIDENCE_THRESHOLD", "RCA_MAX_RETRIES",
        ):
            monkeypatch.delenv(key, raising=False)
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "anthropic"
        assert settings.rca_confidence_threshold == 0.7
        assert settings.rca_max_retries == 1
        assert settings.backend_port == 8000
        assert settings.anthropic_api_key == ""
        assert settings.openai_api_key == ""

    def test_default_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        settings = Settings(_env_file=None)
        assert "sqlite" in settings.database_url


class TestEnvOverrides:
    def test_anthropic_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
        settings = Settings(_env_file=None)
        assert settings.anthropic_api_key == "sk-ant-abc123"

    def test_llm_provider_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "openai"

    def test_confidence_threshold_from_env_is_coerced_to_float(self, monkeypatch):
        monkeypatch.setenv("RCA_CONFIDENCE_THRESHOLD", "0.55")
        settings = Settings(_env_file=None)
        assert settings.rca_confidence_threshold == 0.55
        assert isinstance(settings.rca_confidence_threshold, float)

    def test_backend_port_from_env_is_coerced_to_int(self, monkeypatch):
        monkeypatch.setenv("BACKEND_PORT", "9999")
        settings = Settings(_env_file=None)
        assert settings.backend_port == 9999
        assert isinstance(settings.backend_port, int)

    def test_debug_flag_from_env_is_coerced_to_bool(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "false")
        settings = Settings(_env_file=None)
        assert settings.debug is False


class TestExtraFieldsIgnored:
    """The regression this whole test class exists for: before the fix,
    Settings() raised ValidationError the moment .env contained the
    frontend's NEXT_PUBLIC_* vars — which it always does, since .env is
    shared between backend and frontend."""

    def test_frontend_vars_do_not_crash_settings(self, monkeypatch):
        monkeypatch.setenv("NEXT_PUBLIC_API_URL", "http://localhost:8000")
        monkeypatch.setenv("NEXT_PUBLIC_WS_URL", "ws://localhost:8000")
        settings = Settings(_env_file=None)  # must not raise
        assert settings.backend_port == 8000

    def test_arbitrary_unknown_env_var_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("SOME_TOTALLY_UNRELATED_VAR", "whatever")
        Settings(_env_file=None)  # must not raise


class TestSingleton:
    def test_get_settings_returns_same_instance(self):
        a = get_settings()
        b = get_settings()
        assert a is b

    def test_get_settings_reflects_env_at_first_call_only(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        a = get_settings()
        assert a.llm_provider == "openai"
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        b = get_settings()  # cached — does not re-read env
        assert b.llm_provider == "openai"
        assert a is b

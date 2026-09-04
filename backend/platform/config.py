"""Application configuration.

Reads from environment variables. All integrations fall back to mock
clients when credentials are absent.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — all fields have safe defaults."""

    # App
    app_name: str = "Autonomous Incident Response"
    app_env: str = "development"
    debug: bool = True

    # Backend
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # Database
    database_url: str = "sqlite+aiosqlite:///./incidents.db"

    # LLM — Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # LLM — OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Which provider to use as primary: "anthropic" | "openai"
    llm_provider: str = "anthropic"

    # Incident pipeline
    rca_confidence_threshold: float = 0.7
    rca_max_retries: int = 1

    # Severity thresholds
    approval_timeout_minutes: int = 15

    # Whether /incidents/trigger should drive the real Docker/Toxiproxy
    # environment instead of mock signals + mock remediation.
    #   auto -> use real env only when the IRAS service stack is running
    #   on   -> always use real env (fail loudly if unavailable)
    #   off  -> always stay in mock mode
    real_env: str = "auto"

    # Optional API key for the mutating endpoints that drive the real
    # Docker/Toxiproxy environment (/faults/inject, /remediation/execute,
    # /incidents/trigger, approve/reject).  When set, callers must send it in
    # the `X-API-Key` header.  Empty (default) keeps auth disabled so local
    # dev and the test suite are unaffected (issue #31).
    api_key: str = ""

    # Comma-separated list of allowed browser origins for CORS.  The API is
    # cross-origin for the dashboard (frontend on :3000, API on :8000) but
    # must NOT be wildcard: endpoints drive real infra, so arbitrary websites
    # must not be able to call them from a browser (issue #31).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # extra="ignore": .env is shared with the frontend (NEXT_PUBLIC_* vars)
    # and isn't backend config — reject only unknown *backend* keys, not those.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

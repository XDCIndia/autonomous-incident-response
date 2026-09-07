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
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"

    # Real URL monitoring (issue #36): how often the background loop checks
    # every monitoring_enabled MonitoredTarget, and how many CONSECUTIVE
    # failed checks before it creates a real Incident. Deliberately
    # deterministic, not LLM-based — see backend/monitoring/url_monitor.py.
    url_monitor_interval_seconds: float = 15.0
    url_monitor_failure_threshold: int = 3

    # Abuse protection for POST /targets (issue: rate limiting). Without
    # authentication configured (API_KEY empty — the local-dev default),
    # this is the only endpoint that lets any caller make the backend
    # repeatedly send outbound HTTP requests to arbitrary URLs on a
    # recurring schedule. Both limits are appropriate ONLY for a single
    # backend process — see docs/REAL_MONITORING_PLAN.md Phase 5, which
    # revisits this once there is more than one replica.
    #   target_creation_rate_limit / _window_seconds: sliding-window cap on
    #     how many targets one client (by IP) may create per window.
    #   max_monitored_targets: hard ceiling on total targets, independent of
    #     creation rate over time — this is what actually bounds the
    #     background monitor loop's total outbound request volume per tick,
    #     since check frequency itself is a global setting, not per-caller.
    target_creation_rate_limit: int = 10
    target_creation_rate_window_seconds: float = 60.0
    max_monitored_targets: int = 200

    # Dashboard login (Phase 5). A single shared password gating the UI —
    # not a per-user account system (that's a separate, later phase). Empty
    # (default) disables login entirely: every page and endpoint behaves
    # exactly as it did before this feature existed, so existing local dev
    # and every existing test is unaffected. Set AUTH_PASSWORD to require a
    # session (established via POST /auth/login) on every endpoint that
    # doesn't already accept a valid X-API-Key — session and API-key auth
    # are independent and either one satisfies the same gate, so existing
    # API_KEY-based programmatic/API clients keep working without logging in.
    auth_password: str = ""
    session_ttl_minutes: int = 480
    session_cookie_name: str = "sb_session"

    # extra="ignore": .env is shared with the frontend (NEXT_PUBLIC_* vars)
    # and isn't backend config — reject only unknown *backend* keys, not those.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

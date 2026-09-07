"""In-process session store for dashboard login (Phase 5).

Deliberately a plain in-memory store — the same accepted single-process
tradeoff already used for POST /targets rate limiting
(backend/platform/rate_limiter.py) and for pending-approval tracking
(IncidentOrchestrator._approval_events). Revisited if this backend ever
runs more than one replica (see docs/REAL_MONITORING_PLAN.md Phase 5:
platform hardening).

This is intentionally a single shared credential, not a per-user account
system — there is exactly one password (Settings.auth_password), and a
successful login just proves "this browser knows the shared password."
Multi-user accounts/RBAC are explicitly a separate, later phase.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone


class SessionStore:
    """Opaque, unguessable session tokens with a server-side expiry.

    The token itself IS the secret (secrets.token_urlsafe — 256 bits of
    entropy) — there is no need to additionally sign or HMAC it, since
    possession of the token is the only thing that is ever checked, and an
    attacker who doesn't already have it cannot feasibly guess it.
    """

    def __init__(self, ttl_minutes: int = 480):
        self._ttl = timedelta(minutes=ttl_minutes)
        self._sessions: dict[str, datetime] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = datetime.now(timezone.utc) + self._ttl
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        expires_at = self._sessions.get(token)
        if expires_at is None:
            return False
        if datetime.now(timezone.utc) >= expires_at:
            del self._sessions[token]
            return False
        return True

    def invalidate(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

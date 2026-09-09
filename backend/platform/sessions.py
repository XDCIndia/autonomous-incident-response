"""In-process session store for dashboard login.

Deliberately a plain in-memory store — the same accepted single-process
tradeoff already used for POST /targets rate limiting
(backend/platform/rate_limiter.py) and for pending-approval tracking
(IncidentOrchestrator._approval_events). Revisited if this backend ever
runs more than one replica (see docs/REAL_MONITORING_PLAN.md Phase 5:
platform hardening).

Each session token is bound to the user id it was created for, so
``/auth/session`` can answer "who is logged in", not just "is someone
logged in". Sessions are lost on process restart — users simply sign in
again; the accounts themselves live in SQLite (backend.platform.users).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class _Session:
    user_id: str
    expires_at: datetime


class SessionStore:
    """Opaque, unguessable session tokens with a server-side expiry.

    The token itself IS the secret (secrets.token_urlsafe — 256 bits of
    entropy) — there is no need to additionally sign or HMAC it, since
    possession of the token is the only thing that is ever checked, and an
    attacker who doesn't already have it cannot feasibly guess it.
    """

    def __init__(self, ttl_minutes: int = 480):
        self._ttl = timedelta(minutes=ttl_minutes)
        self._sessions: dict[str, _Session] = {}

    def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + self._ttl,
        )
        return token

    def is_valid(self, token: str | None) -> bool:
        return self.get_user_id(token) is not None

    def get_user_id(self, token: str | None) -> str | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if datetime.now(timezone.utc) >= session.expires_at:
            del self._sessions[token]
            return None
        return session.user_id

    def invalidate(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

"""Hermetic unit tests for the in-process session store (multi-user auth)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.platform.sessions import SessionStore

USER_ID = "user-1"


class TestCreateAndValidate:
    def test_freshly_created_token_is_valid(self):
        store = SessionStore(ttl_minutes=10)
        token = store.create(USER_ID)
        assert store.is_valid(token) is True
        assert store.get_user_id(token) == USER_ID

    def test_unknown_token_is_invalid(self):
        store = SessionStore(ttl_minutes=10)
        assert store.is_valid("not-a-real-token") is False
        assert store.get_user_id("not-a-real-token") is None

    def test_none_token_is_invalid(self):
        store = SessionStore(ttl_minutes=10)
        assert store.is_valid(None) is False
        assert store.get_user_id(None) is None

    def test_empty_string_token_is_invalid(self):
        store = SessionStore(ttl_minutes=10)
        assert store.is_valid("") is False

    def test_two_created_tokens_are_distinct_and_unguessable(self):
        store = SessionStore(ttl_minutes=10)
        a = store.create(USER_ID)
        b = store.create("user-2")
        assert a != b
        assert len(a) > 30  # real entropy, not a short/guessable id
        assert store.get_user_id(b) == "user-2"


class TestExpiry:
    def test_expired_token_is_invalid_and_removed(self):
        store = SessionStore(ttl_minutes=10)
        token = store.create(USER_ID)
        # Force expiry without waiting real time.
        store._sessions[token].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert store.is_valid(token) is False
        # Removed, not just reported invalid — re-checking must not resurrect it.
        assert token not in store._sessions

    def test_token_still_valid_just_before_expiry(self):
        store = SessionStore(ttl_minutes=10)
        token = store.create(USER_ID)
        store._sessions[token].expires_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert store.is_valid(token) is True


class TestInvalidate:
    def test_invalidate_removes_a_valid_session(self):
        store = SessionStore(ttl_minutes=10)
        token = store.create(USER_ID)
        store.invalidate(token)
        assert store.is_valid(token) is False

    def test_invalidate_none_does_not_raise(self):
        store = SessionStore(ttl_minutes=10)
        store.invalidate(None)  # must not raise

    def test_invalidate_unknown_token_does_not_raise(self):
        store = SessionStore(ttl_minutes=10)
        store.invalidate("never-existed")  # must not raise

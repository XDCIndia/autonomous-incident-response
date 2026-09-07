"""Hermetic unit tests for SlidingWindowRateLimiter (Phase 1c: rate
limiting / abuse protection). No network, no FastAPI — pure logic, with a
fake monotonic clock so window-expiry behavior is deterministic instead of
depending on real wall-clock sleeps.
"""

from __future__ import annotations

import pytest

from backend.platform.rate_limiter import SlidingWindowRateLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestConstructorValidation:
    def test_rejects_non_positive_max_requests(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=0, window_seconds=60.0)

    def test_rejects_non_positive_window(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=5, window_seconds=0.0)


class TestSlidingWindowBehavior:
    def test_allows_up_to_the_limit(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)

        assert limiter.allow("a") is True
        assert limiter.allow("a") is True
        assert limiter.allow("a") is True

    def test_blocks_once_limit_exceeded_within_window(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)

        for _ in range(3):
            assert limiter.allow("a") is True
        assert limiter.allow("a") is False

    def test_a_blocked_call_does_not_itself_count_as_a_hit(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        assert limiter.allow("a") is False  # still blocked, not accidentally re-armed

    def test_old_hits_expire_out_of_the_window(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10.0)

        assert limiter.allow("a") is True
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False  # limit reached

        clock.advance(10.1)  # both earlier hits are now outside the window
        assert limiter.allow("a") is True

    def test_keys_are_tracked_independently(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

        assert limiter.allow("client-a") is True
        assert limiter.allow("client-b") is True  # independent bucket
        assert limiter.allow("client-a") is False
        assert limiter.allow("client-b") is False


class TestReset:
    def test_reset_specific_key(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

        limiter.allow("a")
        assert limiter.allow("a") is False
        limiter.reset("a")
        assert limiter.allow("a") is True

    def test_reset_all_keys(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("backend.platform.rate_limiter.time.monotonic", clock)
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

        limiter.allow("a")
        limiter.allow("b")
        limiter.reset()
        assert limiter.allow("a") is True
        assert limiter.allow("b") is True

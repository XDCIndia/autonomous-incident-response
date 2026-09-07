"""In-process rate limiting.

Deliberately a plain in-memory sliding window — appropriate ONLY for a
single backend process. If this backend ever runs more than one replica,
each replica would enforce its own independent limit (see
docs/REAL_MONITORING_PLAN.md Phase 5, which revisits this with a shared
store once that's a real deployment shape). That's an explicit, accepted
tradeoff for the current architecture, not an oversight.
"""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter: at most ``max_requests`` calls to
    ``allow(key)`` returning True within any ``window_seconds`` interval,
    tracked independently per key.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """True and records the hit if ``key`` is still under its limit;
        False (and does NOT record a hit) if it would exceed it."""
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())
        while window and now - window[0] > self._window_seconds:
            window.popleft()
        if len(window) >= self._max_requests:
            return False
        window.append(now)
        return True

    def reset(self, key: str | None = None) -> None:
        """Clear tracked hits — for a specific key, or every key. Test/ops
        convenience only; production code never needs this."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)

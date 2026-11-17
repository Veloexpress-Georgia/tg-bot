"""In-process request limits. The API runs as a single instance."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Allow at most `limit` events per key in any sliding `window` seconds."""

    def __init__(
        self,
        *,
        limit: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[int, deque[float]] = {}

    def allow(self, key: int) -> bool:
        now = self._clock()
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self._max_keys:
                self._prune(now)
            events = self._events[key] = deque()
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._events.items() if not v or now - v[-1] >= self.window]:
            del self._events[key]

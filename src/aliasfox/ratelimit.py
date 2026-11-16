import threading
import time
from collections import deque
from collections.abc import Callable

_SWEEP_THRESHOLD = 1024


class RateLimiter:
    """Sliding-window limiter: at most `max_events` per `window_seconds` per key."""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float] | None:
        q = self._events.get(key)
        if q is None:
            return None
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._events[key]
            return None
        return q

    def _sweep(self, now: float) -> None:
        if len(self._events) > _SWEEP_THRESHOLD:
            for k in list(self._events):
                self._prune(k, now)

    def hit(self, key: str) -> bool:
        """Record an event. True if allowed (and recorded), False if over the limit."""
        with self._lock:
            now = self._clock()
            self._sweep(now)
            q = self._prune(key, now)
            if q is not None and len(q) >= self.max_events:
                return False
            if self.max_events <= 0:
                return False
            self._events.setdefault(key, deque()).append(now)
            return True

    def release(self, key: str) -> None:
        """Give back the most recent event for the key (undo a reservation)."""
        with self._lock:
            q = self._events.get(key)
            if q:
                q.pop()
                if not q:
                    del self._events[key]

    def retry_after(self, key: str) -> int:
        """Whole seconds until the key has room again (0 if it already does)."""
        with self._lock:
            now = self._clock()
            q = self._prune(key, now)
            if q is None or len(q) < self.max_events:
                return 0
            return max(1, int(q[0] + self.window - now + 0.999))

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

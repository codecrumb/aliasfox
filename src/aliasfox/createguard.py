import math
import threading
import time
from collections.abc import Callable

from aliasfox.ratelimit import RateLimiter


class CreateGuard:
    """One create budget shared by the web UI and API keys: a per-user and a
    global sliding window (units are limiter hits), plus a per-user cooldown
    after consecutive Apple errors."""

    _GLOBAL_KEY = "global"

    def __init__(
        self,
        *,
        per_user: int = 10,
        global_per_minute: int = 120,
        cooldown_after: int = 3,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._after = cooldown_after
        self._seconds = cooldown_seconds
        self._user = RateLimiter(per_user, 60, clock=clock)
        self._global = RateLimiter(global_per_minute, 60, clock=clock)
        self.limiters = [self._user, self._global]
        self._lock = threading.Lock()
        self._cool: dict[int, tuple[int, float]] = {}  # user -> (consecutive errors, expiry); cooling once errors >= cooldown_after

    def check(self, user_id: int) -> int | None:
        """Record one hit. None = allowed, else Retry-After seconds (>= 1)."""
        with self._lock:
            n, until = self._cool.get(user_id, (0, 0.0))
            left = until - self._clock() if n >= self._after else 0
        if left > 0:
            return max(1, math.ceil(left))
        key = str(user_id)
        if not self._user.hit(key):
            return self._user.retry_after(key) or 1
        if not self._global.hit(self._GLOBAL_KEY):
            self._user.release(key)
            return self._global.retry_after(self._GLOBAL_KEY) or 1
        return None

    def record_error(self, user_id: int) -> None:
        with self._lock:
            now = self._clock()
            n, until = self._cool.get(user_id, (0, 0.0))
            if until <= now:
                n = 0  # stale count: not consecutive any more
            elif n >= self._after:
                return  # already cooling down: never extend
            self._cool[user_id] = (n + 1, now + self._seconds)

    def record_success(self, user_id: int) -> None:
        with self._lock:
            self._cool.pop(user_id, None)

    def reset_user(self, user_id: int) -> None:
        """Forget the user's budget and cooldown (account deleted; ids get reused)."""
        self._user.reset(str(user_id))
        self.record_success(user_id)

    def sweep(self) -> None:
        with self._lock:
            now = self._clock()
            for u, (_, until) in list(self._cool.items()):
                if until <= now:
                    del self._cool[u]

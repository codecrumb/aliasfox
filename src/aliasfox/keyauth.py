"""Authenticate requests by per-user API key. Never logs keys, headers or hashes."""
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from aliasfox.apikeys import ApiKeyStore, KeyInfo
from aliasfox.auth import USER_ID, client_ip, limiter_key
from aliasfox.config import Settings
from aliasfox.ratelimit import RateLimiter

log = logging.getLogger(__name__)

CACHE_MAX = 1024
TOUCH_INTERVAL = 300.0
FAILED_PER_MINUTE = 20
PER_KEY_PER_MINUTE = 120
MAX_KEY_LEN = 256  # real keys are 47 chars; longer headers are garbage, not worth hashing


class KeyAuthError(Exception):
    def __init__(self, status: int, detail: str, needed: str | None = None, retry_after: int | None = None):
        super().__init__(detail)
        self.status, self.detail, self.needed, self.retry_after = status, detail, needed, retry_after


def _presented_key(request: Request) -> str | None:
    value = request.headers.get("authorization")
    if value is not None:
        scheme, _, token = value.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return request.headers.get("authentication") or None


class KeyAuth:
    def __init__(
        self,
        store: ApiKeyStore,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl: float = 30.0,
    ) -> None:
        self._store, self._settings, self._clock, self._ttl = store, settings, clock, cache_ttl
        self._lock = threading.Lock()
        self._cache: dict[bytes, tuple[KeyInfo, float]] = {}  # hmac -> (info, cached_at)
        self._touched: dict[int, float] = {}
        self._gen = 0  # bumped by invalidate*; stops an in-flight lookup re-caching a revoked key
        self._failed = RateLimiter(FAILED_PER_MINUTE, 60, clock=clock)
        self._per_key = RateLimiter(PER_KEY_PER_MINUTE, 60, clock=clock)
        self.limiters: "list[RateLimiter]" = [self._failed, self._per_key]

    def _resolve(self, key: str) -> KeyInfo | None:
        if len(key) > MAX_KEY_LEN:
            return None
        now = self._clock()
        try:
            digest = self._store.digest(key)
        except UnicodeEncodeError:
            return None
        with self._lock:
            hit = self._cache.get(digest)
        if hit is not None and now - hit[1] < self._ttl:
            info = hit[0]
            if info.expires_at is None or info.expires_at > datetime.now(timezone.utc):
                return info
        with self._lock:
            gen = self._gen
        info = self._store.lookup(key)
        with self._lock:
            if info is None:
                self._cache.pop(digest, None)
            elif gen == self._gen:
                if len(self._cache) >= CACHE_MAX and digest not in self._cache:
                    self._drop_stale(now)
                    if len(self._cache) >= CACHE_MAX:
                        self._cache.pop(next(iter(self._cache)))  # oldest insertion
                self._cache[digest] = (info, now)
        return info

    def _drop_stale(self, now: float) -> int:  # caller holds the lock
        stale = [d for d, (_, t) in self._cache.items() if now - t >= self._ttl]
        for d in stale:
            del self._cache[d]
        return len(stale)

    def authenticate(self, request: Request, scope: str | None) -> KeyInfo:
        if not self._settings.api_keys_enabled:
            raise KeyAuthError(503, "api_keys_disabled")
        ip = limiter_key(client_ip(
            request, trusted_proxy=self._settings.trusted_proxy, hops=self._settings.trusted_proxy_hops
        ))
        if (wait := self._failed.retry_after(ip)) > 0:
            raise KeyAuthError(429, "too_many_attempts", retry_after=wait)
        key = _presented_key(request)
        if key is None:
            raise KeyAuthError(401, "invalid_api_key")
        info = self._resolve(key)
        if info is not None and self._settings.aliasfox_mode != "multi" and info.user_id != USER_ID:
            info = None  # single mode: like cookies, only the owner; leftover users' keys are dead
        if info is None:
            self._failed.hit(ip)
            raise KeyAuthError(401, "invalid_api_key")
        kid = str(info.id)
        if not self._per_key.hit(kid):
            raise KeyAuthError(429, "rate_limited", retry_after=self._per_key.retry_after(kid))
        if scope is not None and scope not in info.scopes:
            raise KeyAuthError(403, "insufficient_scope", needed=scope)
        self._maybe_touch(info.id)
        return info

    def _maybe_touch(self, key_id: int) -> None:
        now = self._clock()
        with self._lock:
            last = self._touched.get(key_id)
            if last is not None and now - last < TOUCH_INTERVAL:
                return
            self._touched[key_id] = now
        try:
            self._store.touch(key_id)
        except Exception as exc:  # last-used is cosmetic; never fail the request
            log.warning("api key touch failed: %s", type(exc).__name__)

    def require(self, scope: str | None = None) -> Callable[[Request], KeyInfo]:
        def dependency(request: Request) -> KeyInfo:
            try:
                return self.authenticate(request, scope)
            except KeyAuthError as e:
                headers = {"Retry-After": str(max(1, e.retry_after or 0))} if e.status == 429 else None
                detail = {"error": e.detail, "needed": e.needed} if e.needed else e.detail  # body: {"detail": ...}
                raise HTTPException(e.status, detail, headers=headers) from None

        return dependency

    def invalidate(self, key_id: int) -> None:
        with self._lock:
            self._gen += 1
            for d in [d for d, (i, _) in self._cache.items() if i.id == key_id]:
                del self._cache[d]
            self._touched.pop(key_id, None)

    def invalidate_user(self, user_id: int) -> None:
        with self._lock:
            self._gen += 1
            for d, (i, _) in list(self._cache.items()):
                if i.user_id == user_id:
                    del self._cache[d]
                    self._touched.pop(i.id, None)

    def evict_expired(self) -> int:
        now = self._clock()
        with self._lock:
            for k in [k for k, t in self._touched.items() if now - t >= TOUCH_INTERVAL]:
                del self._touched[k]
            return self._drop_stale(now)

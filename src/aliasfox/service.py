"""Per-user serialized, cached access to Hide My Email.

Each operation takes the user's lock, loads the stored session, builds a fresh
blocking client and runs it in the threadpool. Rotated cookies are persisted
best-effort; aliases are cached in memory only.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from aliasfox.apple.hme import Alias, HmeClient
from aliasfox.sessions import AppleSessionState, SessionStore

log = logging.getLogger(__name__)


class NotConnected(Exception):
    """The user has no stored Apple session."""


class AliasService:
    def __init__(
        self,
        store: SessionStore,
        *,
        client_factory: Callable[..., HmeClient] = HmeClient,
        ttl_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._factory = client_factory
        self._ttl = ttl_seconds
        self._clock = clock
        self._locks: dict[int, asyncio.Lock] = {}
        self._cache: dict[int, tuple[float, list[Alias]]] = {}

    def _lock(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = self._locks[user_id] = asyncio.Lock()
        return lock

    def _persist(self, user_id: int) -> Callable[[AppleSessionState], None]:
        def persist(state: AppleSessionState) -> None:
            try:
                self._store.save(user_id, state)
            except Exception as exc:  # never mask the Apple result
                log.warning("failed to persist session: %s", type(exc).__name__)

        return persist

    def _fresh(self, user_id: int) -> list[Alias] | None:
        hit = self._cache.get(user_id)
        if hit and self._clock() - hit[0] < self._ttl:
            return list(hit[1])
        return None

    async def is_connected(self, user_id: int) -> bool:
        return await run_in_threadpool(self._store.load, user_id) is not None

    async def _run(self, user_id: int, call: Callable[[HmeClient], Any]) -> Any:
        """Run `call(client)` under the user's lock; caller holds no lock."""
        async with self._lock(user_id):
            return await self._run_locked(user_id, call)

    async def _run_locked(self, user_id: int, call: Callable[[HmeClient], Any]) -> Any:
        state = await run_in_threadpool(self._store.load, user_id)
        if state is None:
            raise NotConnected(user_id)
        client = self._factory(state, on_state_change=self._persist(user_id))

        def work() -> Any:
            try:
                return call(client)
            finally:
                try:
                    client.close()
                except Exception as exc:  # never mask the Apple result/error
                    log.warning("failed to close client: %s", type(exc).__name__)

        return await run_in_threadpool(work)

    async def forget(self, user_id: int) -> None:
        """Drop the user's cached alias list (after connect/disconnect)."""
        async with self._lock(user_id):
            self._cache.pop(user_id, None)

    async def _mutate(self, user_id: int, call: Callable[[HmeClient], Any]) -> Any:
        result = await self._run(user_id, call)
        self._cache.pop(user_id, None)
        return result

    async def list_aliases(self, user_id: int, *, refresh: bool = False) -> list[Alias]:
        if not refresh and (cached := self._fresh(user_id)) is not None:
            return cached  # fast path: no lock, so a slow call cannot stall reads
        async with self._lock(user_id):
            if not refresh and (cached := self._fresh(user_id)) is not None:
                return cached
            aliases = await self._run_locked(user_id, lambda c: c.list())
            self._cache[user_id] = (self._clock(), list(aliases))
            return list(aliases)

    async def generate(self, user_id: int) -> str:
        return await self._run(user_id, lambda c: c.generate())

    async def reserve(self, user_id: int, hme: str, label: str, note: str = "") -> Alias:
        return await self._mutate(user_id, lambda c: c.reserve(hme, label, note))

    async def update(
        self, user_id: int, anonymous_id: str, label: str, note: str | None
    ) -> None:
        await self._mutate(user_id, lambda c: c.update_metadata(anonymous_id, label, note))

    async def set_active(self, user_id: int, anonymous_id: str, active: bool) -> None:
        await self._mutate(user_id, lambda c: c.set_active(anonymous_id, active))

    async def delete(self, user_id: int, anonymous_id: str) -> None:
        await self._mutate(user_id, lambda c: c.delete(anonymous_id))

    async def set_forward_to(self, user_id: int, address: str) -> None:
        await self._mutate(user_id, lambda c: c.update_forward_to(address))

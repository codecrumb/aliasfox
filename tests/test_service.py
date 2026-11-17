import asyncio
import logging
import threading
import time

import pytest
from helpers import make_store
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.db import AppleSessionRow, init_db
from aliasfox.service import AliasService, NotConnected
from aliasfox.sessions import AppleSessionState, SessionUnreadable


def make_alias(i=0):
    return Alias(f"id{i}", f"a{i}@icloud.com", "l", "", True, "", "", "", None)


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = 0
        self.max = 0
        self.calls = []
        self.list_count = 0
        self.fail = None
        self.rotate = False
        self.sleep = 0.0

    def enter(self, name):
        with self.lock:
            self.calls.append(name)
            self.current += 1
            self.max = max(self.max, self.current)
        time.sleep(self.sleep)
        with self.lock:
            self.current -= 1
        if self.fail:
            raise self.fail


class FakeClient:
    tracker: Tracker

    def __init__(self, state, *, on_state_change=None):
        self.state = state
        self.on_state_change = on_state_change
        self.threads = set()

    def _do(self, name):
        self.tracker.enter(name)
        if self.tracker.rotate and self.on_state_change:
            self.state.cookies["rotated"] = "new-cookie"
            self.on_state_change(self.state)

    def list(self):
        self._do("list")
        self.tracker.list_count += 1
        return [make_alias(self.tracker.list_count)]

    def generate(self):
        self._do("generate")
        return "x@icloud.com"

    def reserve(self, hme, label, note=""):
        self._do("reserve")
        return make_alias(9)

    def update_metadata(self, anonymous_id, label, note=None):
        self._do("update")

    def set_active(self, anonymous_id, active):
        self._do("set_active")

    def delete(self, anonymous_id):
        self._do("delete")

    def update_forward_to(self, address):
        self._do("forward")

    def close(self):
        pass


@pytest.fixture
def tracker():
    return Tracker()


@pytest.fixture
def store():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(eng)
    st, users = make_store(eng)
    assert users.create("second") == 2
    for uid in (1, 2):
        st.save(uid, AppleSessionState({"c": "v"}, "https://ws.example"))
    return st


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(store, tracker, clock):
    cls = type("F", (FakeClient,), {"tracker": tracker})
    return AliasService(store, client_factory=cls, ttl_seconds=60, clock=clock)


async def test_list_cached_for_ttl_then_refetched(svc, tracker, clock):
    a = await svc.list_aliases(1)
    b = await svc.list_aliases(1)
    assert tracker.list_count == 1 and a == b
    clock.t += 59
    await svc.list_aliases(1)
    assert tracker.list_count == 1
    clock.t += 2
    await svc.list_aliases(1)
    assert tracker.list_count == 2


async def test_cache_is_per_user_and_returns_copy(svc, tracker):
    a = await svc.list_aliases(1)
    a.clear()
    assert len(await svc.list_aliases(1)) == 1
    await svc.list_aliases(2)
    assert tracker.list_count == 2


async def test_refresh_true_bypasses_cache(svc, tracker):
    await svc.list_aliases(1)
    await svc.list_aliases(1, refresh=True)
    assert tracker.list_count == 2


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.reserve(1, "a@icloud.com", "l"),
        lambda s: s.update(1, "id", "l", "n"),
        lambda s: s.set_active(1, "id", False),
        lambda s: s.delete(1, "id"),
        lambda s: s.set_forward_to(1, "me@example.com"),
    ],
)
async def test_mutation_invalidates_cache(svc, tracker, call):
    await svc.list_aliases(1)
    await svc.list_aliases(2)
    await call(svc)
    await svc.list_aliases(1)
    assert tracker.list_count == 3  # user 1 refetched
    await svc.list_aliases(2)
    assert tracker.list_count == 3  # user 2 untouched


async def test_generate_does_not_invalidate_cache(svc, tracker):
    await svc.list_aliases(1)
    assert await svc.generate(1) == "x@icloud.com"
    await svc.list_aliases(1)
    assert tracker.list_count == 1


async def test_failed_mutation_does_not_invalidate_cache(svc, tracker):
    await svc.list_aliases(1)
    tracker.fail = HmeError(5, "nope")
    with pytest.raises(HmeError):
        await svc.delete(1, "id")
    tracker.fail = None
    await svc.list_aliases(1)
    assert tracker.list_count == 1


async def test_session_expired_passes_through(svc, tracker):
    tracker.fail = SessionExpired(401, "session expired")
    with pytest.raises(SessionExpired):
        await svc.list_aliases(1)


async def test_concurrent_generate_calls_are_serialized(svc, tracker):
    tracker.sleep = 0.05
    results = await asyncio.gather(svc.generate(1), svc.generate(1))
    assert results == ["x@icloud.com"] * 2
    assert tracker.max == 1


async def test_different_users_may_overlap(svc, tracker):
    tracker.sleep = 0.1
    await asyncio.gather(svc.generate(1), svc.generate(2))
    assert tracker.max == 2


async def test_fresh_client_per_operation(store, tracker):
    made = []

    def factory(state, *, on_state_change=None):
        c = type("F", (FakeClient,), {"tracker": tracker})(state, on_state_change=on_state_change)
        made.append(c)
        return c

    s = AliasService(store, client_factory=factory)
    await s.generate(1)
    await s.generate(1)
    assert len(made) == 2 and made[0] is not made[1]


async def test_rotated_cookies_saved_to_store(svc, tracker, store):
    tracker.rotate = True
    await svc.generate(1)
    assert store.load(1).cookies["rotated"] == "new-cookie"
    assert "rotated" not in store.load(2).cookies


async def test_persist_failure_does_not_break_operation(store, tracker, clock, caplog):
    class BrokenStore:
        def load(self, user_id):
            return store.load(user_id)

        def save(self, user_id, state):
            raise RuntimeError("db down: super-secret-cookie")

    tracker.rotate = True
    cls = type("F", (FakeClient,), {"tracker": tracker})
    s = AliasService(BrokenStore(), client_factory=cls, clock=clock)
    with caplog.at_level(logging.WARNING):
        assert await s.generate(1) == "x@icloud.com"
    assert "RuntimeError" in caplog.text
    assert "super-secret-cookie" not in caplog.text
    assert "new-cookie" not in caplog.text


async def test_not_connected_raises(svc, tracker):
    with pytest.raises(NotConnected):
        await svc.list_aliases(99)
    with pytest.raises(NotConnected):
        await svc.generate(99)
    assert tracker.calls == []


async def test_cached_list_does_not_wait_for_user_lock(svc, tracker):
    await svc.list_aliases(1)  # warm the cache
    tracker.sleep = 0.4
    slow = asyncio.create_task(svc.generate(1))
    await asyncio.sleep(0.05)  # generate now holds the lock
    start = time.monotonic()
    aliases = await svc.list_aliases(1)
    elapsed = time.monotonic() - start
    assert len(aliases) == 1
    assert elapsed < 0.2
    assert not slow.done()
    await slow


async def test_stale_cache_rechecked_inside_lock(svc, tracker):
    tracker.sleep = 0.1
    await asyncio.gather(svc.list_aliases(1), svc.list_aliases(1))
    assert tracker.list_count == 1


async def test_session_load_runs_off_the_event_loop(store, tracker):
    threads = []

    class SpyStore:
        def load(self, user_id):
            threads.append(threading.current_thread())
            return store.load(user_id)

        def save(self, user_id, state):
            store.save(user_id, state)

    cls = type("F", (FakeClient,), {"tracker": tracker})
    s = AliasService(SpyStore(), client_factory=cls)
    await s.generate(1)
    assert threads and threads[0] is not threading.main_thread()


async def test_close_failure_does_not_mask_apple_error(store, tracker, caplog):
    class BadClose(FakeClient):
        def close(self):
            raise RuntimeError("close boom: super-secret-cookie")

    tracker.fail = SessionExpired(401, "session expired")
    s = AliasService(store, client_factory=type("F", (BadClose,), {"tracker": tracker}))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SessionExpired):
            await s.generate(1)
    assert "RuntimeError" in caplog.text
    assert "super-secret-cookie" not in caplog.text


async def test_close_failure_does_not_break_success(store, tracker):
    class BadClose(FakeClient):
        def close(self):
            raise RuntimeError("boom")

    s = AliasService(store, client_factory=type("F", (BadClose,), {"tracker": tracker}))
    assert await s.generate(1) == "x@icloud.com"


async def test_is_connected(svc):
    assert await svc.is_connected(1) is True
    assert await svc.is_connected(99) is False


async def test_forget_drops_list_cache_only_for_that_user():
    from aliasfox.service import AliasService

    svc = AliasService(object())
    svc._cache[1] = (0.0, [])
    svc._cache[2] = (0.0, [])
    await svc.forget(1)
    assert 1 not in svc._cache and 2 in svc._cache


# --- B1: session writes through the service, generation guard -----------------------


def _state(**cookies):
    return AppleSessionState({"c": "v", **cookies}, "https://ws.example")


async def test_replace_session_saves_and_drops_cache(svc, store, tracker):
    await svc.list_aliases(1)
    assert tracker.list_count == 1
    await svc.replace_session(1, _state(new="1"))
    assert store.load(1).cookies == {"c": "v", "new": "1"}
    await svc.list_aliases(1)
    assert tracker.list_count == 2  # cache dropped


async def test_disconnect_deletes_and_drops_cache(svc, store, tracker):
    await svc.list_aliases(1)
    await svc.disconnect(1)
    assert store.load(1) is None
    with pytest.raises(NotConnected):
        await svc.list_aliases(1)
    await svc.disconnect(1)  # idempotent


async def test_persist_callback_skips_after_disconnect(store, tracker, clock):
    clients = []

    class Capture(FakeClient):
        tracker = None

        def __init__(self, state, *, on_state_change=None):
            super().__init__(state, on_state_change=on_state_change)
            clients.append(self)

    Capture.tracker = tracker
    svc = AliasService(store, client_factory=Capture, clock=clock)
    await svc.list_aliases(1)
    stale = clients[0]
    await svc.disconnect(1)
    stale.on_state_change(_state(stale="ROTATED"))
    assert store.load(1) is None


async def test_persist_callback_skips_after_replace(store, tracker, clock):
    clients = []

    class Capture(FakeClient):
        def __init__(self, state, *, on_state_change=None):
            super().__init__(state, on_state_change=on_state_change)
            clients.append(self)

    Capture.tracker = tracker
    svc = AliasService(store, client_factory=Capture, clock=clock)
    await svc.list_aliases(1)
    await svc.replace_session(1, _state(fresh="1"))
    clients[0].on_state_change(_state(stale="ROTATED"))
    assert store.load(1).cookies == {"c": "v", "fresh": "1"}


async def test_persist_still_saves_when_generation_unchanged(svc, store, tracker):
    tracker.rotate = True
    await svc.list_aliases(1)
    assert store.load(1).cookies["rotated"] == "new-cookie"


async def test_concurrent_disconnect_beats_slow_rotating_call(svc, store, tracker):
    tracker.rotate = True
    tracker.sleep = 0.2
    slow = asyncio.create_task(svc.list_aliases(1, refresh=True))
    await asyncio.sleep(0.05)  # the call is in flight
    await svc.disconnect(1)
    await slow
    assert store.load(1) is None


async def test_concurrent_reconnect_beats_slow_rotating_call(svc, store, tracker):
    tracker.rotate = True
    tracker.sleep = 0.2
    slow = asyncio.create_task(svc.list_aliases(1, refresh=True))
    await asyncio.sleep(0.05)
    await svc.replace_session(1, _state(fresh="1"))
    await slow
    assert store.load(1).cookies == {"c": "v", "fresh": "1"}


async def test_unreadable_session_raises_session_unreadable(store, tracker, clock):
    with store._sessions.begin() as s:  # corrupt the stored blob
        s.get(AppleSessionRow, 1).blob = b"\x00" * 64
    cls = type("F", (FakeClient,), {"tracker": tracker})
    svc = AliasService(store, client_factory=cls, clock=clock)
    with pytest.raises(SessionUnreadable):
        await svc.list_aliases(1)
    with pytest.raises(SessionUnreadable):
        await svc.is_connected(1)
    # a reconnect repairs it
    await svc.replace_session(1, _state())
    assert await svc.is_connected(1) is True


async def test_evict_expired_drops_only_entries_past_ttl(svc, clock):
    await svc.list_aliases(1)
    clock.t += 30
    await svc.list_aliases(2)
    clock.t += 31  # user 1's list is 61 s old, user 2's 31 s
    assert svc.evict_expired() == 1
    assert 1 not in svc._cache and 2 in svc._cache
    clock.t += 30
    assert svc.evict_expired() == 1
    assert svc._cache == {}

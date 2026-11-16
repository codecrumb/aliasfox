import asyncio
import logging
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.db import init_db
from aliasfox.service import AliasService, NotConnected
from aliasfox.sessions import AppleSessionState, SessionStore

KEY = b"k" * 32


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
    st = SessionStore(eng, KEY)
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

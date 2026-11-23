import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aliasfox.apikeys import ApiKeyStore
from aliasfox.config import Settings
from aliasfox.db import init_db, make_engine
from aliasfox.keyauth import KeyAuth
from aliasfox.keys import KeyRing
from aliasfox.users import UserDirectory

SECRET = "s" * 32


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Env:
    def __init__(self, tmp_path, **settings_kw):
        self.engine = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
        init_db(self.engine)
        self.uid = UserDirectory(self.engine, KeyRing(SECRET)).create("sub-a")
        self.store = ApiKeyStore(self.engine, SECRET)
        self.clock = Clock()
        self.auth = KeyAuth(self.store, Settings(secret_key=SECRET, data_dir=tmp_path, **settings_kw), clock=self.clock)
        app = FastAPI()

        @app.get("/r")
        def r(info=Depends(self.auth.require("read"))):
            return {"id": info.id}

        @app.get("/any")
        def any_(info=Depends(self.auth.require())):
            return {"id": info.id}

        self.client = TestClient(app)

    def key(self, scopes=("read",), **kw):
        return self.store.create(self.uid, "k", scopes, **kw)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_missing_garbage_and_valid_headers(env):
    key, _ = env.key()
    for headers in [{}, {"Authorization": "Bearer"}, {"Authorization": "Bearer x"},
                    {"Authorization": "Bearer " + "a" * 10_000}, {"Authentication": "é".encode() * 5},
                    {"Authorization": "Basic " + key}]:
        r = env.client.get("/r", headers=headers)
        assert (r.status_code, r.json()) == (401, {"detail": "invalid_api_key"}), headers
    assert env.client.get("/r", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert env.client.get("/r", headers={"Authentication": key}).status_code == 200


def test_query_string_key_rejected(env):
    key, _ = env.key()
    assert env.client.get("/r", params={"key": key, "api_key": key}).status_code == 401


def test_cookie_alone_is_401(env):
    key, _ = env.key()
    env.client.cookies.set("aliasfox_session", key)
    env.client.cookies.set("Authorization", key)
    assert env.client.get("/r").status_code == 401


def test_insufficient_scope_403_names_needed_scope(env):
    key, _ = env.key(["create"])
    r = env.client.get("/r", headers={"Authentication": key})
    assert r.status_code == 403
    assert r.json() == {"detail": {"error": "insufficient_scope", "needed": "read"}}
    assert env.client.get("/any", headers={"Authentication": key}).status_code == 200


def test_revoke_plus_invalidate_is_immediate(env):
    key, info = env.key()
    h = {"Authentication": key}
    assert env.client.get("/r", headers=h).status_code == 200
    env.store.revoke(env.uid, info.id)
    assert env.client.get("/r", headers=h).status_code == 200  # still cached
    env.auth.invalidate(info.id)
    assert env.client.get("/r", headers=h).status_code == 401


def test_invalidate_user_is_immediate(env):
    key, _ = env.key()
    h = {"Authentication": key}
    assert env.client.get("/r", headers=h).status_code == 200
    env.store.delete_for_user(env.uid)
    env.auth.invalidate_user(env.uid)
    assert env.client.get("/r", headers=h).status_code == 401


def test_cache_ttl_expiry_rechecks_db(env):
    key, info = env.key()
    h = {"Authentication": key}
    assert env.client.get("/r", headers=h).status_code == 200
    env.store.revoke(env.uid, info.id)
    env.clock.t += 29
    assert env.client.get("/r", headers=h).status_code == 200
    env.clock.t += 2
    assert env.client.get("/r", headers=h).status_code == 401


def test_touch_throttled_to_once_per_300s(env, monkeypatch):
    key, _ = env.key()
    calls = []
    monkeypatch.setattr(env.store, "touch", calls.append)
    h = {"Authentication": key}
    for _ in range(3):
        env.client.get("/r", headers=h)
    assert len(calls) == 1
    env.clock.t += 301
    env.client.get("/r", headers=h)
    assert len(calls) == 2


def test_failed_attempts_limit_21st_is_429(env):
    for i in range(20):
        assert env.client.get("/r", headers={"Authentication": f"bad{i}"}).status_code == 401
    r = env.client.get("/r", headers={"Authentication": "bad-again"})
    assert (r.status_code, r.json()) == (429, {"detail": "too_many_attempts"})
    assert int(r.headers["Retry-After"]) >= 1
    key, _ = env.key()  # blocked even with a valid key until the window passes
    assert env.client.get("/r", headers={"Authentication": key}).status_code == 429
    env.clock.t += 61
    assert env.client.get("/r", headers={"Authentication": key}).status_code == 200


def test_per_key_121st_request_is_429_with_retry_after(env):
    key, _ = env.key()
    h = {"Authentication": key}
    for _ in range(120):
        assert env.client.get("/r", headers=h).status_code == 200
    r = env.client.get("/r", headers=h)
    assert (r.status_code, r.json()) == (429, {"detail": "rate_limited"})
    assert int(r.headers["Retry-After"]) >= 1


def test_disabled_returns_503_api_keys_disabled(tmp_path):
    e = Env(tmp_path, api_keys_enabled=False)
    key, _ = e.key()
    r = e.client.get("/r", headers={"Authentication": key})
    assert (r.status_code, r.json()) == (503, {"detail": "api_keys_disabled"})


def test_expired_key_401(env):
    from datetime import datetime, timedelta, timezone

    key, _ = env.key(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert env.client.get("/r", headers={"Authentication": key}).status_code == 401


def test_cache_is_bounded_and_evicts(env):
    from aliasfox import keyauth

    keys = [env.key()[0] for _ in range(3)]
    keyauth.CACHE_MAX, old = 2, keyauth.CACHE_MAX
    try:
        for k in keys:
            env.client.get("/r", headers={"Authentication": k})
        assert len(env.auth._cache) == 2
    finally:
        keyauth.CACHE_MAX = old
    env.clock.t += 31
    assert env.auth.evict_expired() == 2 and not env.auth._cache


def test_429_retry_after_is_at_least_one_even_when_window_slid(env, monkeypatch):
    key, _ = env.key()
    h = {"Authentication": key}
    for _ in range(120):
        assert env.client.get("/r", headers=h).status_code == 200
    monkeypatch.setattr(env.auth._per_key, "retry_after", lambda k: 0)
    r = env.client.get("/r", headers=h)
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"


def test_invalidate_during_inflight_lookup_does_not_recache_revoked_key(env, monkeypatch):
    key, info = env.key()
    h = {"Authentication": key}
    real = env.store.lookup

    def racing(k):
        found = real(k)
        env.store.revoke(env.uid, info.id)
        env.auth.invalidate(info.id)  # revoke lands while this lookup is in flight
        return found

    monkeypatch.setattr(env.store, "lookup", racing)
    env.client.get("/r", headers=h)
    monkeypatch.undo()
    assert env.client.get("/r", headers=h).status_code == 401

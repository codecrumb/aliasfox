import base64

import pytest
from fastapi.testclient import TestClient

import aliasfox.main as main_mod
from aliasfox.config import Settings
from aliasfox.crypto import derive_cache_key, derive_key, hash_password

SECRET = "session-test-secret-key-0123456789abcdef"
OTHER = "another-session-secret-0123456789abcdef"
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def pw_hash():
    return hash_password(PASSWORD)


def make(tmp_path, secret=SECRET, **kw):
    settings = Settings(secret_key=secret, data_dir=tmp_path, **kw)
    return TestClient(main_mod.create_app(settings), base_url="http://localhost")


def authed(tmp_path, pw_hash, secret=SECRET):
    c = make(tmp_path, secret, auth_enabled=True, password_hash=pw_hash)
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def decode(key: str) -> bytes:
    assert "=" not in key
    return base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))


def test_session_requires_auth(tmp_path, pw_hash):
    c = make(tmp_path, auth_enabled=True, password_hash=pw_hash)
    r = c.get("/api/session")
    assert r.status_code == 401 and "cache_key" not in r.text


def test_session_returns_stable_key_per_epoch_and_changes_after_logout(tmp_path, pw_hash):
    c = authed(tmp_path, pw_hash)
    k1 = c.get("/api/session").json()["cache_key"]
    assert c.get("/api/session").json()["cache_key"] == k1
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    k2 = c.get("/api/session").json()["cache_key"]
    assert k2 != k1
    assert decode(k1) == derive_cache_key(SECRET, 1, 0)
    assert decode(k2) == derive_cache_key(SECRET, 1, 1)


def test_session_key_differs_between_secrets(tmp_path, pw_hash):
    a = authed(tmp_path / "a", pw_hash).get("/api/session").json()["cache_key"]
    b = authed(tmp_path / "b", pw_hash, OTHER).get("/api/session").json()["cache_key"]
    assert a != b


def test_session_response_no_store(tmp_path):
    r = make(tmp_path).get("/api/session")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert "content-security-policy" in r.headers


def test_session_body_shape_and_no_secret_leak(tmp_path):
    r = make(tmp_path).get("/api/session")
    body = r.json()
    assert set(body) == {"cache_key", "mode", "auth_enabled"}
    assert body["mode"] == "single" and body["auth_enabled"] is False
    assert len(decode(body["cache_key"])) == 32
    assert SECRET not in r.text
    assert body["cache_key"] != derive_key(SECRET).hex()


def test_cache_key_is_not_other_derivations(tmp_path):
    key = decode(make(tmp_path).get("/api/session").json()["cache_key"])
    assert key == derive_cache_key(SECRET, 1, 0)
    assert key != derive_cache_key(SECRET, 2, 0)
    assert key != derive_cache_key(SECRET, 1, 1)
    assert key != derive_key(SECRET)
    assert key != derive_key(SECRET, info=b"aliasfox-cookie-v1")
    assert key != derive_key(SECRET, info=b"aliasfox-cache-v1")


def test_derive_cache_key_unit():
    k = derive_cache_key(SECRET, 1, 0)
    assert isinstance(k, bytes) and len(k) == 32
    assert k == derive_cache_key(SECRET, 1, 0)
    assert k != derive_cache_key(OTHER, 1, 0)
    assert k == derive_key(SECRET, info=b"aliasfox-cache-v1|1|0")
    # delimiter prevents (1, 10) colliding with (11, 0)
    assert derive_cache_key(SECRET, 1, 10) != derive_cache_key(SECRET, 11, 0)


def test_session_uses_epoch_validated_by_current_user(tmp_path, pw_hash):
    c = authed(tmp_path, pw_hash)

    class Flaky:
        def __init__(self):
            self.calls = 0

        def get(self, user_id):
            self.calls += 1
            return 0 if self.calls == 1 else 99

        def bump(self, user_id):  # pragma: no cover
            raise AssertionError

    c.app.state.epochs = store = Flaky()
    r = c.get("/api/session")
    assert r.status_code == 200
    assert decode(r.json()["cache_key"]) == derive_cache_key(SECRET, 1, 0)
    assert store.calls == 1

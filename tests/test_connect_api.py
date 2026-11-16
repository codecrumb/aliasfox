import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import aliasfox.api as api_mod
from aliasfox.apple.connect import InvalidCookie
from aliasfox.apple.login import LoginFlow
from aliasfox.config import Settings
from aliasfox.crypto import hash_password
from aliasfox.db import AppleSessionRow, init_db
from aliasfox.main import create_app
from aliasfox.sessions import AppleSessionState, SessionStore

SECRET = "unit-test-secret-key-0123456789abcdef"
PASSWORD = "hunter2-SECRET-pw"
APPLE_ID = "someone@example.com"
COOKIE_VALUE = "SUPERSECRETCOOKIEVALUE"
CODE = "123456"
LOGIN = {"apple_id": APPLE_ID, "password": PASSWORD}


class FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class FakeSession:
    def __init__(self):
        self.cookies = [FakeCookie("X-APPLE-WEBAUTH-USER", COOKIE_VALUE)]
        self.data = {"trust_token": "TT"}


class FakeApi:
    requires_2fa = True
    requires_2sa = False
    two_factor_delivery_method = "trusted_device"
    fail_login = False
    made: list = []

    def __init__(self, apple_id, password, directory):
        if self.fail_login:
            raise RuntimeError(f"Invalid creds {apple_id} {password}")
        self.dir = directory
        self.session = FakeSession()
        self.data = {"dsInfo": {"dsid": "42"}}
        self.webservices = {"premiummailsettings": {"url": "https://p1-maildomainws.icloud.com"}}
        self.made.append(self)

    def request_2fa_code(self):
        pass

    def validate_2fa_code(self, code):
        return code == CODE


def fake_factory(cls):
    def factory(apple_id, password, *, cookie_directory, china_mainland=False):
        return cls(apple_id, password, cookie_directory)

    return factory


class FakeService:
    """Mimics AliasService's session writes (replace_session/disconnect)."""

    def __init__(self, store=None):
        self.store = store
        self.replaced = []
        self.disconnected = []

    async def replace_session(self, user_id, state):
        self.replaced.append(user_id)
        self.store.save(user_id, state)

    async def disconnect(self, user_id):
        self.disconnected.append(user_id)
        self.store.delete(user_id)

    async def is_connected(self, user_id):
        return True


def new_store():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(eng)
    return SessionStore(eng, b"k" * 32), eng


class Env:
    pass


@pytest.fixture
def env():
    e = Env()
    e.api_cls = type("A", (FakeApi,), {"made": []})
    e.store, e.engine = new_store()
    e.svc = FakeService(e.store)
    e.flow = LoginFlow(service_factory=fake_factory(e.api_cls))
    settings = Settings(secret_key=SECRET)
    e.client = TestClient(
        create_app(settings, service=e.svc, store=e.store, login_flow=e.flow),
        base_url="http://localhost",
    )
    return e


def fake_connect(text, **kw):
    if "BAD" in text:
        raise InvalidCookie("cookie looks truncated or corrupted - copy it again")
    return AppleSessionState({"X-APPLE-WEBAUTH-USER": COOKIE_VALUE}, "https://p1.icloud.com", "7")


def test_connect_cookie_stores_session(env, monkeypatch):
    monkeypatch.setattr(api_mod, "connect_with_cookie", fake_connect)
    r = env.client.post("/api/connect/cookie", json={"cookie": "X-APPLE-WEBAUTH-USER=v"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert env.store.load(1).webservices_url == "https://p1.icloud.com"
    assert env.svc.replaced == [1]


def test_connect_invalid_cookie_400_without_echo(env, monkeypatch):
    monkeypatch.setattr(api_mod, "connect_with_cookie", fake_connect)
    r = env.client.post("/api/connect/cookie", json={"cookie": "BAD-ECHOME-12345"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_cookie"
    assert "ECHOME" not in r.text
    assert env.store.load(1) is None


def test_cookie_body_too_long_422(env):
    r = env.client.post("/api/connect/cookie", json={"cookie": "a" * 16385})
    assert r.status_code == 422


def test_login_needs_2fa_then_verify_stores_session(env):
    r = env.client.post("/api/connect/login", json=LOGIN)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_2fa" and body["flow_id"]
    assert env.store.load(1) is None
    r2 = env.client.post("/api/connect/verify", json={"flow_id": body["flow_id"], "code": CODE})
    assert r2.status_code == 200 and r2.json() == {"status": "ok"}
    st = env.store.load(1)
    assert st.cookies == {"X-APPLE-WEBAUTH-USER": COOKIE_VALUE}
    assert st.webservices_url == "https://p1-maildomainws.icloud.com"
    assert st.dsid == "42" and st.extra == {"trust_token": "TT"}
    assert env.svc.replaced == [1]


def test_login_without_2fa_saves_immediately(env):
    env.api_cls.requires_2fa = False
    r = env.client.post("/api/connect/login", json=LOGIN)
    assert r.json() == {"status": "ok"}
    assert env.store.load(1) is not None


def test_stored_blob_has_no_plaintext_cookie(env):
    env.api_cls.requires_2fa = False
    env.client.post("/api/connect/login", json=LOGIN)
    with Session(env.engine) as s:
        blob = s.scalar(select(AppleSessionRow.blob).where(AppleSessionRow.user_id == 1))
    assert blob and COOKIE_VALUE.encode() not in blob


def test_error_mapping(env):
    env.api_cls.fail_login = True
    r = env.client.post("/api/connect/login", json=LOGIN)
    assert r.status_code == 401 and r.json() == {"detail": "apple_login_failed"}
    env.api_cls.fail_login = False
    r = env.client.post("/api/connect/verify", json={"flow_id": "nope", "code": CODE})
    assert r.status_code == 410 and r.json() == {"detail": "flow_expired"}
    fid = env.client.post("/api/connect/login", json=LOGIN).json()["flow_id"]
    r = env.client.post("/api/connect/verify", json={"flow_id": fid, "code": "000000"})
    assert r.status_code == 401 and r.json() == {"detail": "invalid_code"}


def test_too_many_flows_429(env):
    env.flow._max_pending = 1
    env.flow.start(2, APPLE_ID, PASSWORD)  # another user holds the only slot
    r = env.client.post("/api/connect/login", json=LOGIN)
    assert r.status_code == 429 and r.json() == {"detail": "too_many_attempts"}


def test_verify_rejects_non_six_digit_code_422(env):
    for code in ["12345", "1234567", "abcdef", "12345a", "123456\n"]:
        r = env.client.post("/api/connect/verify", json={"flow_id": "x", "code": code})
        assert r.status_code == 422, code


def test_login_validation_422(env):
    assert env.client.post("/api/connect/login", json={"apple_id": "a"}).status_code == 422
    big = {"apple_id": "a" * 321, "password": "p"}
    assert env.client.post("/api/connect/login", json=big).status_code == 422
    assert env.client.post(
        "/api/connect/login", json={"apple_id": "a", "password": "p" * 1025}
    ).status_code == 422


def test_password_not_in_response_logs_or_exceptions(env, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    texts = []

    def grab(r):
        texts.append(r.text)
        texts.append(str(dict(r.headers)))
        return r

    # success path
    r = grab(env.client.post("/api/connect/login", json=LOGIN))
    fid = r.json()["flow_id"]
    # wrong code
    grab(env.client.post("/api/connect/verify", json={"flow_id": fid, "code": "000000"}))
    # success
    grab(env.client.post("/api/connect/verify", json={"flow_id": fid, "code": CODE}))
    # expired flow
    grab(env.client.post("/api/connect/verify", json={"flow_id": fid, "code": CODE}))
    # wrong password; capture exception strings raised by LoginFlow itself
    env.api_cls.fail_login = True
    grab(env.client.post("/api/connect/login", json=LOGIN))
    with pytest.raises(Exception) as ei:
        env.flow.start(1, APPLE_ID, PASSWORD)
    texts.append(repr(ei.value) + str(ei.value))
    texts.append(caplog.text)
    blob = "\n".join(texts)
    for secret in (PASSWORD, APPLE_ID, CODE, COOKIE_VALUE):
        assert secret not in blob, secret


def test_login_attempt_cleans_up_temp_dir(env):
    fid = env.client.post("/api/connect/login", json=LOGIN).json()["flow_id"]
    d = env.api_cls.made[0].dir
    assert os.path.isdir(d)
    env.client.post("/api/connect/verify", json={"flow_id": fid, "code": CODE})
    assert not os.path.exists(d)


def test_forget_session_deletes_row(env):
    env.store.save(1, AppleSessionState({"a": "b"}, "u", None))
    r = env.client.delete("/api/connect")
    assert r.status_code == 200 and r.json() == {"status": "disconnected"}
    assert env.store.load(1) is None
    assert env.svc.disconnected == [1]


def test_apple_login_rate_limited(env):
    env.api_cls.fail_login = True
    for _ in range(5):
        assert env.client.post("/api/connect/login", json=LOGIN).status_code == 401
    r = env.client.post("/api/connect/login", json=LOGIN)
    assert r.status_code == 429 and r.json() == {"detail": "rate_limited"}
    assert int(r.headers["retry-after"]) >= 1
    assert len(env.api_cls.made) == 0  # failures counted, no further Apple call


def test_failed_logins_are_not_released(env):
    env.api_cls.fail_login = True
    for _ in range(5):
        env.client.post("/api/connect/login", json=LOGIN)
    env.api_cls.fail_login = False
    assert env.client.post("/api/connect/login", json=LOGIN).status_code == 429


def test_validation_error_does_not_consume_budget(env):
    for _ in range(10):
        env.client.post("/api/connect/login", json={"apple_id": "a"})
    assert env.client.post("/api/connect/login", json=LOGIN).status_code == 200


def test_verify_and_cookie_rate_limited_at_10(env, monkeypatch):
    monkeypatch.setattr(api_mod, "connect_with_cookie", fake_connect)
    for _ in range(10):
        assert env.client.post("/api/connect/verify", json={"flow_id": "x", "code": CODE}).status_code == 410
    assert env.client.post("/api/connect/verify", json={"flow_id": "x", "code": CODE}).status_code == 429
    for _ in range(10):
        assert env.client.post("/api/connect/cookie", json={"cookie": "BAD"}).status_code == 400
    assert env.client.post("/api/connect/cookie", json={"cookie": "BAD"}).status_code == 429


def test_global_login_limit(env):
    env.api_cls.fail_login = True
    # different IPs via trusted proxy: per-IP never trips, global does at 15
    store, _ = new_store()
    settings = Settings(secret_key=SECRET, trusted_proxy=True)
    c = TestClient(
        create_app(settings, service=env.svc, store=store, login_flow=env.flow),
        base_url="http://localhost",
    )
    codes = [
        c.post("/api/connect/login", json=LOGIN, headers={"x-forwarded-for": f"10.0.0.{i}"}).status_code
        for i in range(16)
    ]
    assert codes[:15] == [401] * 15 and codes[15] == 429


def test_router_requires_store():
    with pytest.raises(RuntimeError):
        create_app(Settings(secret_key=SECRET), service=FakeService(), store=None)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/connect/cookie", {"cookie": "x"}),
        ("post", "/api/connect/login", LOGIN),
        ("post", "/api/connect/verify", {"flow_id": "x", "code": CODE}),
        ("delete", "/api/connect", None),
    ],
)
def test_connect_routes_require_auth(method, path, body):
    store, _ = new_store()
    settings = Settings(secret_key=SECRET, auth_enabled=True, password_hash=hash_password("pw-pw-pw"))
    c = TestClient(
        create_app(settings, service=FakeService(), store=store), base_url="http://localhost"
    )
    r = c.request(method, path, json=body)
    assert r.status_code == 401 and r.json() == {"detail": "not_authenticated"}


def test_disconnect_cancels_pending_flows(env):
    fid = env.client.post("/api/connect/login", json=LOGIN).json()["flow_id"]
    d = env.api_cls.made[0].dir
    assert env.client.delete("/api/connect").status_code == 200
    assert not os.path.exists(d)
    r = env.client.post("/api/connect/verify", json={"flow_id": fid, "code": CODE})
    assert r.status_code == 410 and r.json() == {"detail": "flow_expired"}


def test_hsa2_both_flags_end_to_end(env):
    env.api_cls.requires_2sa = True
    fid = env.client.post("/api/connect/login", json=LOGIN).json()["flow_id"]
    r = env.client.post("/api/connect/verify", json={"flow_id": fid, "code": CODE})
    assert r.json() == {"status": "ok"}


LONG_PW = "L0ngSecretPw" * 100
COOKIE_SECRET = "SECRETCOOKIE" + "x" * 17000


@pytest.mark.parametrize(
    "path,body,secret",
    [
        ("/api/connect/login", {"password": PASSWORD}, PASSWORD),
        ("/api/connect/login", {"apple_id": APPLE_ID, "password": LONG_PW}, LONG_PW[:40]),
        ("/api/connect/login", {"apple_id": APPLE_ID, "password": PASSWORD, "china": PASSWORD}, PASSWORD),
        ("/api/connect/cookie", {"cookie": COOKIE_SECRET}, "SECRETCOOKIE"),
        ("/api/connect/verify", {"flow_id": "f", "code": "12ab56"}, "12ab56"),
        ("/api/connect/verify", {"flow_id": "f", "code": "1234567"}, "1234567"),
    ],
)
def test_422_never_echoes_input(env, path, body, secret):
    r = env.client.post(path, json=body)
    assert r.status_code == 422
    assert secret not in r.text
    for item in r.json()["detail"]:
        assert set(item) == {"loc", "msg", "type"}

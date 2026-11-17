"""DELETE /api/account, GET /api/config and the per-user Apple login cap.

Multi-mode apps are built with create_app against a real SQLite file; only the
Neon verifier and Apple are faked.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_exchange import SECRET, FakeVerifier, exchange, ident, multi_settings

import aliasfox.main as main_mod
from aliasfox.apple.login import LoginFlow
from aliasfox.auth import COOKIE_NAME
from aliasfox.config import Settings
from aliasfox.db import make_engine
from aliasfox.keys import KeyRing
from aliasfox.main import create_app
from aliasfox.service import AliasService
from aliasfox.sessions import AppleSessionState, SessionStore
from test_connect_api import FakeApi, fake_factory

BASE = "https://aliasfox.test"


class FakeClient:
    """Blocks inside list() until released; remembers its persist callback."""

    gate: threading.Event
    entered: threading.Event
    callbacks: list = []

    def __init__(self, state, *, on_state_change=None):
        self.state = state
        self.on_state_change = on_state_change
        type(self).callbacks.append(on_state_change)

    def list(self):
        type(self).entered.set()
        assert type(self).gate.wait(5)
        self.state.cookies["rotated"] = "new"
        self.on_state_change(self.state)
        return []

    def close(self):
        pass


class Env:
    def __init__(self, tmp_path, monkeypatch, identities, *, client_factory=None,
                 login_flow=None, **kw):
        self.verifier = FakeVerifier(identities)
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        monkeypatch.setattr(main_mod, "NeonTokenVerifier", lambda **_: self.verifier)
        self.settings = multi_settings(
            tmp_path, database_url=f"sqlite:///{tmp_path}/multi.db", **kw
        )
        self.engine = make_engine(self.settings)
        keyring = KeyRing(SECRET)
        self.store = SessionStore(self.engine, keyring)
        kwargs = {"client_factory": client_factory} if client_factory else {}
        self.service = AliasService(self.store, **kwargs)
        self.app = create_app(
            self.settings, service=self.service, store=self.store, login_flow=login_flow
        )

    def client(self):
        return TestClient(self.app, base_url=BASE)

    def count(self, table, where="1=1"):
        with self.engine.connect() as c:
            return c.execute(text(f"select count(*) from {table} where {where}")).scalar_one()

    def save_session(self, uid):
        self.store.save(uid, AppleSessionState({"c": "v"}, "https://ws.example"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch, {"ta": ident("sub-a"), "tb": ident("sub-b")},
               signups_enabled=True)


def sign_in(env, token):
    c = env.client()
    assert exchange(c, token).status_code == 200
    return c


def uid_of(env, sub):
    return env.app.state.epochs.find_by_subject(sub)


def test_delete_account_removes_everything(env):
    c = sign_in(env, "ta")
    uid = uid_of(env, "sub-a")
    env.save_session(uid)
    assert env.count("apple_sessions", f"user_id={uid}") == 1
    old_cookie = c.cookies[COOKIE_NAME]
    r = c.delete("/api/account")
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    cleared = r.headers["set-cookie"].lower()
    assert COOKIE_NAME in cleared and "max-age=0" in cleared
    for flag in ("httponly", "samesite=strict", "path=/", "secure"):
        assert flag in cleared
    assert env.count("users", f"id={uid}") == 0  # row and wrapped key
    assert env.count("apple_sessions", f"user_id={uid}") == 0
    stale = env.client()
    stale.cookies.set(COOKIE_NAME, old_cookie)
    assert stale.get("/api/status").status_code == 401
    assert stale.delete("/api/account").status_code == 401
    assert c.delete("/api/account").status_code == 401


def test_delete_account_does_not_touch_other_user(env):
    ca, cb = sign_in(env, "ta"), sign_in(env, "tb")
    a, b = uid_of(env, "sub-a"), uid_of(env, "sub-b")
    env.save_session(a)
    env.save_session(b)
    key_before = env.count("users", f"id={b} and wrapped_key is not null")
    assert ca.delete("/api/account").status_code == 200
    assert key_before == 1 and env.count("users", f"id={b} and wrapped_key is not null") == 1
    assert env.store.load(b) is not None
    assert env.store.load(a) is None
    assert cb.get("/api/status").json()["connected"] is True


def test_delete_during_inflight_apple_call_does_not_resurrect_session(tmp_path, monkeypatch):
    cls = type("F", (FakeClient,), {
        "gate": threading.Event(), "entered": threading.Event(), "callbacks": []})
    e = Env(tmp_path, monkeypatch, {"ta": ident("sub-a")}, client_factory=cls,
            signups_enabled=True)
    with TestClient(e.app, base_url=BASE) as c:  # one event loop shared by the threads
        assert exchange(c, "ta").status_code == 200
        uid = uid_of(e, "sub-a")
        e.save_session(uid)
        results = {}
        t1 = threading.Thread(
            target=lambda: results.update(get=c.get("/api/aliases?refresh=true")))
        t1.start()
        assert cls.entered.wait(5)
        t2 = threading.Thread(target=lambda: results.update(delete=c.delete("/api/account")))
        t2.start()
        t2.join(0.3)  # the delete queues behind the in-flight call
        cls.gate.set()
        t1.join(5)
        t2.join(5)
        assert not t1.is_alive() and not t2.is_alive()
        assert results["delete"].status_code == 200
        assert e.count("apple_sessions", f"user_id={uid}") == 0
        # a stale client finishing after the delete must not write anything back
        for cb in cls.callbacks:
            cb(AppleSessionState({"c": "late"}, "https://ws.example"))
        assert e.count("apple_sessions", f"user_id={uid}") == 0
        assert e.count("users", f"id={uid}") == 0


def test_delete_cancels_pending_login_flow(tmp_path, monkeypatch):
    api_cls = type("A", (FakeApi,), {"made": []})
    flow = LoginFlow(service_factory=fake_factory(api_cls))
    e = Env(tmp_path, monkeypatch, {"ta": ident("sub-a")}, login_flow=flow, signups_enabled=True)
    c = sign_in(e, "ta")
    fid = c.post("/api/connect/login", json={"apple_id": "x@example.com", "password": "pw"}
                 ).json()["flow_id"]
    assert c.delete("/api/account").status_code == 200
    c2 = sign_in(e, "ta")  # sign-ups are open: a new account
    r = c2.post("/api/connect/verify", json={"flow_id": fid, "code": "123456"})
    assert r.status_code == 410 and r.json() == {"detail": "flow_expired"}


def test_reexchange_after_delete_with_signups_closed_is_403(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, {"ta": ident("sub-a")}, signups_enabled=False,
            signup_allowlist="someone.private@example.com")
    c = sign_in(e, "ta")  # allowlisted
    assert c.delete("/api/account").status_code == 200
    e.settings.signup_allowlist = ""  # no longer allowlisted
    c2 = e.client()
    r = exchange(c2, "ta")
    assert r.status_code == 403 and r.json() == {"detail": "signups_closed"}
    assert COOKIE_NAME not in c2.cookies


def test_delete_account_absent_in_single_mode(tmp_path):
    app = create_app(Settings(secret_key=SECRET, data_dir=tmp_path))
    c = TestClient(app, base_url="http://localhost")
    assert c.delete("/api/account").status_code in (404, 405)
    assert "/api/account" not in {getattr(r, "path", None) for r in app.routes}


# --- /api/config --------------------------------------------------------------------


def test_config_endpoint_reflects_flag_and_hides_secrets(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, {}, signups_enabled=True,
            signup_allowlist="friend@example.com", contact_email="hi@example.com",
            neon_auth_issuer="https://issuer.example", public_url="https://aliasfox.test",
            password_hash="argon-hash-secret")
    r = e.client().get("/api/config")  # no cookie
    assert r.status_code == 200
    assert r.json() == {
        "mode": "multi",
        "signups_open": True,
        "neon_auth_url": "https://auth.example/neondb/auth",
        "contact": "hi@example.com",
    }
    for leak in (SECRET, "jwks", "friend@example.com", "argon-hash-secret", "issuer.example"):
        assert leak not in r.text
    e.settings.signups_enabled = False
    assert e.client().get("/api/config").json()["signups_open"] is False


def test_config_single_mode(tmp_path):
    s = Settings(secret_key=SECRET, data_dir=tmp_path, contact_email="hi@example.com",
                 signups_enabled=True, neon_auth_base_url="https://auth.example/x")
    r = TestClient(create_app(s), base_url="http://localhost").get("/api/config")
    assert r.json() == {"mode": "single", "signups_open": False, "neon_auth_url": None,
                        "contact": "hi@example.com"}


def test_config_not_shadowed_by_spa(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>")
    s = Settings(secret_key=SECRET, data_dir=tmp_path)
    r = TestClient(create_app(s, dist_dir=dist), base_url="http://localhost").get("/api/config")
    assert r.headers["content-type"].startswith("application/json")


# --- per-user Apple login cap -------------------------------------------------------


def login_env(tmp_path, monkeypatch):
    api_cls = type("A", (FakeApi,), {"made": [], "requires_2fa": False})
    flow = LoginFlow(service_factory=fake_factory(api_cls))
    return Env(tmp_path, monkeypatch, {"ta": ident("sub-a"), "tb": ident("sub-b")},
               login_flow=flow, signups_enabled=True)


def login(c, n):
    return c.post("/api/connect/login",
                  json={"apple_id": "x@example.com", "password": "pw"},
                  headers={"X-Forwarded-For": f"10.0.0.{n}"})


def test_per_user_login_cap_five_per_hour_other_user_unaffected(tmp_path, monkeypatch):
    e = login_env(tmp_path, monkeypatch)
    ca, cb = sign_in(e, "ta"), sign_in(e, "tb")
    for n in range(1, 6):  # a distinct IP each time: only the user cap can trip
        assert login(ca, n).status_code == 200
    r = login(ca, 6)
    assert r.status_code == 429 and r.json() == {"detail": "rate_limited"}
    assert int(r.headers["retry-after"]) >= 1
    assert login(cb, 7).status_code == 200


def test_delete_resets_per_user_limiters(tmp_path, monkeypatch):
    e = login_env(tmp_path, monkeypatch)
    ca = sign_in(e, "ta")
    for n in range(1, 6):
        assert login(ca, n).status_code == 200
    assert login(ca, 6).status_code == 429
    assert ca.delete("/api/account").status_code == 200
    cb = sign_in(e, "tb")  # may reuse the deleted id on SQLite
    assert login(cb, 8).status_code == 200

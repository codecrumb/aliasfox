"""POST /api/auth/exchange: Neon Auth token -> local session cookie, sign-up gate.

The verifier is faked (the real one is covered in test_neon_jwt.py); cookies,
the SQLite database and UserDirectory are real.
"""
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import inspect, text

import aliasfox.main as main_mod
from aliasfox.api import install_error_handlers
from aliasfox.auth import COOKIE_NAME, current_user, install_auth
from aliasfox.config import Settings
from aliasfox.crypto import derive_key
from aliasfox.db import init_db, make_engine
from aliasfox.exchange import install_exchange
from aliasfox.keys import KeyRing
from aliasfox.neon_jwt import Identity, TokenRejected
from aliasfox.users import UserDirectory
from helpers import multi_mode_settings

SECRET = "exchange-test-secret-key-0123456789abcdef"
BASE = "https://aliasfox.test"
EMAIL = "someone.private@example.com"


class FakeVerifier:
    """token -> Identity; anything else is rejected like the real verifier."""

    def __init__(self, identities=None, before=None):
        self.identities = dict(identities or {})
        self.before = before
        self.calls = 0

    def verify(self, token):
        self.calls += 1
        if self.before is not None:
            self.before(token)
        if not isinstance(token, str) or token not in self.identities:
            raise TokenRejected()
        return self.identities[token]


def ident(sub, email=EMAIL, verified=True):
    return Identity(subject=sub, email=email, email_verified=verified)


def multi_settings(tmp_path, **kw):
    kw.setdefault("secret_key", SECRET)
    return multi_mode_settings(tmp_path, **kw)


class Env:
    def __init__(self, tmp_path, verifier, **kw):
        self.settings = multi_settings(tmp_path, **kw)
        # Real SQLite file under tmp_path (make_engine without DATABASE_URL).
        self.engine = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
        init_db(self.engine)
        self.users = UserDirectory(self.engine, KeyRing(SECRET))
        self.verifier = verifier
        app = FastAPI()
        install_auth(app, self.settings, self.users, password_login=False)
        install_exchange(app, self.settings, verifier, self.users)
        install_error_handlers(app)

        @app.get("/api/protected")
        def protected(uid: int = Depends(current_user)):
            return {"uid": uid}

        self.app = app

    def client(self):
        return TestClient(self.app, base_url=BASE)

    def count_users(self):
        with self.engine.connect() as c:
            return c.execute(text("select count(*) from users")).scalar_one()


def env(tmp_path, identities=None, **kw):
    return Env(tmp_path, FakeVerifier(identities), **kw)


def exchange(c, token, **kw):
    return c.post("/api/auth/exchange", json={"token": token}, **kw)


def serializer(secret=SECRET):
    return URLSafeTimedSerializer(derive_key(secret, info=b"aliasfox-cookie-v1").hex())


def cookie_payload(c):
    return serializer().loads(c.cookies[COOKIE_NAME])


# --- sign-in and sign-up gate ------------------------------------------------------


def test_known_subject_gets_cookie(tmp_path):
    e = env(tmp_path, {"t-known": ident("sub-a", email=None, verified=False)})
    uid = e.users.create("sub-a")
    c = e.client()
    r = exchange(c, "t-known")  # known sub signs in without any email claim
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    h = r.headers["set-cookie"].lower()
    for flag in ("httponly", "samesite=strict", "path=/", "secure", "max-age=604800"):
        assert flag in h
    assert cookie_payload(c) == {"u": uid, "e": e.users.get(uid)}
    assert c.get("/api/protected").json() == {"uid": uid}
    assert e.count_users() == 1


def test_known_subject_signs_in_while_signups_closed(tmp_path):
    e = env(tmp_path, {"t": ident("sub-a", verified=False)}, signups_enabled=False)
    uid = e.users.create("sub-a")
    c = e.client()
    assert exchange(c, "t").status_code == 200
    assert c.get("/api/protected").json() == {"uid": uid}


def test_unknown_subject_closed_is_403(tmp_path):
    e = env(tmp_path, {"t": ident("sub-new")}, signups_enabled=False,
            signup_allowlist="other@example.com")
    c = e.client()
    r = exchange(c, "t")
    assert r.status_code == 403
    assert r.json() == {"detail": "signups_closed"}
    assert COOKIE_NAME not in c.cookies
    assert "set-cookie" not in r.headers
    assert e.count_users() == 0


def test_unknown_subject_open_creates_user(tmp_path):
    e = env(tmp_path, {"t": ident("sub-new")}, signups_enabled=True)
    c = e.client()
    r = exchange(c, "t")
    assert r.status_code == 200
    uid = e.users.find_by_subject("sub-new")
    assert uid is not None
    assert cookie_payload(c) == {"u": uid, "e": e.users.get(uid)}
    assert c.get("/api/protected").json() == {"uid": uid}
    assert e.count_users() == 1
    # a second exchange for the same subject reuses the row
    assert exchange(e.client(), "t").status_code == 200
    assert e.count_users() == 1


def test_allowlisted_email_creates_user_while_closed(tmp_path):
    e = env(tmp_path, {"t": ident("sub-owner", email="owner@example.com")},
            signups_enabled=False, signup_allowlist="friend@example.com, owner@example.com")
    c = e.client()
    assert exchange(c, "t").status_code == 200
    assert c.get("/api/protected").json() == {"uid": e.users.find_by_subject("sub-owner")}


def test_allowlist_case_insensitive(tmp_path):
    e = env(tmp_path, {"t": ident("sub-owner", email="OWNER@Example.COM")},
            signups_enabled=False, signup_allowlist="Owner@example.com")
    assert exchange(e.client(), "t").status_code == 200
    assert e.users.find_by_subject("sub-owner") is not None


@pytest.mark.parametrize("signups", [True, False])
def test_unverified_email_cannot_create(tmp_path, signups):
    e = env(
        tmp_path,
        {
            "unverified": ident("sub-u", email="owner@example.com", verified=False),
            "no-email": ident("sub-n", email=None, verified=False),
        },
        signups_enabled=signups,
        signup_allowlist="owner@example.com",
    )
    # Without an email nobody is on the allowlist: while closed that is signups_closed.
    expected = {
        "unverified": "email_not_verified",
        "no-email": "email_not_verified" if signups else "signups_closed",
    }
    for token, detail in expected.items():
        c = e.client()
        r = exchange(c, token)
        assert r.status_code == 403, token
        assert r.json() == {"detail": detail}, token
        assert COOKIE_NAME not in c.cookies
    assert e.count_users() == 0


def test_unverified_stranger_told_signups_closed_before_verification(tmp_path):
    e = env(tmp_path, {"t": ident("sub-x", email="stranger@example.com", verified=False)},
            signups_enabled=False, signup_allowlist="owner@example.com")
    c = e.client()
    r = exchange(c, "t")
    assert r.status_code == 403 and r.json() == {"detail": "signups_closed"}
    assert COOKIE_NAME not in c.cookies and e.count_users() == 0


def test_unverified_allowlisted_email_is_not_admitted(tmp_path):
    e = env(tmp_path, {
        "unverified": ident("sub-o", email="  Owner@Example.com ", verified=False),
        "verified": ident("sub-o", email="  Owner@Example.com ", verified=True),
    }, signups_enabled=False, signup_allowlist="owner@example.com")
    c = e.client()
    r = exchange(c, "unverified")
    assert r.status_code == 403 and r.json() == {"detail": "email_not_verified"}
    assert COOKIE_NAME not in c.cookies and e.count_users() == 0
    # once verified, the trimmed, lowercased email matches the allowlist
    assert exchange(c, "verified").status_code == 200
    assert e.count_users() == 1


def test_denylisted_unknown_subject_is_403_and_no_user_created(tmp_path):
    e = env(tmp_path, {"t": ident("sub-bad")}, signups_enabled=True,
            subject_denylist="sub-other, sub-bad")
    c = e.client()
    r = exchange(c, "t")
    assert r.status_code == 403 and r.json() == {"detail": "account_disabled"}
    assert COOKIE_NAME not in c.cookies and "set-cookie" not in r.headers
    assert e.count_users() == 0


def test_denylisted_known_subject_is_403_and_existing_cookies_die(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a"), "tb": ident("sub-b")}, signups_enabled=True)
    a, b = e.client(), e.client()
    assert exchange(a, "ta").status_code == 200
    assert exchange(b, "tb").status_code == 200
    ua, ub = e.users.find_by_subject("sub-a"), e.users.find_by_subject("sub-b")
    assert a.get("/api/protected").json() == {"uid": ua}

    e.settings.subject_denylist = "sub-a"  # the operator adds A and restarts
    c = e.client()
    r = exchange(c, "ta")
    assert r.status_code == 403 and r.json() == {"detail": "account_disabled"}
    assert COOKIE_NAME not in c.cookies and "set-cookie" not in r.headers
    assert a.get("/api/protected").status_code == 401  # A's old cookie no longer works
    assert e.users.find_by_subject("sub-a") == ua  # the row is kept, only locked out
    # B is unaffected: the old cookie still works and B can sign in again
    assert b.get("/api/protected").json() == {"uid": ub}
    assert exchange(e.client(), "tb").status_code == 200


def test_no_email_stored_in_db(tmp_path):
    e = env(tmp_path, {"t": ident("sub-new", email=EMAIL)}, signups_enabled=False,
            signup_allowlist=EMAIL)
    assert exchange(e.client(), "t").status_code == 200
    cols = {col["name"] for col in inspect(e.engine).get_columns("users")}
    assert not any("email" in name or "mail" in name for name in cols), cols
    with e.engine.connect() as conn:
        rows = conn.execute(text("select * from users")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["auth_subject"] == "sub-new"
    for value in rows[0].values():
        assert EMAIL not in str(value)
    e.engine.dispose()
    raw = (tmp_path / "aliasfox.db").read_bytes()
    assert EMAIL.encode() not in raw
    assert b"example.com" not in raw


def test_two_concurrent_first_exchanges_one_user(tmp_path):
    n = 8
    barrier = threading.Barrier(n)
    verifier = FakeVerifier({"t": ident("sub-race")}, before=lambda _t: barrier.wait(10))
    e = Env(tmp_path, verifier, signups_enabled=True)

    def attempt(i):
        c = e.client()
        r = exchange(c, "t", headers={"X-Forwarded-For": f"10.0.0.{i + 1}"})
        return r.status_code, (cookie_payload(c) if r.status_code == 200 else None)

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(attempt, range(n)))
    assert [code for code, _ in results] == [200] * n
    assert e.count_users() == 1
    uid = e.users.find_by_subject("sub-race")
    assert {p["u"] for _, p in results} == {uid}


# --- token and body handling -------------------------------------------------------


def test_bad_token_401_no_cookie(tmp_path):
    e = env(tmp_path, {"good": ident("sub-a")}, signups_enabled=True)
    c = e.client()
    r = exchange(c, "forged.jwt.token")
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid_token"}
    assert COOKIE_NAME not in c.cookies
    assert "set-cookie" not in r.headers
    assert e.count_users() == 0


def test_oversized_or_non_string_token_is_422_or_401_never_500(tmp_path):
    e = env(tmp_path, {"x" * 4096: ident("sub-a")}, signups_enabled=True)
    c = e.client()
    for body in ({"token": "x" * 4097}, {"token": 5}, {"token": None}, {"token": ["a"]},
                 {"token": {"a": 1}}, {}, [], "token", 7):
        r = c.post("/api/auth/exchange", json=body)
        assert r.status_code == 422, body
        assert "x" * 100 not in r.text  # validation errors never echo the input
    for raw in (b"not json", b"", b"\xff\xfe"):
        r = c.post("/api/auth/exchange", content=raw,
                   headers={"content-type": "application/json"})
        assert r.status_code == 422, raw
    assert exchange(c, "").status_code == 401
    assert exchange(c, "y" * 4096).status_code == 401
    assert COOKIE_NAME not in c.cookies
    assert exchange(c, "x" * 4096).status_code == 200  # max length accepted


def test_exchange_rate_limited_per_ip(tmp_path):
    e = env(tmp_path, {"good": ident("sub-a")}, signups_enabled=True)
    ip = {"X-Forwarded-For": "203.0.113.9"}
    c = e.client()
    assert [exchange(c, "bad", headers=ip).status_code for _ in range(5)] == [401] * 5
    calls = e.verifier.calls
    r = exchange(c, "good", headers=ip)
    assert r.status_code == 429
    assert r.json() == {"detail": "too_many_attempts"}
    assert int(r.headers["retry-after"]) > 0
    assert e.verifier.calls == calls  # throttled before verifying
    assert COOKIE_NAME not in c.cookies
    other = {"X-Forwarded-For": "203.0.113.10"}
    assert exchange(e.client(), "good", headers=other).status_code == 200


def test_exchange_successes_not_counted(tmp_path):
    e = env(tmp_path, {"good": ident("sub-a")}, signups_enabled=True)
    ip = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(10):
        assert exchange(e.client(), "good", headers=ip).status_code == 200
    assert exchange(e.client(), "bad", headers=ip).status_code == 401


def test_gate_refusals_count_against_ip(tmp_path):
    e = env(tmp_path, {"closed": ident("sub-x")}, signups_enabled=False)
    ip = {"X-Forwarded-For": "203.0.113.9"}
    codes = [exchange(e.client(), "closed", headers=ip).status_code for _ in range(6)]
    assert codes == [403] * 5 + [429]


def test_exchange_cross_origin_rejected(tmp_path):
    e = env(tmp_path, {"t": ident("sub-a")}, signups_enabled=True)
    c = e.client()
    r = exchange(c, "t", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"detail": "bad_origin"}
    assert e.verifier.calls == 0
    assert exchange(c, "t", headers={"Origin": BASE}).status_code == 200


def test_verifier_runs_off_the_event_loop(tmp_path):
    seen = []

    def check(_token):
        try:
            asyncio.get_running_loop()
            seen.append("on-loop")
        except RuntimeError:
            seen.append("threadpool")

    e = Env(tmp_path, FakeVerifier({"t": ident("sub-a")}, before=check), signups_enabled=True)
    assert exchange(e.client(), "t").status_code == 200
    assert seen == ["threadpool"]


def test_exchange_never_logs_token_subject_or_email(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    token = "tok-SECRET-abcdef"
    e = env(tmp_path, {token: ident("sub-SECRET-1", email="Hidden.Person@example.com")},
            signups_enabled=True)
    c = e.client()
    exchange(c, token)
    exchange(e.client(), "bad-tok-SECRET-zzz")
    cookie = c.cookies.get(COOKIE_NAME)
    assert cookie
    for secret in (token, "sub-SECRET-1", "hidden.person", "Hidden.Person",
                   "bad-tok-SECRET-zzz", cookie, SECRET):
        assert secret not in caplog.text


# --- logout and cookie validation in multi mode ----------------------------------------


def test_logout_bumps_only_that_user_epoch(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a"), "tb": ident("sub-b")}, signups_enabled=True)
    a, b = e.client(), e.client()
    assert exchange(a, "ta").status_code == 200
    assert exchange(b, "tb").status_code == 200
    ua, ub = cookie_payload(a)["u"], cookie_payload(b)["u"]
    assert ua != ub
    stolen_a = a.cookies[COOKIE_NAME]
    ea, eb = e.users.get(ua), e.users.get(ub)
    assert a.post("/api/auth/logout").status_code == 200
    assert e.users.get(ua) == ea + 1
    assert e.users.get(ub) == eb
    assert b.get("/api/protected").json() == {"uid": ub}
    replay = e.client()
    replay.cookies.set(COOKIE_NAME, stolen_a)
    assert replay.get("/api/protected").status_code == 401


def test_anonymous_logout_bumps_nobody(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a")}, signups_enabled=True)
    exchange(e.client(), "ta")
    ua = e.users.find_by_subject("sub-a")
    before = e.users.get(ua)
    assert e.client().post("/api/auth/logout").status_code == 200
    assert e.users.get(ua) == before


def test_login_route_absent_in_multi(tmp_path):
    e = env(tmp_path)
    r = e.client().post("/api/auth/login", json={"password": "anything"})
    assert r.status_code == 404
    paths = {getattr(route, "path", None) for route in e.app.routes}
    assert "/api/auth/login" not in paths
    assert {"/api/auth/logout", "/api/auth/exchange"} <= paths


def test_cookie_for_deleted_user_is_401(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a")}, signups_enabled=True)
    c = e.client()
    exchange(c, "ta")
    uid = cookie_payload(c)["u"]
    assert c.get("/api/protected").status_code == 200
    e.users.delete(uid)
    assert c.get("/api/protected").status_code == 401
    forged = e.client()
    forged.cookies.set(COOKIE_NAME, serializer().dumps({"u": 999, "e": 0}))
    assert forged.get("/api/protected").status_code == 401


def test_cookie_epoch_mismatch_is_401(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a")}, signups_enabled=True)
    c = e.client()
    exchange(c, "ta")
    uid = cookie_payload(c)["u"]
    start = e.users.get(uid)
    for bad in (start + 1, start - 1, -1, str(start), None, True, float(start)):
        f = e.client()
        f.cookies.set(COOKIE_NAME, serializer().dumps({"u": uid, "e": bad}))
        assert f.get("/api/protected").status_code == 401, bad
    e.users.bump(uid)
    assert c.get("/api/protected").status_code == 401


def test_cookie_user_id_must_be_a_plain_int(tmp_path):
    e = env(tmp_path)
    e.users.ensure_single_user()  # user 1, epoch 0
    ok = e.client()
    ok.cookies.set(COOKIE_NAME, serializer().dumps({"u": 1, "e": 0}))
    assert ok.get("/api/protected").json() == {"uid": 1}
    for bad in (True, 1.0, "1", None, [1]):
        f = e.client()
        f.cookies.set(COOKIE_NAME, serializer().dumps({"u": bad, "e": 0}))
        assert f.get("/api/protected").status_code == 401, bad


def test_cookie_signed_with_other_secret_is_401(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a")}, signups_enabled=True)
    c = e.client()
    exchange(c, "ta")
    uid = cookie_payload(c)["u"]
    f = e.client()
    f.cookies.set(
        COOKIE_NAME, serializer("another-secret-key-0123456789abcdef").dumps(
            {"u": uid, "e": e.users.get(uid)}
        )
    )
    assert f.get("/api/protected").status_code == 401


def test_deleted_user_cookie_cannot_open_reused_id(tmp_path):
    e = env(tmp_path, {"ta": ident("sub-a"), "tb": ident("sub-b")}, signups_enabled=True)
    e.users.create("sub-first")  # A is not the only row; it is the highest id
    a = e.client()
    assert exchange(a, "ta").status_code == 200
    ua = cookie_payload(a)["u"]
    assert a.get("/api/protected").json() == {"uid": ua}
    e.users.delete(ua)
    b = e.client()
    assert exchange(b, "tb").status_code == 200
    ub = cookie_payload(b)["u"]
    assert ub == ua  # SQLite reuses the deleted highest id: the scenario under test
    assert a.get("/api/protected").status_code == 401  # A's old cookie opens nothing
    assert b.get("/api/protected").json() == {"uid": ub}


# --- create_app wiring ---------------------------------------------------------------


class RecordingVerifier(FakeVerifier):
    instances: list = []

    def __init__(self, *, jwks_url, issuer, **_kw):
        super().__init__({"t": ident("sub-wired")})
        self.jwks_url, self.issuer = jwks_url, issuer
        RecordingVerifier.instances.append(self)


@pytest.fixture
def recording(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    RecordingVerifier.instances = []
    monkeypatch.setattr(main_mod, "NeonTokenVerifier", RecordingVerifier)
    return RecordingVerifier.instances


def wired_settings(tmp_path, **kw):
    return multi_settings(tmp_path, database_url=f"sqlite:///{tmp_path}/multi.db", **kw)


def test_create_app_multi_wires_exchange(tmp_path, recording):
    app = main_mod.create_app(wired_settings(tmp_path, signups_enabled=True))
    (v,) = recording
    assert v.jwks_url == "https://auth.example/neondb/auth/.well-known/jwks.json"
    assert v.issuer == "https://auth.example"  # scheme://host of NEON_AUTH_BASE_URL
    c = TestClient(app, base_url=BASE)
    assert c.post("/api/auth/login", json={"password": "x"}).status_code == 404
    assert c.get("/api/status").status_code == 401
    assert exchange(c, "t").status_code == 200
    st = c.get("/api/status")
    assert st.status_code == 200
    assert st.json()["auth_enabled"] is True and st.json()["mode"] == "multi"
    s = c.get("/api/session").json()
    assert set(s) == {"cache_key", "mode", "auth_enabled"} and s["auth_enabled"] is True
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/status").status_code == 401


def test_create_app_multi_uses_explicit_issuer(tmp_path, recording):
    main_mod.create_app(wired_settings(tmp_path, neon_auth_issuer="https://issuer.example/x"))
    assert recording[0].issuer == "https://issuer.example/x"


def test_neon_auth_issuer_from_env(monkeypatch):
    monkeypatch.setenv("NEON_AUTH_ISSUER", "https://iss.example")
    assert Settings().neon_auth_issuer == "https://iss.example"
    monkeypatch.delenv("NEON_AUTH_ISSUER")
    assert Settings().neon_auth_issuer is None


def test_create_app_single_has_no_exchange(tmp_path, recording):
    app = main_mod.create_app(Settings(secret_key=SECRET, data_dir=tmp_path))
    assert recording == []
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/auth/exchange" not in paths
    assert "/api/auth/login" in paths


def test_multi_lifespan_does_not_seed_icloud_cookie(tmp_path, recording, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "seed_from_env", lambda s, st: calls.append(1))
    app = main_mod.create_app(wired_settings(tmp_path, icloud_cookie="X-APPLE-WEBAUTH-USER=v"))
    with TestClient(app, base_url=BASE):
        pass
    assert calls == []

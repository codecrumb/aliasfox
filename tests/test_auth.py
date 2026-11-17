import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from aliasfox.auth import client_ip, current_user, install_auth
from aliasfox.config import ConfigError, Settings
from aliasfox.crypto import derive_key, hash_password

PASSWORD = "correct horse battery staple"
SECRET = "unit-test-secret-key-0123456789abcdef"


@pytest.fixture(scope="module")
def pw_hash():
    return hash_password(PASSWORD)


def build(pw_hash, **kw):
    kw.setdefault("secret_key", SECRET)
    kw.setdefault("password_hash", pw_hash)
    kw.setdefault("auth_enabled", True)
    settings = Settings(**kw)
    app = FastAPI()
    install_auth(app, settings)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/protected")
    def protected(uid: int = Depends(current_user)):
        return {"uid": uid}

    @app.post("/api/mutate")
    def mutate(uid: int = Depends(current_user)):
        return {"uid": uid}

    return app


def client(app, base="http://localhost", **kw):
    return TestClient(app, base_url=base, **kw)


def serializer():
    key = derive_key(SECRET, info=b"aliasfox-cookie-v1")
    return URLSafeTimedSerializer(key.hex())


def login(c, pw=PASSWORD, **kw):
    return c.post("/api/auth/login", json={"password": pw}, **kw)


def test_login_correct_password_sets_cookie(pw_hash):
    c = client(build(pw_hash))
    r = login(c)
    assert r.status_code == 200
    assert "aliasfox_session" in c.cookies
    assert c.get("/api/protected").json() == {"uid": 1}


def test_cookie_flags(pw_hash):
    c = client(build(pw_hash))
    h = login(c).headers["set-cookie"].lower()
    assert "httponly" in h
    assert "samesite=strict" in h
    assert "path=/" in h
    assert "max-age=604800" in h
    assert "secure" not in h


def test_cookie_secure_on_non_loopback_host_when_auth_enabled(pw_hash):
    for base in ("http://aliasfox.onrender.com", "http://203.0.113.5:8080", "http://testserver"):
        c = client(build(pw_hash), base=base)
        assert "secure" in login(c).headers["set-cookie"].lower(), base


@pytest.mark.parametrize(
    "base", ["http://localhost", "http://127.0.0.1", "http://localhost:8080", "http://[::1]:8080"]
)
def test_cookie_not_secure_on_loopback_http(pw_hash, base):
    c = client(build(pw_hash), base=base)
    assert "secure" not in login(c).headers["set-cookie"].lower()


def test_logout_cookie_secure_on_non_loopback(pw_hash):
    c = client(build(pw_hash), base="https://aliasfox.onrender.com")
    login(c)
    assert "secure" in c.post("/api/auth/logout").headers["set-cookie"].lower()
    c = client(build(pw_hash), base="http://localhost")
    login(c)
    assert "secure" not in c.post("/api/auth/logout").headers["set-cookie"].lower()


def test_cookie_secure_on_https(pw_hash):
    c = client(build(pw_hash), base="https://localhost")
    assert "secure" in login(c).headers["set-cookie"].lower()


def test_cookie_secure_via_forwarded_proto_only_when_trusted(pw_hash):
    h = {"X-Forwarded-Proto": "https"}
    c = client(build(pw_hash, trusted_proxy=True))
    assert "secure" in login(c, headers=h).headers["set-cookie"].lower()
    c = client(build(pw_hash, trusted_proxy=False))
    assert "secure" not in login(c, headers=h).headers["set-cookie"].lower()


def test_login_wrong_password_401(pw_hash):
    c = client(build(pw_hash))
    r = login(c, "nope")
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid_credentials"}
    assert "aliasfox_session" not in c.cookies


@pytest.mark.parametrize("bad", [None, ""])
def test_empty_password_hash_fails_closed(bad):
    c = client(build(None, password_hash=bad))
    for pw in ("", "anything", PASSWORD):
        r = login(c, pw)
        assert r.status_code == 401
        assert r.json() == {"detail": "invalid_credentials"}
    assert c.get("/api/protected").status_code == 401


def test_malformed_login_body_rejected(pw_hash):
    c = client(build(pw_hash))
    assert c.post("/api/auth/login", json={}).status_code == 422
    assert c.post("/api/auth/login", json={"password": 5}).status_code == 422


def test_sixth_failure_returns_429(pw_hash):
    c = client(build(pw_hash))
    assert [login(c, "bad").status_code for _ in range(5)] == [401] * 5
    r = login(c, "bad")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


def test_correct_password_after_five_failures_still_429(pw_hash):
    c = client(build(pw_hash))
    for _ in range(5):
        login(c, "bad")
    r = login(c, PASSWORD)
    assert r.status_code == 429
    assert "aliasfox_session" not in c.cookies


def test_successful_logins_not_counted(pw_hash):
    c = client(build(pw_hash))
    for _ in range(10):
        assert login(c).status_code == 200
    assert login(c, "bad").status_code == 401


def test_throttle_is_per_ip_and_spoofed_xff_ignored_untrusted(pw_hash):
    c = client(build(pw_hash))
    for i in range(5):
        login(c, "bad", headers={"X-Forwarded-For": f"9.9.9.{i}"})
    # untrusted proxy: XFF ignored, so still the same client
    assert login(c, "bad", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429


def test_throttle_keyed_on_rightmost_xff_when_trusted(pw_hash):
    c = client(build(pw_hash, trusted_proxy=True))
    for i in range(5):
        # attacker rotates the spoofable left entry; proxy-appended right is constant
        login(c, "bad", headers={"X-Forwarded-For": f"9.9.9.{i}, 7.7.7.7"})
    r = login(c, "bad", headers={"X-Forwarded-For": "1.1.1.1, 7.7.7.7"})
    assert r.status_code == 429
    assert login(c, "bad", headers={"X-Forwarded-For": "1.1.1.1, 8.8.8.8"}).status_code == 401


class Req:
    def __init__(self, headers, host="10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": host})() if host else None


def test_client_ip_xff_rightmost_only_when_trusted():
    r = Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"})
    assert client_ip(r, trusted_proxy=True) == "3.3.3.3"
    assert client_ip(r, trusted_proxy=False) == "10.0.0.1"


def test_client_ip_fallbacks():
    assert client_ip(Req({}), trusted_proxy=True) == "10.0.0.1"
    assert client_ip(Req({"x-forwarded-for": " "}), trusted_proxy=True) == "10.0.0.1"
    assert client_ip(Req({}, host=None), trusted_proxy=False) == "unknown"


def test_protected_route_401_without_cookie_when_auth_on(pw_hash):
    r = client(build(pw_hash)).get("/api/protected")
    assert r.status_code == 401
    assert r.json() == {"detail": "not_authenticated"}


def test_tampered_cookie_rejected(pw_hash):
    c = client(build(pw_hash))
    login(c)
    good = c.cookies["aliasfox_session"]
    c.cookies.clear()
    c.cookies.set("aliasfox_session", good[:-2] + ("AA" if good[-2:] != "AA" else "BB"))
    assert c.get("/api/protected").status_code == 401


def test_cookie_signed_with_other_key_rejected(pw_hash):
    c = client(build(pw_hash))
    forged = URLSafeTimedSerializer("other").dumps({"u": 1})
    c.cookies.set("aliasfox_session", forged)
    assert c.get("/api/protected").status_code == 401


def test_wrong_payload_rejected(pw_hash):
    c = client(build(pw_hash))
    c.cookies.set("aliasfox_session", serializer().dumps({"u": 2, "e": 0}))
    assert c.get("/api/protected").status_code == 401


def test_expired_cookie_rejected(pw_hash, monkeypatch):
    c = client(build(pw_hash))
    c.cookies.set("aliasfox_session", serializer().dumps({"u": 1, "e": 0}))
    assert c.get("/api/protected").status_code == 200
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 7 * 86400 + 60)
    assert c.get("/api/protected").status_code == 401


def test_logout_clears_cookie(pw_hash):
    c = client(build(pw_hash))
    login(c)
    r = c.post("/api/auth/logout")
    assert r.status_code == 200
    assert "aliasfox_session" in r.headers["set-cookie"]
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert c.get("/api/protected").status_code == 401


def test_auth_off_allows_requests():
    c = client(build(None, auth_enabled=False, password_hash=None))
    assert c.get("/api/protected").json() == {"uid": 1}
    assert c.post("/api/mutate").json() == {"uid": 1}


def test_auth_off_login_does_not_grant_anything():
    c = client(build(None, auth_enabled=False, password_hash=None))
    assert login(c, "x").status_code == 401


def test_cross_origin_post_rejected(pw_hash):
    c = client(build(pw_hash))
    login(c)
    r = c.post("/api/mutate", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"detail": "bad_origin"}


def test_cross_origin_login_rejected(pw_hash):
    c = client(build(pw_hash))
    assert login(c, headers={"Origin": "http://evil.example"}).status_code == 403


def test_same_origin_and_no_origin_post_allowed(pw_hash):
    c = client(build(pw_hash))
    login(c)
    assert c.post("/api/mutate", headers={"Origin": "http://localhost"}).status_code == 200
    assert c.post("/api/mutate").status_code == 200


def test_origin_port_must_match(pw_hash):
    c = client(build(pw_hash), base="http://localhost:8080")
    login(c)
    assert c.post("/api/mutate", headers={"Origin": "http://localhost:8080"}).status_code == 200
    assert c.post("/api/mutate", headers={"Origin": "http://localhost:9"}).status_code == 403


def test_null_origin_rejected(pw_hash):
    c = client(build(pw_hash))
    login(c)
    assert c.post("/api/mutate", headers={"Origin": "null"}).status_code == 403


def test_origin_not_checked_for_get(pw_hash):
    c = client(build(pw_hash))
    login(c)
    r = c.get("/api/protected", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200


def test_bad_host_when_auth_off():
    app = build(None, auth_enabled=False, password_hash=None)
    r = client(app, base="http://evil.example").get("/api/protected")
    assert r.status_code == 403
    assert r.json() == {"detail": "bad_host"}


@pytest.mark.parametrize(
    "base", ["http://localhost", "http://127.0.0.1", "http://localhost:8080", "http://[::1]:8080"]
)
def test_local_hosts_allowed_when_auth_off(base):
    app = build(None, auth_enabled=False, password_hash=None)
    assert client(app, base=base).get("/api/protected").status_code == 200


def test_bad_host_not_checked_when_auth_on(pw_hash):
    c = client(build(pw_hash), base="http://aliasfox.onrender.com")
    assert login(c).status_code == 200


def test_healthz_exempt_from_guards():
    app = build(None, auth_enabled=False, password_hash=None)
    assert client(app, base="http://evil.example").get("/healthz").status_code == 200
    r = client(app, base="http://evil.example").post(
        "/healthz", headers={"Origin": "http://x.example"}
    )
    assert r.status_code == 405  # reached routing, not blocked by a guard


def test_settings_and_limiter_on_app_state(pw_hash):
    app = build(pw_hash)
    assert app.state.settings.auth_enabled is True
    assert app.state.login_limiter is not None


def test_password_never_in_logs(pw_hash, caplog):
    caplog.set_level(logging.DEBUG)
    c = client(build(pw_hash))
    login(c)
    login(c, "wrong-password-xyz")
    for _ in range(6):
        login(c, "wrong-password-xyz")
    cookie = c.cookies.get("aliasfox_session") or ""
    text = caplog.text
    for secret in (PASSWORD, "wrong-password-xyz", SECRET, pw_hash):
        assert secret not in text
    if cookie:
        assert cookie not in text


@pytest.mark.parametrize("key", [None, ""])
def test_install_auth_requires_secret_key(pw_hash, key):
    with pytest.raises(ConfigError):
        install_auth(FastAPI(), Settings(secret_key=key, password_hash=pw_hash, auth_enabled=True))


def test_concurrent_wrong_passwords_capped_at_five(pw_hash, monkeypatch):
    import aliasfox.auth as auth

    calls = []
    real = auth.verify_password

    def counting(h, p):
        calls.append(p)
        return real(h, p)

    monkeypatch.setattr(auth, "verify_password", counting)
    app = build(pw_hash)
    barrier = threading.Barrier(20)

    def attempt(_):
        c = client(app)
        barrier.wait()
        return login(c, "bad").status_code

    with ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(attempt, range(20)))
    assert len(calls) <= 5
    assert codes.count(401) == len(calls)
    assert codes.count(429) == 20 - len(calls)


def test_ipv6_clients_keyed_by_slash_64(pw_hash):
    c = client(build(pw_hash, trusted_proxy=True))
    for i in range(5):
        login(c, "bad", headers={"X-Forwarded-For": f"2001:db8:1:2::{i + 1}"})
    r = login(c, "bad", headers={"X-Forwarded-For": "2001:db8:1:2:ffff::9"})
    assert r.status_code == 429
    r = login(c, "bad", headers={"X-Forwarded-For": "2001:db8:1:3::1"})
    assert r.status_code == 401


def _fresh_ip_headers(i):
    return {"X-Forwarded-For": f"10.{i // 250}.{(i // 25) % 10}.{i % 25 + 1}"}


def test_no_global_failure_lockout_many_ips_never_block_owner(pw_hash):
    app = build(pw_hash, trusted_proxy=True)
    barrier = threading.Barrier(12)

    def attempt(i):
        barrier.wait()
        return login(client(app), "bad", headers=_fresh_ip_headers(i)).status_code

    with ThreadPoolExecutor(12) as ex:
        codes = list(ex.map(attempt, range(12)))
    assert set(codes) <= {401, 429}
    # 60 more failures from distinct IPs: no global budget exists to exhaust
    for i in range(100, 160):
        assert login(client(app), "bad", headers=_fresh_ip_headers(i)).status_code == 401
    r = login(client(app), PASSWORD, headers={"X-Forwarded-For": "11.1.1.1"})
    assert r.status_code == 200


def test_no_global_limiter_left_in_auth_module():
    import aliasfox.auth as auth

    assert not hasattr(auth, "GLOBAL_MAX_FAILURES")


def test_password_verification_concurrency_capped_at_two(pw_hash, monkeypatch):
    import aliasfox.auth as auth

    lock = threading.Lock()
    state = {"now": 0, "max": 0, "calls": 0}

    def slow_verify(h, p):
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        return False

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    app = build(pw_hash, trusted_proxy=True)
    barrier = threading.Barrier(8)

    def attempt(i):
        barrier.wait()
        return login(client(app), "bad", headers=_fresh_ip_headers(i)).status_code

    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(attempt, range(8)))
    assert state["max"] == 2
    assert state["calls"] == 8
    assert codes == [401] * 8


def test_busy_returns_429_after_wait_and_does_not_burn_ip_budget(pw_hash, monkeypatch):
    import aliasfox.auth as auth

    release = threading.Event()
    entered = threading.Semaphore(0)

    def blocking_verify(h, p):
        entered.release()
        release.wait(10)
        return False

    monkeypatch.setattr(auth, "verify_password", blocking_verify)
    monkeypatch.setattr(auth, "LOGIN_SLOT_WAIT_SECONDS", 0.2)
    assert auth.LOGIN_CONCURRENCY == 2
    app = build(pw_hash, trusted_proxy=True)
    with ThreadPoolExecutor(2) as ex:
        futs = [
            ex.submit(lambda i=i: login(client(app), "bad", headers=_fresh_ip_headers(i)))
            for i in range(2)
        ]
        entered.acquire(timeout=5)
        entered.acquire(timeout=5)
        same_ip = {"X-Forwarded-For": "99.9.9.9"}
        for _ in range(8):  # busy responses must not consume the IP failure budget
            r = login(client(app), "bad", headers=same_ip)
            assert r.status_code == 429
            assert r.json() == {"detail": "busy"}
            assert r.headers["retry-after"] == "5"
        release.set()
        assert [f.result().status_code for f in futs] == [401, 401]
    monkeypatch.setattr(auth, "verify_password", lambda h, p: False)
    assert login(client(app), "bad", headers=same_ip).status_code == 401


def test_password_max_length(pw_hash):
    c = client(build(pw_hash))
    assert login(c, "x" * 1025).status_code == 422
    assert login(c, "x" * 1024).status_code == 401


def test_limiter_key_maps_ipv4_mapped_ipv6_to_ipv4():
    from aliasfox.auth import limiter_key

    assert limiter_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert limiter_key("::ffff:203.0.113.8") != limiter_key("::ffff:203.0.113.7")
    assert limiter_key("203.0.113.7") == "203.0.113.7"
    assert limiter_key("2001:db8::1") == limiter_key("2001:db8::2")  # /64 still collapses


# --- A4: revocable sessions (session epoch) ---------------------------------------


def test_cookie_payload_carries_epoch(pw_hash):
    app = build(pw_hash)
    c = client(app)
    login(c)
    assert serializer().loads(c.cookies["aliasfox_session"]) == {"u": 1, "e": 0}
    app.state.epochs.bump(1)
    c2 = client(app)
    login(c2)
    assert serializer().loads(c2.cookies["aliasfox_session"]) == {"u": 1, "e": 1}


def test_replayed_cookie_after_logout_is_401(pw_hash):
    c = client(build(pw_hash))
    login(c)
    stolen = c.cookies["aliasfox_session"]
    assert c.get("/api/protected").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    replay = client(c.app)
    replay.cookies.set("aliasfox_session", stolen)
    assert replay.get("/api/protected").status_code == 401


def test_logout_revokes_every_outstanding_cookie(pw_hash):
    app = build(pw_hash)
    laptop, phone = client(app), client(app)
    login(laptop)
    login(phone)
    assert phone.get("/api/protected").status_code == 200
    laptop.post("/api/auth/logout")
    assert phone.get("/api/protected").status_code == 401
    # a fresh login works again and carries the new epoch
    assert login(laptop).status_code == 200
    assert laptop.get("/api/protected").status_code == 200


def test_epoch_mismatch_is_401(pw_hash):
    app = build(pw_hash)
    c = client(app)
    for bad in (1, -1, 7, "0", None, True):
        c.cookies.set("aliasfox_session", serializer().dumps({"u": 1, "e": bad}))
        assert c.get("/api/protected").status_code == 401, bad


def test_cookie_without_epoch_rejected(pw_hash):
    c = client(build(pw_hash))
    c.cookies.set("aliasfox_session", serializer().dumps({"u": 1}))
    assert c.get("/api/protected").status_code == 401


def test_unauthenticated_or_stale_logout_does_not_bump_epoch(pw_hash):
    app = build(pw_hash)
    anon = client(app)
    assert anon.post("/api/auth/logout").status_code == 200
    assert app.state.epochs.get(1) == 0
    stale = client(app)
    stale.cookies.set("aliasfox_session", serializer().dumps({"u": 1, "e": 3}))
    stale.post("/api/auth/logout")
    assert app.state.epochs.get(1) == 0


def test_db_epoch_store_and_current_user(tmp_path):
    from aliasfox.db import init_db, make_engine
    from aliasfox.keys import KeyRing
    from aliasfox.users import UserDirectory

    engine = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
    init_db(engine)
    epochs = UserDirectory(engine, KeyRing(SECRET))
    assert epochs.get(1) is None  # init_db seeds no user
    epochs.ensure_single_user()
    assert epochs.get(1) == 0
    assert epochs.bump(1) == 1
    assert epochs.get(1) == 1
    assert epochs.get(999) is None


def test_init_db_adds_missing_epoch_column_to_old_dev_db(tmp_path):
    import sqlite3

    from aliasfox.db import init_db, make_engine

    path = tmp_path / "aliasfox.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, created_at DATETIME)")
    con.execute("INSERT INTO users (id) VALUES (1)")
    con.commit()
    con.close()
    engine = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
    init_db(engine)
    from aliasfox.keys import KeyRing
    from aliasfox.users import UserDirectory

    assert UserDirectory(engine, KeyRing(SECRET)).get(1) == 0


def test_create_app_wires_db_epochs_so_logout_survives_restart(tmp_path, pw_hash):
    from aliasfox.main import create_app

    settings = Settings(
        secret_key=SECRET, data_dir=tmp_path, auth_enabled=True, password_hash=pw_hash
    )
    c = client(create_app(settings), base="http://localhost")
    login(c)
    stolen = c.cookies["aliasfox_session"]
    c.post("/api/auth/logout")
    restarted = client(create_app(settings), base="http://localhost")
    restarted.cookies.set("aliasfox_session", stolen)
    assert restarted.get("/api/status").status_code == 401


# --- B7: trusted_proxy_hops -------------------------------------------------------------


def test_client_ip_nth_from_right():
    r = Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4"})
    assert client_ip(r, trusted_proxy=True, hops=1) == "4.4.4.4"
    assert client_ip(r, trusted_proxy=True, hops=2) == "3.3.3.3"
    assert client_ip(r, trusted_proxy=True, hops=4) == "1.1.1.1"
    assert client_ip(r, trusted_proxy=False, hops=2) == "10.0.0.1"


def test_client_ip_fewer_entries_than_hops_falls_back_to_peer():
    r = Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2"})
    assert client_ip(r, trusted_proxy=True, hops=3) == "10.0.0.1"


def test_trusted_proxy_hops_setting_default_and_env(monkeypatch):
    assert Settings().trusted_proxy_hops == 1
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert Settings().trusted_proxy_hops == 2
    with pytest.raises(ValueError):
        Settings(trusted_proxy_hops=0)


def test_login_throttle_uses_configured_hops(pw_hash):
    c = client(build(pw_hash, trusted_proxy=True, trusted_proxy_hops=2))
    for i in range(5):
        # client-controlled left entry rotates; the 2nd-from-right (the real client) is constant
        login(c, "bad", headers={"X-Forwarded-For": f"9.9.9.{i}, 7.7.7.7, 10.1.1.1"})
    r = login(c, "bad", headers={"X-Forwarded-For": "1.1.1.1, 7.7.7.7, 10.1.1.1"})
    assert r.status_code == 429
    r = login(c, "bad", headers={"X-Forwarded-For": "1.1.1.1, 8.8.8.8, 10.1.1.1"})
    assert r.status_code == 401

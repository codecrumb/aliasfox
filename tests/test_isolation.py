"""Per-user isolation, end to end (design section 5.3).

Users A and B sign in through the real exchange route (only the Neon verifier
is faked), each with their own TestClient cookie jar, against a real SQLite
file. Apple is one fake "iCloud" whose accounts are named after the session
cookie, so any cross-user leak shows up as the other account's aliases.
"""
import base64
import re

import pytest
from fastapi.routing import APIRoute
from helpers import (
    APPLE_CODE,
    MULTI_SECRET,
    MultiUserEnv,
)
from starlette.routing import Mount

import aliasfox.main as main_mod
from aliasfox.auth import COOKIE_NAME
from aliasfox.config import Settings
from aliasfox.crypto import derive_cache_key
from aliasfox.keys import KeyRing

LOGIN = {"password": "apple-pw-SECRET"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a", "b", "c"])


@pytest.fixture
def ab(env):
    """A and B signed in and connected to their own Apple accounts."""
    ca, a = env.sign_in("a")
    cb, b = env.sign_in("b")
    assert a != b
    env.connect(ca, "acct-a")
    env.connect(cb, "acct-b")
    return ca, a, cb, b


def alias_ids(r):
    assert r.status_code == 200, r.text
    return sorted(x["anonymous_id"] for x in r.json())


def cache_key(c):
    r = c.get("/api/session")
    assert r.status_code == 200, r.text
    return r.json()["cache_key"]


def expected_cache_key(uid, epoch):
    raw = derive_cache_key(MULTI_SECRET, uid, epoch)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- reads -------------------------------------------------------------------------------


def test_a_never_sees_b_aliases_status_or_session_key(env):
    ca, a = env.sign_in("a")
    cb, b = env.sign_in("b")
    env.connect(ca, "acct-a")

    # B is not connected: B must get not_connected, never A's (cached) list.
    assert alias_ids(ca.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]
    r = cb.get("/api/aliases")
    assert r.status_code == 409 and r.json() == {"detail": "not_connected"}
    assert cb.get("/api/status").json()["connected"] is False
    assert ca.get("/api/status").json()["connected"] is True

    env.connect(cb, "acct-b")
    for refresh in ("false", "true"):
        ra = ca.get(f"/api/aliases?refresh={refresh}")
        rb = cb.get(f"/api/aliases?refresh={refresh}")
        assert alias_ids(ra) == ["acct-a-id-0", "acct-a-id-1"]
        assert alias_ids(rb) == ["acct-b-id-0", "acct-b-id-1"]
        assert "acct-b" not in ra.text and "acct-a" not in rb.text
    assert cb.get("/api/status").json()["connected"] is True

    ka, kb = cache_key(ca), cache_key(cb)
    assert ka != kb
    assert ka == expected_cache_key(a, env.users.get(a))
    assert kb == expected_cache_key(b, env.users.get(b))
    assert env.world.ops_on(None) == []


# --- user id smuggled through request input ----------------------------------------------


def test_user_id_in_body_query_header_path_is_ignored(env, ab):
    ca, a, cb, b = ab
    q = f"user_id={b}&u={b}&user={b}&uid={b}"
    hdr = {"X-User-Id": str(b), "X-User": str(b), "X-Uid": str(b)}
    extra = {"user_id": b, "u": b, "uid": b}
    env.world.add_account("acct-a2")
    env.world.add_account("acct-a3")
    b_world = env.world.snapshot("acct-b")
    b_blob = env.blob(b)
    b_key = env.wrapped_key(b)
    b_epoch = env.users.get(b)

    def b_untouched():
        assert env.world.snapshot("acct-b") == b_world
        assert env.blob(b) == b_blob
        assert env.wrapped_key(b) == b_key
        assert env.users.get(b) == b_epoch
        assert env.world.ops_on("acct-b") == []  # A never ran anything on B's account
        assert env.store.load(b).cookies == {"acct": "acct-b"}

    def call(method, path, body=None):
        sep = "&" if "?" in path else "?"
        kw = {"headers": hdr}
        if body is not None:
            kw["json"] = {**body, **extra}
        return ca.request(method, f"{path}{sep}{q}", **kw)

    # Reads.
    assert alias_ids(call("GET", "/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]
    assert alias_ids(call("GET", "/api/aliases?refresh=true")) == ["acct-a-id-0", "acct-a-id-1"]
    assert call("GET", "/api/session").json()["cache_key"] == expected_cache_key(a, env.users.get(a))
    # User id in the path: there is no such route, and an id-shaped alias id is
    # looked up in A's own Apple account only.
    assert call("GET", f"/api/users/{b}/aliases").status_code == 404
    r = call("DELETE", f"/api/aliases/{b}")
    assert r.status_code == 502 and r.json()["code"] == "not_found"
    b_untouched()

    # Every mutating route.
    r = call("POST", "/api/aliases/generate", {})
    assert r.status_code == 200 and r.json()["hme"].startswith("acct-a-gen-")
    r = call("POST", "/api/aliases", {"hme": "new@privaterelay.appleid.com", "label": "x"})
    assert r.status_code == 200 and r.json()["anonymous_id"].startswith("acct-a-")
    assert call("PATCH", "/api/aliases/acct-a-id-0", {"label": "renamed"}).status_code == 200
    assert call("POST", "/api/aliases/acct-a-id-0/active", {"active": False}).status_code == 200
    assert call("DELETE", "/api/aliases/acct-a-id-1", {}).status_code == 200
    assert call("PUT", "/api/forward-to", {"address": "a-new@real.example"}).status_code == 200
    acct_a = env.world.accounts["acct-a"]
    assert acct_a["aliases"]["acct-a-id-0"].label == "renamed"
    assert acct_a["aliases"]["acct-a-id-0"].is_active is False
    assert "acct-a-id-1" not in acct_a["aliases"]
    assert acct_a["forward_to"] == "a-new@real.example"
    b_untouched()

    assert call("POST", "/api/connect/cookie", {"cookie": "acct-a2"}).status_code == 200
    assert env.store.load(a).cookies == {"acct": "acct-a2"}
    b_untouched()

    r = call("POST", "/api/connect/login", {"apple_id": "acct-a3", **LOGIN})
    assert r.status_code == 200 and r.json()["status"] == "needs_2fa"
    flow_id = r.json()["flow_id"]
    assert call("POST", "/api/connect/resend", {"flow_id": flow_id}).status_code == 200
    r = call("POST", "/api/connect/verify", {"flow_id": flow_id, "code": APPLE_CODE})
    assert r.status_code == 200, r.text
    assert env.store.load(a).cookies == {"acct": "acct-a3"}
    b_untouched()

    assert call("DELETE", "/api/connect", {}).status_code == 200
    assert env.store.load(a) is None
    assert cb.get("/api/status").json()["connected"] is True
    b_untouched()

    a_epoch = env.users.get(a)
    assert call("POST", "/api/auth/logout", {}).status_code == 200
    assert env.users.get(a) == a_epoch + 1
    assert cb.get("/api/status").status_code == 200
    b_untouched()

    env.resign_in(ca, "a")
    assert call("DELETE", "/api/account", {}).status_code == 200
    assert env.users.get(a) is None
    b_untouched()
    # B's own (uncached) read still works and is the first call ever made as B.
    assert alias_ids(cb.get("/api/aliases")) == ["acct-b-id-0", "acct-b-id-1"]
    assert env.world.ops_on("acct-b") == ["list"]


def test_a_cannot_modify_or_delete_b_alias_by_id(env, ab):
    ca, a, cb, b = ab
    before = env.world.snapshot("acct-b")
    assert alias_ids(cb.get("/api/aliases")) == ["acct-b-id-0", "acct-b-id-1"]
    for method, path, body in (
        ("PATCH", "/api/aliases/acct-b-id-0", {"label": "pwned", "note": "pwned"}),
        ("POST", "/api/aliases/acct-b-id-0/active", {"active": False}),
        ("DELETE", "/api/aliases/acct-b-id-0", None),
    ):
        r = ca.request(method, path, json=body)
        assert r.status_code == 502, r.text
        assert r.json()["detail"] == "apple_error" and r.json()["code"] == "not_found"
    # Each attempt ran against A's own Apple account, never B's.
    assert env.world.ops_on("acct-b") == ["list"]
    assert env.world.ops_on("acct-a") == ["update_metadata", "set_active", "delete"]
    assert env.world.snapshot("acct-b") == before
    assert alias_ids(cb.get("/api/aliases?refresh=true")) == ["acct-b-id-0", "acct-b-id-1"]
    assert all(x["label"] != "pwned" and x["is_active"] for x in cb.get("/api/aliases").json())


# --- ciphertext moved between rows -------------------------------------------------------


def test_b_blob_in_a_row_is_session_unreadable(env, ab):
    ca, a, cb, b = ab
    env.raw("update apple_sessions set blob=:blob where user_id=:a", blob=env.blob(b), a=a)
    r = ca.get("/api/aliases?refresh=true")
    assert r.status_code == 409 and r.json() == {"detail": "session_unreadable"}
    assert "acct-b" not in r.text
    st = ca.get("/api/status").json()
    assert st["connected"] is False and st["reason"] == "session_unreadable"
    assert env.world.ops_on("acct-b") == []  # nothing was ever sent to Apple as B
    assert alias_ids(cb.get("/api/aliases")) == ["acct-b-id-0", "acct-b-id-1"]


def test_a_wrapped_key_in_b_row_is_session_unreadable(env, ab):
    ca, a, cb, b = ab
    # The strongest form: move A's wrapped key AND A's blob into B's rows, so
    # B's row holds a complete, internally consistent copy of A's secret.
    env.raw("update users set wrapped_key=:k where id=:b", k=env.wrapped_key(a), b=b)
    r = cb.get("/api/aliases?refresh=true")
    assert r.status_code == 409 and r.json() == {"detail": "session_unreadable"}
    env.raw("update apple_sessions set blob=:blob where user_id=:b", blob=env.blob(a), b=b)
    for path in ("/api/aliases?refresh=true", "/api/aliases"):
        r = cb.get(path)
        assert r.status_code == 409 and r.json() == {"detail": "session_unreadable"}
        assert "acct-a" not in r.text
    st = cb.get("/api/status").json()
    assert st["connected"] is False and st["reason"] == "session_unreadable"
    assert env.world.ops_on("acct-a") == []  # nothing was ever sent to Apple as A
    assert alias_ids(ca.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]


# --- pending Apple login -----------------------------------------------------------------


def test_a_pending_login_cannot_be_verified_resent_or_cancelled_by_b(env):
    ca, a = env.sign_in("a")
    cb, b = env.sign_in("b")
    env.world.add_account("acct-a")
    r = ca.post("/api/connect/login", json={"apple_id": "acct-a", **LOGIN})
    assert r.status_code == 200 and r.json()["status"] == "needs_2fa"
    flow_id = r.json()["flow_id"]
    api = env.login_apis[-1]

    r = cb.post("/api/connect/verify", json={"flow_id": flow_id, "code": APPLE_CODE})
    assert r.status_code == 410 and r.json() == {"detail": "flow_expired"}
    for _ in range(5):  # B cannot burn A's code attempts either
        r = cb.post("/api/connect/verify", json={"flow_id": flow_id, "code": "000000"})
        assert r.status_code == 410
    r = cb.post("/api/connect/resend", json={"flow_id": flow_id})
    assert r.status_code == 410 and r.json() == {"detail": "flow_expired"}
    assert cb.delete("/api/connect").status_code == 200  # cancels B's flows only
    # B starting its own login cancels B's previous flow, not A's.
    assert cb.post("/api/connect/login", json={"apple_id": "acct-b", **LOGIN}).status_code == 200

    assert api.validations == 0
    assert api.code_requests == 1  # start()'s own request; B's resend never reached Apple
    assert env.store.load(b) is None

    r = ca.post("/api/connect/verify", json={"flow_id": flow_id, "code": APPLE_CODE})
    assert r.status_code == 200, r.text
    assert env.store.load(a).cookies == {"acct": "acct-a"}
    assert env.store.load(b) is None
    assert cb.get("/api/status").json()["connected"] is False


# --- disconnect, logout, cache key -------------------------------------------------------


def test_a_disconnect_leaves_b_connected(env, ab):
    ca, a, cb, b = ab
    assert alias_ids(ca.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]
    assert alias_ids(cb.get("/api/aliases")) == ["acct-b-id-0", "acct-b-id-1"]
    b_blob = env.blob(b)
    assert ca.delete("/api/connect").json() == {"status": "disconnected"}
    assert env.blob(a) is None and env.blob(b) == b_blob
    assert ca.get("/api/status").json()["connected"] is False
    assert ca.get("/api/aliases").status_code == 409  # A's cache dropped
    assert cb.get("/api/status").json()["connected"] is True
    assert alias_ids(cb.get("/api/aliases?refresh=true")) == ["acct-b-id-0", "acct-b-id-1"]


def test_a_logout_does_not_log_out_b(env, ab):
    ca, a, cb, b = ab
    a_epoch, b_epoch = env.users.get(a), env.users.get(b)
    stale_a = ca.cookies[COOKIE_NAME]
    assert ca.post("/api/auth/logout").status_code == 200
    assert env.users.get(a) == a_epoch + 1 and env.users.get(b) == b_epoch
    assert ca.get("/api/status").status_code == 401
    assert cb.get("/api/status").json()["connected"] is True
    # A stale cookie hitting logout again bumps nobody.
    replay = env.anonymous()
    replay.cookies.set(COOKIE_NAME, stale_a)
    assert replay.post("/api/auth/logout").status_code == 200
    assert env.users.get(a) == a_epoch + 1 and env.users.get(b) == b_epoch
    assert alias_ids(cb.get("/api/aliases")) == ["acct-b-id-0", "acct-b-id-1"]


def test_logout_drops_that_users_alias_cache_only(env, ab):
    ca, a, cb, b = ab
    ca.get("/api/aliases")
    cb.get("/api/aliases")
    assert {a, b} <= set(env.service._cache)
    assert ca.post("/api/auth/logout").status_code == 200
    assert a not in env.service._cache and b in env.service._cache
    # a stale or missing cookie drops nothing
    env.anonymous().post("/api/auth/logout")
    assert b in env.service._cache


def test_cache_key_differs_per_user_and_changes_on_logout(env, ab):
    ca, a, cb, b = ab
    ka1, kb1 = cache_key(ca), cache_key(cb)
    assert ka1 != kb1
    assert ca.post("/api/auth/logout").status_code == 200
    assert ca.get("/api/session").status_code == 401
    env.resign_in(ca, "a")
    ka2 = cache_key(ca)
    assert ka2 != ka1 and ka2 != kb1
    assert ka2 == expected_cache_key(a, env.users.get(a))
    assert cache_key(cb) == kb1


# --- route coverage ----------------------------------------------------------------------

# Multi mode's static public pages (pages.py); `/` serves the SPA when signed in.
PAGE_ROUTES = {(m, p) for m in ("GET", "HEAD") for p in ("/", "/login", "/signup", "/privacy", "/terms", "/site.HASH.css", "/hero.HASH.js", "/hero3d.HASH.js")}
PUBLIC = {
    ("GET", "/healthz"),
    ("HEAD", "/healthz"),
    ("GET", "/robots.txt"),
    ("HEAD", "/robots.txt"),
    *{(m, p) for m in ("GET", "HEAD") for p in ("/llms.txt", "/.well-known/ard.json")},  # both modes
    ("GET", "/api/config"),
    ("GET", "/mcp"),  # answers 405 Allow: POST for everyone, no data
    ("POST", "/api/auth/exchange"),
    ("POST", "/api/auth/logout"),
    ("GET", "/{full_path:spa_path}"),  # SPA shell and public files from the build
    ("HEAD", "/{full_path:spa_path}"),
    ("GET", "/.well-known/{rest:path}"),
    ("HEAD", "/.well-known/{rest:path}"),
    *PAGE_ROUTES,
}
PUBLIC_MOUNTS = {"/assets"}  # hashed static build assets


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>x</title>")
    (d / "assets" / "app.js").write_text("1")
    return d


def route_table(app):
    """{(method, path)} for every APIRoute; fails on any other kind of route
    (so a new mount or raw Starlette route has to be reviewed here)."""
    table = set()

    def walk(routes, prefix=""):
        for route in routes:
            inner = getattr(route, "original_router", None)  # include_router wraps routers
            if inner is not None:
                walk(inner.routes, prefix + route.include_context.prefix)
            elif isinstance(route, APIRoute):
                path = re.sub(r"\.[0-9a-f]{10}\.(css|js)$", r".HASH.\1", prefix + route.path)
                table.update((m, path) for m in route.methods)
            elif isinstance(route, Mount) and prefix + route.path in PUBLIC_MOUNTS:
                continue
            else:
                pytest.fail(f"unreviewed route kind {type(route).__name__}: {route!r}")

    walk(app.routes)
    return table


def concrete(path):
    return re.sub(r"\{[^}]+\}", "dummy-id", path)


def test_every_route_requires_auth_or_is_allowlisted(tmp_path, monkeypatch, dist):
    env = MultiUserEnv(tmp_path, monkeypatch, ["a"], dist_dir=dist)
    table = route_table(env.app)
    assert PUBLIC <= table, "allow-list names a route that no longer exists"
    anon = env.anonymous()
    garbage = env.anonymous()
    garbage.cookies.set(COOKIE_NAME, "not-a-signed-cookie")
    checked = []
    for method, path in sorted(table - PUBLIC):
        for c in (anon, garbage):
            kw = {} if method in ("GET", "HEAD") else {"json": {}}
            r = c.request(method, concrete(path), **kw)
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without auth"
        checked.append((method, path))
    # Sanity: the sweep really covered the API surface.
    for must in (("GET", "/api/aliases"), ("DELETE", "/api/account"), ("POST", "/api/connect/login")):
        assert must in checked


def test_mode_specific_routes(tmp_path, monkeypatch, dist):
    (tmp_path / "m").mkdir()
    multi = route_table(MultiUserEnv(tmp_path / "m", monkeypatch, ["a"], dist_dir=dist).app)
    single_settings = Settings(secret_key=MULTI_SECRET, data_dir=tmp_path / "s")
    single = route_table(main_mod.create_app(single_settings, dist_dir=dist))
    assert single - multi == {("POST", "/api/auth/login")}
    assert multi - single == {
        ("DELETE", "/api/account"),
        ("POST", "/api/auth/exchange"),
        *PAGE_ROUTES,
    }


# --- id reuse after account deletion -----------------------------------------------------


def test_reused_user_id_inherits_nothing_from_deleted_account(env):
    """SQLite reuses the highest deleted id. A brand-new user C who gets deleted
    user A's id must not see A's cached aliases, finish A's pending Apple login,
    inherit A's create/login rate-limit budget, or be reachable with A's cookie."""
    cb, b = env.sign_in("b")
    ca, a = env.sign_in("a")
    assert a > b  # A holds the highest id, so SQLite hands it out again
    env.connect(ca, "acct-a")
    assert alias_ids(ca.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]  # cached
    for _ in range(10):  # exhaust A's per-user create budget
        assert ca.post("/api/aliases/generate").status_code == 200
    assert ca.post("/api/aliases/generate").status_code == 429
    for _ in range(5):  # exhaust A's per-user Apple login budget, leaving a pending flow
        r = ca.post("/api/connect/login", json={"apple_id": "acct-a", **LOGIN})
        assert r.status_code == 200 and r.json()["status"] == "needs_2fa"
    flow_id = r.json()["flow_id"]
    old_key = cache_key(ca)
    stale_a = ca.cookies[COOKIE_NAME]
    assert ca.delete("/api/account").status_code == 200

    cc, c = env.sign_in("c")
    assert c == a, "precondition: SQLite reused the deleted id"
    r = cc.get("/api/aliases")
    assert r.status_code == 409 and r.json() == {"detail": "not_connected"}
    assert "acct-a" not in r.text
    r = cc.post("/api/connect/verify", json={"flow_id": flow_id, "code": APPLE_CODE})
    assert r.status_code == 410
    assert env.store.load(c) is None
    assert cache_key(cc) != old_key
    stale = env.anonymous()
    stale.cookies.set(COOKIE_NAME, stale_a)
    assert stale.get("/api/status").status_code == 401

    env.world.add_account("acct-c")
    r = cc.post("/api/connect/login", json={"apple_id": "acct-c", **LOGIN})
    assert r.status_code == 200, r.text  # fresh per-user login budget
    env.connect(cc, "acct-c")
    assert cc.post("/api/aliases/generate").status_code == 200  # fresh create budget
    assert alias_ids(cc.get("/api/aliases")) == ["acct-c-id-0", "acct-c-id-1"]
    assert env.store.load(b) is None and cb.get("/api/status").json()["connected"] is False
    assert env.store.load(c).cookies == {"acct": "acct-c"}


# --- ALIASFOX_SECRET_KEY rotation in multi mode (final review F1) ------------------------


ROTATED_SECRET = "rotated-multi-secret-0123456789abcdefgh"


def test_reconnect_after_secret_rotation_rekeys_only_that_user(tmp_path, monkeypatch, caplog):
    before = MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])
    ca, a = before.sign_in("a")
    cb, b = before.sign_in("b")
    before.connect(ca, "acct-a")
    before.connect(cb, "acct-b")
    b_row = before.raw(
        "select blob, updated_at from apple_sessions where user_id=:u", u=b
    ).one()
    b_key = before.wrapped_key(b)

    # Same database file, new secret: every stored session is now unreadable.
    after = MultiUserEnv(tmp_path, monkeypatch, ["a", "b"], secret=ROTATED_SECRET)
    ca2, a2 = after.sign_in("a")
    assert a2 == a
    assert ca2.get("/api/status").json()["reason"] == "session_unreadable"
    with caplog.at_level("DEBUG"):
        after.connect(ca2, "acct-a")  # reconnecting re-keys user A and succeeds
    assert ca2.get("/api/status").json()["connected"] is True
    assert alias_ids(ca2.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]
    assert after.store.load(a).cookies == {"acct": "acct-a"}
    KeyRing(ROTATED_SECRET).unwrap(a, after.wrapped_key(a))  # A re-keyed under the new secret
    # B's rows are not touched (still unreadable under the new secret until B reconnects).
    assert after.wrapped_key(b) == b_key
    assert after.raw(
        "select blob, updated_at from apple_sessions where user_id=:u", u=b
    ).one() == b_row
    cb2, _ = after.sign_in("b")
    assert cb2.get("/api/status").json()["reason"] == "session_unreadable"
    for record in caplog.records:
        assert "acct-a" not in record.getMessage()


# --- API keys: isolation and lifecycle ---------------------------------------------------


def _bearer(env, uid, scopes=("read", "create", "edit", "deactivate", "delete")):
    key, _ = env.app.state.api_keys.create(uid, "k", list(scopes))
    return {"Authorization": f"Bearer {key}"}


def test_a_key_never_touches_b_aliases(env, ab):
    ca, a, cb, b = ab
    h = _bearer(env, a)
    c = env.anonymous()
    b_world = env.world.snapshot("acct-b")
    env.world.calls.clear()
    assert alias_ids(c.get("/api/v1/aliases", headers=h)) == ["acct-a-id-0", "acct-a-id-1"]
    for method, path, body in [
        ("PATCH", "/api/v1/aliases/acct-b-id-0", {"label": "hack"}),
        ("POST", "/api/v1/aliases/acct-b-id-0/active", {"active": False}),
        ("DELETE", "/api/v1/aliases/acct-b-id-0", None),
    ]:
        assert c.request(method, path, headers=h, json=body).status_code == 502, path
    assert c.post("/api/v1/aliases", headers=h, json={"label": "x", "user_id": b}).status_code == 201
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": "delete_alias", "arguments": {"id": "acct-b-id-1"}}})
    assert r.json()["result"]["isError"] is True
    assert env.world.snapshot("acct-b") == b_world
    assert env.world.ops_on("acct-b") == []
    assert env.store.load(b).cookies == {"acct": "acct-b"}


def test_deleted_account_keys_401_with_hot_cache(env, ab):
    ca, a, cb, b = ab
    h = _bearer(env, a)
    c = env.anonymous()
    assert c.get("/api/v1/me", headers=h).status_code == 200  # now cached
    assert env.app.state.key_auth._cache
    assert ca.delete("/api/account").status_code == 200
    assert c.get("/api/v1/me", headers=h).status_code == 401
    assert env.anonymous().get("/api/v1/me", headers=_bearer(env, b)).status_code == 200  # B unaffected


def test_account_delete_resets_create_guard_for_reused_id(env, ab):
    ca, a, cb, b = ab
    guard = env.app.state.create_guard
    for _ in range(3):
        guard.record_error(a)
    assert guard.check(a) is not None  # cooling down
    assert ca.delete("/api/account").status_code == 200
    assert guard.check(a) is None


def test_denylisted_subject_keys_deleted_at_startup(env, ab):
    ca, a, cb, b = ab
    _bearer(env, a)
    _bearer(env, b)
    denied = env.settings.model_copy(update={"subject_denylist": "sub-a"})
    main_mod.create_app(denied, service=env.service, store=env.store, login_flow=env.flow)
    assert env.app.state.api_keys.list_for_user(a) == []
    assert len(env.app.state.api_keys.list_for_user(b)) == 1


def test_api_keys_disabled_makes_key_routes_503_but_ui_works(env, ab):
    from fastapi.testclient import TestClient

    ca, a, cb, b = ab
    h = _bearer(env, a)
    off = main_mod.create_app(
        env.settings.model_copy(update={"api_keys_enabled": False}),
        service=env.service, store=env.store, login_flow=env.flow,
    )
    c = TestClient(off, base_url="https://aliasfox.test", headers={"X-Forwarded-For": "203.0.113.9"})
    for method, path in [("GET", "/api/v1/me"), ("GET", "/api/v1/aliases"), ("POST", "/api/alias/random/new")]:
        assert c.request(method, path, headers=h).status_code == 503, path
    assert c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 503
    assert c.post("/api/auth/exchange", json={"token": "tok-a"}).status_code == 200
    assert alias_ids(c.get("/api/aliases")) == ["acct-a-id-0", "acct-a-id-1"]


def test_key_resolved_during_account_delete_never_serves_the_reused_id(env, ab, monkeypatch):
    from aliasfox.users import UserDirectory

    ca, a, cb, b = ab
    h = _bearer(env, b)
    real = UserDirectory.delete

    def delete_with_racing_key_request(self, user_id):
        # a key request landing between the first invalidation and the row delete re-caches the row
        assert env.anonymous().get("/api/v1/me", headers=h).status_code == 200
        real(self, user_id)

    monkeypatch.setattr(UserDirectory, "delete", delete_with_racing_key_request)
    assert cb.delete("/api/account").status_code == 200
    monkeypatch.undo()
    cc, c = env.sign_in("c")
    assert c == b  # SQLite reuses the highest deleted id
    assert env.anonymous().get("/api/v1/me", headers=h).status_code == 401

"""Scope matrix and cookie/key separation across every route (design sections 3 and 6)."""
import pytest
from helpers import MultiUserEnv
from test_isolation import PUBLIC, concrete, dist, route_table  # noqa: F401  (dist is a fixture)

from aliasfox.apikeys import SCOPES
from aliasfox.middleware import is_key_path

SHIM = "/api/alias/random/new"
# (method, path, json body, required scope or None for any valid key). "!" ids exist in acct-a.
HTTP_ROUTES = [
    ("GET", "/api/v1/me", None, None),
    ("GET", "/api/v1/aliases", None, "read"),
    ("POST", "/api/v1/aliases", {"label": "x"}, "create"),
    ("PATCH", "/api/v1/aliases/acct-a-id-0", {"label": "z"}, "edit"),
    ("POST", "/api/v1/aliases/acct-a-id-0/active", {"active": False}, "deactivate"),
    ("DELETE", "/api/v1/aliases/acct-a-id-0", None, "delete"),
    ("POST", SHIM, {}, "create"),
]
MCP_TOOLS = [
    ("list_aliases", {}, "read"),
    ("create_alias", {"label": "x"}, "create"),
    ("update_alias", {"id": "acct-a-id-0", "label": "z"}, "edit"),
    ("set_alias_active", {"id": "acct-a-id-0", "active": False}, "deactivate"),
    ("delete_alias", {"id": "acct-a-id-0"}, "delete"),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a"])


def key_with(env, scopes):
    c, uid = env.sign_in("a")
    env.connect(c, "acct-a")
    key, _ = env.app.state.api_keys.create(uid, "k", list(scopes))
    return key


def mcp_call(env, key, name, args):
    r = env.anonymous().post(
        "/mcp", headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
    )
    assert r.status_code == 200
    return r.json()["result"]


@pytest.mark.parametrize("scope", SCOPES)
def test_scope_matrix(env, scope):
    key = key_with(env, [scope])
    for method, path, body, needed in HTTP_ROUTES:
        env.world.add_account("acct-a")  # fresh aliases for every probe
        r = env.anonymous().request(method, path, headers={"Authorization": f"Bearer {key}"}, json=body)
        if needed in (None, scope):
            assert r.status_code in (200, 201), (scope, method, path, r.status_code)
        else:
            assert r.status_code == 403, (scope, method, path, r.status_code)
            expect = {"error": "insufficient_scope", "needed": needed}
            assert r.json() == ({"error": "This key cannot create addresses"} if path == SHIM else {"detail": expect})
    for name, args, needed in MCP_TOOLS:
        env.world.add_account("acct-a")
        res = mcp_call(env, key, name, args)
        if needed == scope:
            assert res["isError"] is False, (scope, name, res)
        else:
            assert res["isError"] is True and "does not allow" in res["content"][0]["text"], (scope, name)


def test_matrix_covers_every_key_route(tmp_path, monkeypatch, dist):
    env = MultiUserEnv(tmp_path, monkeypatch, ["a"], dist_dir=dist)
    key_routes = {(m, concrete(p)) for m, p in route_table(env.app) if is_key_path(p)}
    covered = {(m, p.replace("acct-a-id-0", "dummy-id")) for m, p, *_ in HTTP_ROUTES} | {("POST", "/mcp"), ("GET", "/mcp")}
    assert key_routes == covered


def test_all_scope_key_is_401_on_every_cookie_route(tmp_path, monkeypatch, dist):
    env = MultiUserEnv(tmp_path, monkeypatch, ["a"], dist_dir=dist)
    key = key_with(env, SCOPES)
    anon, checked = env.anonymous(), []
    for method, path in sorted(route_table(env.app) - PUBLIC):
        if is_key_path(path):
            continue
        kw = {} if method in ("GET", "HEAD") else {"json": {}}
        r = anon.request(method, concrete(path), headers={"Authorization": f"Bearer {key}"}, **kw)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} to an API key"
        checked.append((method, path))
    for must in (("GET", "/api/aliases"), ("GET", "/api/keys"), ("DELETE", "/api/account")):
        assert must in checked


def test_cookie_on_key_routes_is_401(env):
    c, uid = env.sign_in("a")
    env.connect(c, "acct-a")
    for method, path, body, _ in HTTP_ROUTES:
        assert c.request(method, path, json=body).status_code == 401, (method, path)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401

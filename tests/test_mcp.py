"""MCP server: JSON-RPC over POST /mcp."""
import json

import pytest
from helpers import MultiUserEnv

from aliasfox.apple.hme import HmeError


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])


def key_for(env, scopes, account="acct-a", name="a"):
    c, uid = env.sign_in(name)
    if account:
        env.connect(c, account)
    key, _ = env.app.state.api_keys.create(uid, "k", list(scopes))
    return key


def rpc(env, key, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return env.anonymous().post("/mcp", headers={"Authorization": f"Bearer {key}"}, json=msg)


def tool(env, key, name, args=None):
    r = rpc(env, key, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200
    return r.json()


def data(resp):
    assert resp["result"]["isError"] is False, resp
    return json.loads(resp["result"]["content"][0]["text"])


def test_initialize_names_server_aliasfox(env):
    key = key_for(env, ["read"])
    r = rpc(env, key, "initialize", {"protocolVersion": "2024-11-05"}).json()["result"]
    assert r["serverInfo"] == {"name": "aliasfox", "version": "1.0"}
    assert r["protocolVersion"] == "2024-11-05" and r["capabilities"] == {"tools": {}}
    assert rpc(env, key, "initialize", {"protocolVersion": "1999"}).json()["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.parametrize("scopes,n", [(["read"], 1), (["read", "create"], 2), (["read", "create", "edit", "deactivate"], 4),
                                      (["read", "create", "edit", "deactivate", "delete"], 5)])
def test_tools_list_filtered_by_scope(env, scopes, n):
    tools = rpc(env, key_for(env, scopes), "tools/list").json()["result"]["tools"]
    assert len(tools) == n
    for t in tools:
        assert t["inputSchema"]["additionalProperties"] is False and "untrusted" in t["description"]


def test_call_without_scope_is_error_result(env):
    r = tool(env, key_for(env, ["read"]), "create_alias", {"label": "x"})
    assert r["result"]["isError"] is True
    assert r["result"]["content"][0]["text"] == "This key does not allow create_alias (needs scope: create)"


def test_results_never_contain_forward_to_or_real_mailbox(env):
    key = key_for(env, ["read", "create"])
    out = tool(env, key, "list_aliases")
    assert "real.example" not in json.dumps(out) and "forward_to" not in json.dumps(out)
    new = data(tool(env, key, "create_alias", {"label": "L", "note": "N"}))
    assert set(new) == {"id", "address", "label", "note", "active", "created"} and new["label"] == "L"
    assert "real.example" not in json.dumps(data(tool(env, key, "list_aliases")))
    assert data(tool(env, key, "list_aliases"))[0]["created"].endswith("Z")


def test_extra_user_id_argument_rejected(env):
    key = key_for(env, ["read"])
    r = tool(env, key, "list_aliases", {"user_id": 2})
    assert r["error"]["code"] == -32602
    assert tool(env, key, "list_aliases", {"limit": "x"})["error"]["code"] == -32602
    assert tool(env, key, "nope")["error"]["code"] == -32602


def test_list_query_and_limit_clamp(env):
    key = key_for(env, ["read"])
    assert [a["label"] for a in data(tool(env, key, "list_aliases", {"query": "LABEL 1"}))] == ["acct-a label 1"]
    assert [a["id"] for a in data(tool(env, key, "list_aliases"))] == ["acct-a-id-1", "acct-a-id-0"]  # newest first
    assert len(data(tool(env, key, "list_aliases", {"limit": 0}))) == 1
    assert data(tool(env, key, "list_aliases", {"active": False})) == []


def test_update_set_active_delete(env):
    key = key_for(env, ["read", "edit", "deactivate", "delete"])
    assert data(tool(env, key, "update_alias", {"id": "acct-a-id-0", "label": "new"})) == {"ok": True}
    assert data(tool(env, key, "set_alias_active", {"id": "acct-a-id-0", "active": False})) == {"ok": True}
    got = {a["id"]: a for a in data(tool(env, key, "list_aliases"))}
    assert got["acct-a-id-0"]["label"] == "new" and got["acct-a-id-0"]["active"] is False
    data(tool(env, key, "delete_alias", {"id": "acct-a-id-0"}))
    assert [a["id"] for a in data(tool(env, key, "list_aliases"))] == ["acct-a-id-1"]


def test_no_or_bad_key_is_401_at_http_level(env):
    c = env.anonymous()
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and r.json() == {"detail": "invalid_api_key"} and r.headers["www-authenticate"] == "Bearer"
    assert rpc(env, "bad", "ping").status_code == 401


def test_failed_auth_flood_429_retry_after(env):
    r, c = None, env.anonymous()
    for _ in range(25):
        r = c.post("/mcp", headers={"Authorization": "Bearer bad"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_create_counts_against_guard(env):
    key = key_for(env, ["create"])
    codes = [tool(env, key, "create_alias", {"label": "x"})["result"]["isError"] for _ in range(6)]
    assert codes == [False] * 5 + [True]
    assert tool(env, key, "create_alias", {"label": "x"})["result"]["content"][0]["text"].startswith(
        "Too many addresses created. Try again in ")


def test_not_connected_and_apple_error(env, monkeypatch):
    from helpers import FakeHmeClient

    nc = key_for(env, ["read"], account=None, name="b")
    assert tool(env, nc, "list_aliases")["result"]["content"][0]["text"] == (
        "Apple session expired or not connected. Sign in again in AliasFox.")
    key = key_for(env, ["create"])

    def boom(self, hme, label, note=""):
        raise HmeError(-1, "z" * 500)

    monkeypatch.setattr(FakeHmeClient, "reserve", boom)
    r = tool(env, key, "create_alias", {"label": "x"})["result"]
    assert r["isError"] and r["content"][0]["text"] == "iCloud: " + "z" * 200


def test_protocol_edges(env):
    key = key_for(env, ["read"])
    c, h = env.anonymous(), {"Authorization": f"Bearer {key}"}
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""
    assert rpc(env, key, "ping").json()["result"] == {}
    assert rpc(env, key, "nope").json()["error"]["code"] == -32601
    assert rpc(env, key, "tools/call", ["x"]).json()["error"]["code"] == -32602
    r = c.post("/mcp", headers=h, content=b"{bad")
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
    for body in ([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], 5):
        r = c.post("/mcp", headers=h, json=body)
        assert r.status_code == 200 and r.json()["error"]["code"] == -32600
    r = c.get("/mcp")
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert rpc(env, key, "ping").headers["content-type"] == "application/json"


def test_limit_is_clamped_to_200(env):
    env.world.add_account("acct-big", 250)
    key = key_for(env, ["read"], account="acct-big")
    assert len(data(tool(env, key, "list_aliases", {"limit": 9999}))) == 200
    assert len(data(tool(env, key, "list_aliases"))) == 50


def test_scope_is_checked_before_arguments(env):
    key = key_for(env, ["read"])
    for name, args in (("create_alias", {}), ("delete_alias", {"id": 5}), ("update_alias", {"bogus": 1})):
        r = tool(env, key, name, args)["result"]
        assert r["isError"] is True and "does not allow" in r["content"][0]["text"]
    assert tool(env, key, "nope", {})["error"]["code"] == -32602  # unknown tool stays invalid
    assert tool(env, key_for(env, ["create"]), "create_alias", {})["error"]["code"] == -32602


@pytest.mark.parametrize("method", ["ping", "initialize", "tools/list"])
def test_null_params_is_like_missing_params(env, method):
    key = key_for(env, ["read"])
    r = env.anonymous().post(
        "/mcp", headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": None},
    )
    assert r.status_code == 200 and "result" in r.json()


def test_query_has_max_length_256_in_schema_and_enforced(env):
    key = key_for(env, ["read"])
    t = next(t for t in rpc(env, key, "tools/list").json()["result"]["tools"] if t["name"] == "list_aliases")
    assert t["inputSchema"]["properties"]["query"]["maxLength"] == 256
    assert tool(env, key, "list_aliases", {"query": "q" * 256})["result"]["isError"] is False
    assert tool(env, key, "list_aliases", {"query": "q" * 257})["error"]["code"] == -32602


@pytest.mark.parametrize("bad", [True, False, {"a": 1}, [1], {}])
def test_bad_jsonrpc_id_is_invalid_request(env, bad):
    key = key_for(env, ["read"])
    r = env.anonymous().post(
        "/mcp", headers={"Authorization": f"Bearer {key}"}, json={"jsonrpc": "2.0", "id": bad, "method": "ping"}
    )
    assert r.json()["error"]["code"] == -32600 and r.json()["id"] is None


@pytest.mark.parametrize("ok", ["abc", 7, 1.5, None])
def test_string_number_null_ids_are_echoed(env, ok):
    key = key_for(env, ["read"])
    r = env.anonymous().post(
        "/mcp", headers={"Authorization": f"Bearer {key}"}, json={"jsonrpc": "2.0", "id": ok, "method": "ping"}
    )
    assert r.json() == {"jsonrpc": "2.0", "id": ok, "result": {}}

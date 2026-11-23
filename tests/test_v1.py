"""Key-authenticated /api/v1 alias routes (Task 5)."""
import pytest
from helpers import MultiUserEnv

from aliasfox.apple.hme import HmeError, SessionExpired


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])


def key_for(env, name, account, scopes):
    c, uid = env.sign_in(name)
    if account:
        env.connect(c, account)
    key, _ = env.app.state.api_keys.create(uid, "k", scopes)
    return c, uid, {"Authorization": f"Bearer {key}"}


def api(env):
    return env.anonymous()


ALL = ["read", "create", "edit", "deactivate", "delete"]


def test_me_shape(env):
    _, _, h = key_for(env, "a", "acct-a", ["read", "create"])
    r = api(env).get("/api/v1/me", headers=h)
    assert r.json() == {"name": "k", "scopes": ["create", "read"], "connected": True}
    _, _, h2 = key_for(env, "b", None, ["read"])
    assert api(env).get("/api/v1/me", headers=h2).json()["connected"] is False


def test_list_matches_ui_list(env):
    c, _, h = key_for(env, "a", "acct-a", ["read"])
    r = api(env).get("/api/v1/aliases", headers=h)
    assert r.status_code == 200 and r.json() == c.get("/api/aliases").json()


def test_create_201_generate_then_reserve_in_world_ops(env):
    _, _, h = key_for(env, "a", "acct-a", ["create"])
    env.world.calls.clear()
    r = api(env).post("/api/v1/aliases", headers=h, json={"label": "shop", "note": "n"})
    assert r.status_code == 201
    assert r.json()["label"] == "shop" and r.json()["note"] == "n"
    assert env.world.ops_on("acct-a") == ["generate", "reserve"]


def test_create_retries_once_on_41003_then_502_on_second_failure(env, monkeypatch):
    from helpers import FakeHmeClient

    _, _, h = key_for(env, "a", "acct-a", ["create"])
    real = FakeHmeClient.reserve
    fails = {"n": 1}

    def flaky(self, hme, label, note=""):
        if fails["n"] > 0:
            fails["n"] -= 1
            self._acct("reserve")
            raise HmeError(-41003, "try again")
        return real(self, hme, label, note)

    monkeypatch.setattr(FakeHmeClient, "reserve", flaky)
    guard, seen = env.app.state.create_guard, []
    for name in ("check", "record_error", "record_success"):
        monkeypatch.setattr(guard, name, lambda *a, _n=name, _f=getattr(guard, name): (seen.append(_n), _f(*a))[1])
    env.world.calls.clear()
    r = api(env).post("/api/v1/aliases", headers=h, json={"label": "x"})
    assert r.status_code == 201
    assert env.world.ops_on("acct-a") == ["generate", "reserve", "generate", "reserve"]
    assert seen == ["check", "check", "record_success"]  # the retry adds no hit and no error

    seen.clear()
    fails["n"] = 5
    r = api(env).post("/api/v1/aliases", headers=h, json={"label": "x"})
    assert r.status_code == 502 and r.json()["detail"] == "apple_error"
    assert seen == ["check", "check", "record_error"]


def test_sixth_create_in_a_minute_is_429(env):
    _, _, h = key_for(env, "a", "acct-a", ["create"])
    c = api(env)
    codes = [c.post("/api/v1/aliases", headers=h, json={"label": "x"}).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]
    r = c.post("/api/v1/aliases", headers=h, json={"label": "x"})
    assert r.json() == {"detail": "rate_limited"} and int(r.headers["Retry-After"]) >= 1


def test_patch_active_delete_each_need_their_scope(env):
    _, _, h = key_for(env, "a", "acct-a", ["read"])
    c = api(env)
    for method, path, body, need in [
        ("PATCH", "/api/v1/aliases/acct-a-id-0", {"label": "z"}, "edit"),
        ("POST", "/api/v1/aliases/acct-a-id-0/active", {"active": False}, "deactivate"),
        ("DELETE", "/api/v1/aliases/acct-a-id-0", None, "delete"),
        ("POST", "/api/v1/aliases", {"label": "z"}, "create"),
    ]:
        r = c.request(method, path, headers=h, json=body)
        assert r.status_code == 403
        assert r.json() == {"detail": {"error": "insufficient_scope", "needed": need}}
    _, _, h2 = key_for(env, "b", "acct-b", ALL)
    assert c.patch("/api/v1/aliases/acct-b-id-0", headers=h2, json={"label": "z"}).json() == {"ok": True}
    assert c.post("/api/v1/aliases/acct-b-id-0/active", headers=h2, json={"active": False}).json() == {"ok": True}
    assert c.delete("/api/v1/aliases/acct-b-id-0", headers=h2).json() == {"ok": True}
    assert "acct-b-id-0" not in env.world.accounts["acct-b"]["aliases"]


def test_not_connected_is_409(env):
    _, _, h = key_for(env, "a", None, ["read"])
    r = api(env).get("/api/v1/aliases", headers=h)
    assert r.status_code == 409 and r.json() == {"detail": "not_connected"}


def test_session_expired_is_503_not_401(env):
    c, _, h = key_for(env, "a", "acct-a", ["read", "create"])
    del env.world.accounts["acct-a"]
    for r in (
        api(env).get("/api/v1/aliases", headers=h),
        api(env).post("/api/v1/aliases", headers=h, json={"label": "x"}),
    ):
        assert r.status_code == 503 and r.json() == {"detail": "session_expired"}
    assert c.get("/api/aliases?refresh=true").status_code == 401  # UI keeps 401


def test_label_and_note_length_422(env):
    _, _, h = key_for(env, "a", "acct-a", ["create", "edit"])
    c = api(env)
    assert c.post("/api/v1/aliases", headers=h, json={"label": "x" * 257}).status_code == 422
    assert c.post("/api/v1/aliases", headers=h, json={"label": "x", "note": "n" * 2049}).status_code == 422
    assert c.patch("/api/v1/aliases/acct-a-id-0", headers=h, json={"label": "x" * 257}).status_code == 422


def test_user_id_comes_from_key_not_body(env):
    _, _, h = key_for(env, "a", "acct-a", ["create", "edit"])
    _, uid_b, _ = key_for(env, "b", "acct-b", ["read"])
    before = env.world.snapshot("acct-b")
    env.world.calls.clear()
    c = api(env)
    r = c.post("/api/v1/aliases", headers=h, json={"label": "x", "user_id": uid_b})
    assert r.status_code == 201
    c.patch("/api/v1/aliases/acct-b-id-0", headers=h, json={"label": "hack", "user_id": uid_b})
    assert env.world.snapshot("acct-b") == before
    assert env.world.ops_on("acct-b") == []  # acct-b's Apple world was never called


def test_repeated_403_consumes_no_create_budget(env, monkeypatch):
    _, _, ro = key_for(env, "a", "acct-a", ["read"])
    _, _, rw = key_for(env, "a", "acct-a", ["create"])
    guard, seen = env.app.state.create_guard, []
    monkeypatch.setattr(guard, "check", lambda *a: seen.append(a) or None)
    c = api(env)
    for _ in range(5):
        assert c.post("/api/v1/aliases", headers=ro, json={"label": "x"}).status_code == 403
    assert seen == []
    assert c.post("/api/v1/aliases", headers=rw, json={"label": "x"}).status_code == 201

import pytest
from helpers import MultiUserEnv

from aliasfox.apikeys import PRESETS, SCOPES


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])


def make(c, **body):
    body.setdefault("name", "k")
    if "scopes" not in body:
        body.setdefault("preset", "readonly")
    return c.post("/api/keys", json=body)


def test_create_returns_key_once_and_list_has_no_key(env):
    c, _ = env.sign_in("a")
    r = make(c, name="  laptop ", expires_days=30)
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("afk_") and body["name"] == "laptop"
    assert set(body) == {"id", "name", "preset", "scopes", "prefix", "created_at", "last_used_at", "expires_at", "key"}
    assert body["expires_at"] is not None and body["last_used_at"] is None
    listed = c.get("/api/keys")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert "key" not in listed.json()[0] and body["key"] not in listed.text
    assert listed.json()[0]["id"] == body["id"]


@pytest.mark.parametrize("preset", list(PRESETS))
def test_presets_map_to_scopes(env, preset):
    c, _ = env.sign_in("a")
    r = make(c, preset=preset)
    assert r.status_code == 201 and r.json()["preset"] == preset
    assert r.json()["scopes"] == [s for s in SCOPES if s in PRESETS[preset]]


def test_explicit_scopes(env):
    c, _ = env.sign_in("a")
    r = make(c, scopes=["edit", "read"])
    assert r.status_code == 201 and r.json()["preset"] is None and r.json()["scopes"] == ["read", "edit"]


@pytest.mark.parametrize(
    "body",
    [
        {"preset": "full", "scopes": ["read"]},
        {},
        {"scopes": []},
        {"scopes": ["root"]},
        {"preset": "root"},
        {"preset": "full", "name": ""},
        {"preset": "full", "name": "x" * 81},
        {"preset": "full", "expires_days": 0},
        {"preset": "full", "expires_days": 366},
    ],
)
def test_invalid_bodies_422(env, body):
    c, _ = env.sign_in("a")
    body = {"name": "k", **body}
    assert c.post("/api/keys", json=body).status_code == 422


def test_21st_key_409(env):
    c, _ = env.sign_in("a")
    for _ in range(20):
        assert make(c).status_code == 201
    r = make(c)
    assert (r.status_code, r.json()) == (409, {"detail": "too_many_keys"})


def test_user_b_cannot_revoke_user_a_key_and_it_still_works(env):
    ca, _ = env.sign_in("a")
    cb, _ = env.sign_in("b")
    made = make(ca).json()
    assert cb.delete(f"/api/keys/{made['id']}").status_code == 404
    assert cb.get("/api/keys").json() == []
    assert env.app.state.api_keys.lookup(made["key"]) is not None
    assert ca.delete(f"/api/keys/{made['id']}").json() == {"ok": True}
    assert ca.delete(f"/api/keys/{made['id']}").status_code == 404
    assert ca.get("/api/keys").json() == []


def test_key_header_on_keys_routes_is_401(env):
    c, _ = env.sign_in("a")
    key = make(c).json()["key"]
    anon = env.anonymous()
    h = {"Authorization": f"Bearer {key}"}
    assert anon.get("/api/keys", headers=h).status_code == 401
    assert anon.post("/api/keys", headers=h, json={"name": "k", "preset": "full"}).status_code == 401
    assert anon.delete("/api/keys/1", headers=h).status_code == 401


def test_revoke_invalidates_cache(env):
    c, _ = env.sign_in("a")
    made = make(c).json()
    ka = env.app.state.key_auth
    assert ka._resolve(made["key"]) is not None  # now cached
    assert c.delete(f"/api/keys/{made['id']}").status_code == 200
    assert ka._resolve(made["key"]) is None

"""Origin-guard exemption and CORS for key-authenticated paths (Task 6)."""
import pytest
from helpers import MultiUserEnv

from aliasfox.middleware import is_key_path

FOREIGN = {"Origin": "https://evil.example"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a"])


def test_preflight_on_v1_is_204_with_star_and_authentication_header(env):
    r = env.anonymous().options("/api/v1/aliases", headers={**FOREIGN, "Access-Control-Request-Method": "POST"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["access-control-allow-headers"] == (
        "Authorization, Authentication, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version"
    )
    assert r.headers["access-control-allow-methods"] == "GET, POST, PATCH, DELETE, OPTIONS"
    assert r.headers["access-control-max-age"] == "600"
    assert "x-content-type-options" in r.headers


def test_no_allow_credentials_anywhere(env):
    c, uid = env.sign_in("a")
    key, _ = env.app.state.api_keys.create(uid, "k", ["read"])
    for r in (
        c.options("/api/v1/me", headers=FOREIGN),
        c.get("/api/v1/me", headers={**FOREIGN, "Authorization": f"Bearer {key}"}),
        c.get("/api/v1/me", headers=FOREIGN),
    ):
        assert r.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in r.headers


def test_foreign_origin_post_with_key_not_bad_origin(env):
    c, uid = env.sign_in("a")
    key, _ = env.app.state.api_keys.create(uid, "k", ["read", "create"])
    r = env.anonymous().post(
        "/api/v1/aliases", headers={**FOREIGN, "Authorization": f"Bearer {key}"}, json={"label": "x"}
    )
    assert r.json().get("detail") != "bad_origin"
    assert r.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("path", ["/api/aliases", "/api/keys"])
def test_foreign_origin_post_to_cookie_route_still_403_bad_origin(env, path):
    c, _ = env.sign_in("a")
    r = c.post(path, headers=FOREIGN, json={"label": "x"})
    assert r.status_code == 403 and r.json() == {"detail": "bad_origin"}
    assert "access-control-allow-origin" not in r.headers


def test_options_on_non_key_path_has_no_cors_headers(env):
    r = env.anonymous().options("/api/aliases", headers=FOREIGN)
    assert not [h for h in r.headers if h.startswith("access-control")]


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/v1", True), ("/api/v1/", True), ("/api/v1/me", True),
        ("/api/alias/random/new", True), ("/mcp", True), ("/mcp/", True), ("/mcp/x", True),
        ("/api/v1x", False), ("/api/alias/random/newx", False), ("/api/alias/random/new/", False),
        ("/mcpx", False), ("/api/aliases", False), ("/api/keys", False), ("/", False),
    ],
)
def test_is_key_path_table(path, expected):
    assert is_key_path(path) is expected

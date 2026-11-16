import pytest
from fastapi.testclient import TestClient

import aliasfox.main as main_mod
from aliasfox.config import Settings
from aliasfox.web import install_web

SECRET = "web-test-secret-key-0123456789abcdef"
INDEX = "<!doctype html><html><body><div id=root></div></body></html>"


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "app.js").write_text("console.log(1)")
    (d / "assets" / "app.css").write_text("body{}")
    (d / "favicon.svg").write_text("<svg/>")
    return d


def make_client(tmp_path, dist_dir, **settings_kw):
    settings = Settings(secret_key=SECRET, data_dir=tmp_path / "data", **settings_kw)
    app = main_mod.create_app(settings)
    install_web(app, dist_dir)
    return TestClient(app, base_url="http://localhost")


def test_index_served_at_root_with_security_headers(tmp_path, dist):
    r = make_client(tmp_path, dist).get("/")
    assert r.status_code == 200 and r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_assets_served_with_immutable_cache(tmp_path, dist):
    c = make_client(tmp_path, dist)
    for name in ("app.js", "app.css"):
        r = c.get(f"/assets/{name}")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/assets/missing.js").status_code == 404


def test_top_level_file_served_no_cache(tmp_path, dist):
    r = make_client(tmp_path, dist).get("/favicon.svg")
    assert r.text == "<svg/>" and r.headers["cache-control"] == "no-cache"


def test_spa_fallback_for_client_routes(tmp_path, dist):
    c = make_client(tmp_path, dist)
    for path in ("/aliases", "/settings/deep/link", "/connect"):
        r = c.get(path)
        assert r.status_code == 200 and r.text == INDEX
        assert r.headers["cache-control"] == "no-cache"
        assert "content-security-policy" in r.headers


def test_unknown_api_path_is_json_404(tmp_path, dist):
    c = make_client(tmp_path, dist)
    for method in ("get", "post", "put", "delete"):
        r = getattr(c, method)("/api/nope")
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
        assert r.headers["cache-control"] == "no-store"
    r = c.get("/api")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_healthz_not_shadowed(tmp_path, dist):
    r = make_client(tmp_path, dist).get("/healthz")
    assert r.json() == {"status": "ok"}


def test_api_routes_not_shadowed(tmp_path, dist):
    r = make_client(tmp_path, dist).get("/api/aliases")
    assert r.status_code == 409  # real route, not connected yet


def test_missing_dist_does_not_crash_startup(tmp_path):
    with make_client(tmp_path, tmp_path / "nope") as c:
        r = c.get("/")
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
        assert c.get("/healthz").status_code == 200


def test_dist_without_index_does_not_crash(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    r = make_client(tmp_path, d).get("/")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_env_var_overrides_default_dist(tmp_path, dist, monkeypatch):
    monkeypatch.setenv("WEB_DIST_DIR", str(dist))
    app = main_mod.create_app(Settings(secret_key=SECRET, data_dir=tmp_path / "data"))
    r = TestClient(app, base_url="http://localhost").get("/")
    assert r.text == INDEX


@pytest.mark.parametrize(
    "path",
    [
        "/..%2f..%2fetc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/assets/../../x",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/%252e%252e/%252e%252e/secret.txt",
        "/..%252f..%252fsecret.txt",
        "//etc/passwd",
        "/%2fetc/passwd",
        "/secret.txt%00",
        "/assets/..%5c..%5csecret.txt",
    ],
)
def test_path_traversal_never_leaks(tmp_path, dist, path):
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    r = make_client(tmp_path, dist).get(path)
    assert "TOPSECRET" not in r.text and "root:" not in r.text
    assert r.status_code in (200, 400, 404)
    if r.status_code == 200:
        assert r.text == INDEX


def test_symlink_escaping_dist_is_not_served(tmp_path, dist):
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    (dist / "leak.txt").symlink_to(tmp_path / "secret.txt")
    (dist / "assets" / "leak.txt").symlink_to(tmp_path / "secret.txt")
    c = make_client(tmp_path, dist)
    assert "TOPSECRET" not in c.get("/leak.txt").text
    assert "TOPSECRET" not in c.get("/assets/leak.txt").text


def test_non_get_on_root_does_not_return_index(tmp_path, dist):
    c = make_client(tmp_path, dist)
    for method in ("post", "put", "delete", "patch"):
        r = getattr(c, method)("/")
        assert r.status_code in (404, 405) and INDEX not in r.text


def test_head_works(tmp_path, dist):
    c = make_client(tmp_path, dist)
    r = c.head("/")
    assert r.status_code == 200 and r.content == b""
    assert "content-security-policy" in r.headers
    assert c.head("/assets/app.js").status_code == 200
    assert c.head("/aliases").status_code == 200


def test_bad_host_still_applies_to_html_when_auth_off(tmp_path, dist):
    c = make_client(tmp_path, dist)
    r = c.get("/", headers={"host": "evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "bad_host"}
    assert c.get("/healthz", headers={"host": "evil.example"}).status_code == 200

"""Pure-ASGI middleware: body cap, security headers, HSTS, 500 containment, gzip."""

import httpx
import pytest
from fastapi.testclient import TestClient

from aliasfox.config import Settings
from aliasfox.main import SECURITY_HEADERS, create_app

SECRET = "m" * 32
LIMIT = 65536


def make_client(tmp_path, base="http://localhost", **kw):
    settings = Settings(secret_key=SECRET, data_dir=tmp_path, **kw)
    return TestClient(create_app(settings), base_url=base)


def assert_headers(r):
    for name, value in SECURITY_HEADERS.items():
        assert r.headers[name] == value


def test_declared_content_length_too_large_413(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/connect/cookie", content=b"x" * (LIMIT + 1))
    assert r.status_code == 413
    assert r.json() == {"detail": "payload_too_large"}
    assert_headers(r)
    assert r.headers["cache-control"] == "no-store"


def test_chunked_body_too_large_413(tmp_path):
    c = make_client(tmp_path)

    def gen():
        for _ in range(LIMIT // 1024 + 2):
            yield b"x" * 1024

    r = c.post("/api/connect/cookie", content=gen())
    assert r.status_code == 413
    assert r.json() == {"detail": "payload_too_large"}
    assert_headers(r)


def test_body_exactly_at_limit_is_not_rejected(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/connect/cookie", content=b"x" * LIMIT)
    assert r.status_code != 413  # reaches validation instead
    assert r.status_code == 422

    def gen():
        yield b"x" * (LIMIT // 2)
        yield b"x" * (LIMIT // 2)

    assert c.post("/api/connect/cookie", content=gen()).status_code == 422


def test_413_applies_to_every_route(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/aliases", content=b"x" * (LIMIT + 1))
    assert r.status_code == 413


def test_security_headers_on_404_and_405(tmp_path):
    c = make_client(tmp_path)
    for r in (c.get("/nope"), c.get("/api/nope"), c.post("/healthz")):
        assert_headers(r)


def test_security_headers_on_403_and_401(tmp_path):
    c = make_client(tmp_path, base="http://evil.example")
    r = c.get("/api/aliases")
    assert r.status_code == 403
    assert_headers(r)
    from aliasfox.crypto import hash_password

    c = make_client(
        tmp_path, auth_enabled=True, password_hash=hash_password("pw-pw-pw-pw")
    )
    r = c.get("/api/aliases")
    assert r.status_code == 401
    assert_headers(r)


# --- A13: unhandled exceptions ---------------------------------------------------


def test_unhandled_exception_is_fixed_500_with_headers(tmp_path, caplog):
    import aliasfox.main as main_mod

    settings = Settings(secret_key=SECRET, data_dir=tmp_path)
    app = create_app(settings)

    @app.get("/boom")
    def boom():
        raise RuntimeError("SECRETVALUE-in-message")

    caplog.set_level("DEBUG")
    c = TestClient(app, base_url="http://localhost")  # raise_server_exceptions stays True
    r = c.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"detail": "internal_error"}
    assert_headers(r)
    assert "SECRETVALUE" not in r.text
    assert "SECRETVALUE" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert main_mod  # silence unused


def test_unhandled_exception_on_api_path_gets_no_store(tmp_path):
    app = create_app(Settings(secret_key=SECRET, data_dir=tmp_path))

    @app.get("/api/boom")
    def boom():
        raise ValueError("x")

    r = TestClient(app, base_url="http://localhost").get("/api/boom")
    assert r.status_code == 500
    assert r.headers["cache-control"] == "no-store"


# --- A3: HSTS only over https -----------------------------------------------------


def test_hsts_only_when_request_is_https(tmp_path):
    http = make_client(tmp_path)
    assert "strict-transport-security" not in http.get("/healthz").headers
    https = make_client(tmp_path, base="https://localhost")
    assert https.get("/healthz").headers["strict-transport-security"] == "max-age=31536000"


def test_hsts_via_trusted_forwarded_proto_only(tmp_path):
    h = {"X-Forwarded-Proto": "https"}
    trusted = make_client(tmp_path, trusted_proxy=True)
    assert "strict-transport-security" in trusted.get("/healthz", headers=h).headers
    untrusted = make_client(tmp_path)
    assert "strict-transport-security" not in untrusted.get("/healthz", headers=h).headers


def test_hsts_present_on_error_responses_over_https(tmp_path):
    c = make_client(tmp_path, base="https://localhost")
    assert "strict-transport-security" in c.get("/nope").headers


def test_no_redirect_slashes_downgrade(tmp_path):
    c = make_client(tmp_path, base="https://localhost")
    r = c.get("/api/aliases/", follow_redirects=False)
    assert r.status_code == 404
    assert "location" not in r.headers


# --- A10: docs disabled, HEAD /healthz -------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_and_openapi_disabled(tmp_path, path):
    assert make_client(tmp_path).get(path).status_code == 404


def test_healthz_answers_head(tmp_path):
    r = make_client(tmp_path).head("/healthz")
    assert r.status_code == 200
    assert r.content == b""


# --- A11: gzip --------------------------------------------------------------------


def test_gzip_on_large_alias_list_keeps_headers(tmp_path):
    import aliasfox.main as main_mod
    from aliasfox.apple.hme import Alias

    big = [
        Alias(f"id{i}", f"a{i}@icloud.com", "label " * 20, "", True, "f@x.com", "ON_DEMAND", "", 1)
        for i in range(60)
    ]

    class Svc:
        async def list_aliases(self, user_id, refresh=False):
            return big

    from aliasfox.db import init_db, make_engine
    from aliasfox.crypto import derive_key
    from aliasfox.sessions import SessionStore

    settings = Settings(secret_key=SECRET, data_dir=tmp_path)
    engine = make_engine(settings)
    init_db(engine)
    store = SessionStore(engine, derive_key(SECRET))
    app = main_mod.create_app(settings, service=Svc(), store=store)
    c = TestClient(app, base_url="http://localhost")
    r = c.get("/api/aliases", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert len(r.json()) == 60
    assert r.headers["cache-control"] == "no-store"
    assert_headers(r)


# --- A10: Dockerfile --------------------------------------------------------------


def test_dockerfile_healthcheck_uses_port_and_safe_path():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    assert "PYTHONSAFEPATH=1" in text
    health = text[text.index("HEALTHCHECK") :].split("\nCMD [\"python\", \"-m\"")[0]
    assert "${PORT:-8080}" in health
    assert ":8080/healthz" not in health

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

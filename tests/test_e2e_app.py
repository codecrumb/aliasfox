"""End to end through the real create_app: connect, list, restart, no plaintext."""

import functools
import sqlite3

import httpx
from fastapi.testclient import TestClient

import aliasfox.api as api_mod
import aliasfox.main as main_mod
from aliasfox.apple.connect import connect_with_cookie
from aliasfox.apple.hme import HmeClient
from aliasfox.config import Settings

SECRET = "e2e-secret-key-value-0123456789abcdef"
COOKIE_VALUE = "PLAINTEXTCOOKIEVALUE123"
COOKIE = f"X-APPLE-WEBAUTH-USER={COOKIE_VALUE}; other=zzz"
BASE = "https://p99-maildomainws.icloud.com"


def apple_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "setup.icloud.com":
        return httpx.Response(
            200,
            json={
                "webservices": {"premiummailsettings": {"url": BASE}},
                "dsInfo": {"dsid": "42"},
            },
        )
    assert COOKIE_VALUE in request.headers["cookie"]
    return httpx.Response(
        200,
        json={
            "success": True,
            "result": {
                "hmeEmails": [
                    {
                        "anonymousId": "id1",
                        "hme": "fox@icloud.com",
                        "label": "shop",
                        "isActive": True,
                        "createTimestamp": 1700000000000,
                    }
                ]
            },
        },
    )


def build(tmp_path, monkeypatch, handler=apple_handler, dist_dir=None, **settings_kw):
    """Real create_app over a fake Apple transport. `handler` replaces the Apple
    fake, `dist_dir` serves a web bundle, `settings_kw` (e.g. auth) go to Settings."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        api_mod,
        "connect_with_cookie",
        lambda text: connect_with_cookie(text, transport=transport),
    )
    monkeypatch.setattr(
        main_mod,
        "AliasService",
        functools.partial(
            main_mod.AliasService,
            client_factory=lambda state, **kw: HmeClient(state, transport=transport, **kw),
        ),
    )
    settings = Settings(secret_key=SECRET, data_dir=tmp_path, **settings_kw)  # no ICLOUD_COOKIE
    app = main_mod.create_app(settings, dist_dir=dist_dir)
    return TestClient(app, base_url="http://localhost")


def test_connect_list_restart_and_no_plaintext(tmp_path, monkeypatch):
    with build(tmp_path, monkeypatch) as client:
        assert client.get("/api/aliases").status_code == 409  # not connected yet
        r = client.post("/api/connect/cookie", json={"cookie": COOKIE})
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        r = client.get("/api/aliases")
        assert r.status_code == 200
        assert [a["hme"] for a in r.json()] == ["fox@icloud.com"]

    # "Restart": a brand new app object over the same DB file, still no ICLOUD_COOKIE.
    with build(tmp_path, monkeypatch) as client:
        r = client.get("/api/aliases")
        assert r.status_code == 200
        assert [a["hme"] for a in r.json()] == ["fox@icloud.com"]

    con = sqlite3.connect(tmp_path / "aliasfox.db")
    try:
        blob = con.execute("select blob from apple_sessions").fetchone()[0]
        everything = b"".join(
            bytes(str(v), "utf8") if not isinstance(v, bytes) else v
            for row in con.execute("select * from apple_sessions")
            for v in row
        )
    finally:
        con.close()
    assert blob and COOKIE_VALUE.encode() not in everything
    raw = (tmp_path / "aliasfox.db").read_bytes()
    assert COOKIE_VALUE.encode() not in raw

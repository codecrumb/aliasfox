import re
import pytest
from fastapi.testclient import TestClient
from helpers import make_store

from aliasfox.sessions import SessionUnreadable
from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.auth import COOKIE_NAME
from aliasfox.config import Settings
from aliasfox.crypto import hash_password
from aliasfox.main import create_app
from aliasfox.db import init_db
from aliasfox.service import NotConnected

SECRET = "unit-test-secret-key-0123456789abcdef"
COOKIE_TEXT = "X-APPLE-WEBAUTH-USER=SUPERSECRETCOOKIE"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

ALIAS = Alias(
    "id1", "a@icloud.com", "Shop", "n", True, "me@x.com", "WEB", "shop.com", 1700000000000,
    raw={"originAppName": "Safari", "appBundleId": "com.apple.mobilesafari"},
)
BARE = Alias("id2", "b@icloud.com", "", "", False, "", "", "", None)


def memory_store():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(eng)
    return make_store(eng)[0]


class FakeService:
    def __init__(self):
        self.calls = []
        self.error = None
        self.connected = True
        self.hme_available = True

    def _rec(self, name, *args, **kw):
        self.calls.append((name, args, kw))
        if self.error:
            raise self.error

    async def list_aliases(self, user_id, *, refresh=False):
        self._rec("list", user_id, refresh=refresh)
        return [ALIAS, BARE]

    async def generate(self, user_id):
        self._rec("generate", user_id)
        return "new@icloud.com"

    async def reserve(self, user_id, hme, label, note=""):
        self._rec("reserve", user_id, hme, label, note)
        return ALIAS

    async def update(self, user_id, anonymous_id, label, note):
        self._rec("update", user_id, anonymous_id, label, note)

    async def set_active(self, user_id, anonymous_id, active):
        self._rec("set_active", user_id, anonymous_id, active)

    async def delete(self, user_id, anonymous_id):
        self._rec("delete", user_id, anonymous_id)

    async def forward_options(self, user_id):
        self._rec("forward_options", user_id)
        return ["me@x.com", "b@x.com"], "me@x.com"

    async def set_forward_to(self, user_id, address):
        self._rec("forward", user_id, address)

    async def is_connected(self, user_id):
        self._rec("is_connected", user_id)
        return self.connected

    async def availability(self, user_id):
        self._rec("availability", user_id)
        return self.hme_available

    async def recheck(self, user_id):
        self._rec("recheck", user_id)
        return self.hme_available


@pytest.fixture
def svc():
    return FakeService()


def make_client(svc, **kw):
    settings = Settings(secret_key=SECRET, **kw)
    return TestClient(
        create_app(settings, service=svc, store=memory_store()), base_url="http://localhost"
    )


@pytest.fixture
def client(svc):
    return make_client(svc)


def test_list_returns_aliases_json(client, svc):
    r = client.get("/api/aliases")
    assert r.status_code == 200
    body = r.json()
    assert body[0] == {
        "anonymous_id": "id1",
        "hme": "a@icloud.com",
        "label": "Shop",
        "note": "n",
        "is_active": True,
        "forward_to": "me@x.com",
        "origin": "WEB",
        "domain": "shop.com",
        "created_ms": 1700000000000,
        "origin_app_name": "Safari",
        "app_bundle_id": "com.apple.mobilesafari",
    }
    assert body[1]["origin_app_name"] is None and body[1]["app_bundle_id"] is None
    assert body[1]["created_ms"] is None
    assert svc.calls == [("list", (1,), {"refresh": False})]


def test_list_refresh_passes_through(client, svc):
    client.get("/api/aliases?refresh=true")
    assert svc.calls[-1] == ("list", (1,), {"refresh": True})


def test_expired_session_is_401_session_expired_not_500(client, svc):
    svc.error = SessionExpired(401, "session expired")
    r = client.get("/api/aliases")
    assert r.status_code == 401
    assert r.json() == {"detail": "session_expired"}


def test_not_connected_is_409(client, svc):
    svc.error = NotConnected(1)
    r = client.post("/api/aliases/generate")
    assert r.status_code == 409
    assert r.json() == {"detail": "not_connected"}


def test_hme_error_is_502_with_code_and_no_cookie_text(client, svc):
    svc.error = HmeError(1234, "apple said no")
    r = client.get("/api/aliases")
    assert r.status_code == 502
    assert r.json() == {"detail": "apple_error", "code": 1234, "message": "apple said no"}
    assert COOKIE_TEXT not in r.text


def test_hme_error_message_capped_at_200_chars(client, svc):
    svc.error = HmeError(1, "x")
    svc.error.message = "x" * 5000  # bypass HmeError's own cap: the handler caps too
    r = client.get("/api/aliases")
    assert r.status_code == 502
    assert r.json()["message"] == "x" * 200


def test_hme_error_with_no_code(client, svc):
    svc.error = HmeError(None, "bad")
    r = client.get("/api/aliases")
    assert r.status_code == 502 and r.json()["code"] is None


MUTATIONS = [
    ("post", "/api/aliases/generate", None, ("generate", (1,), {})),
    (
        "post",
        "/api/aliases",
        {"hme": "a@icloud.com", "label": "L", "note": "N"},
        ("reserve", (1, "a@icloud.com", "L", "N"), {}),
    ),
    (
        "patch",
        "/api/aliases/id1",
        {"label": "L2", "note": "N2"},
        ("update", (1, "id1", "L2", "N2"), {}),
    ),
    (
        "post",
        "/api/aliases/id1/active",
        {"active": False},
        ("set_active", (1, "id1", False), {}),
    ),
    ("delete", "/api/aliases/id1", None, ("delete", (1, "id1"), {})),
    ("put", "/api/forward-to", {"address": "me@x.com"}, ("forward", (1, "me@x.com"), {})),
]


@pytest.mark.parametrize("method,path,body,expected", MUTATIONS)
def test_each_mutation_route_calls_service(client, svc, method, path, body, expected):
    r = client.request(method, path, json=body)
    assert r.status_code == 200, r.text
    assert svc.calls == [expected]


def test_mutation_response_shapes(client):
    assert client.post("/api/aliases/generate").json() == {"hme": "new@icloud.com"}
    r = client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L"})
    assert r.json()["anonymous_id"] == "id1"
    assert client.delete("/api/aliases/id1").json() == {"ok": True}


def test_reserve_note_defaults_to_empty_and_patch_note_optional(client, svc):
    client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L"})
    assert svc.calls[-1] == ("reserve", (1, "a@icloud.com", "L", ""), {})
    client.patch("/api/aliases/id1", json={"label": "L"})
    assert svc.calls[-1] == ("update", (1, "id1", "L", None), {})


def test_user_id_never_comes_from_request(client, svc):
    client.get("/api/aliases?user_id=2", headers={"X-User": "2", "X-User-Id": "2"})
    client.post("/api/aliases/generate?user_id=2", headers={"X-User": "2"})
    client.delete("/api/aliases/id1?user_id=2")
    client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L", "user_id": 2})
    assert svc.calls and all(args[0] == 1 for _, args, _ in svc.calls)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/aliases", {"hme": "a@icloud.com", "label": "x" * 257}),
        ("post", "/api/aliases", {"hme": "a@icloud.com", "label": "L", "note": "x" * 2049}),
        ("post", "/api/aliases", {"hme": "x" * 321, "label": "L"}),
        ("post", "/api/aliases", {"label": "L"}),
        ("patch", "/api/aliases/id1", {"label": "x" * 257}),
        ("patch", "/api/aliases/id1", {"label": "L", "note": "x" * 2049}),
        ("post", "/api/aliases/id1/active", {"active": "maybe"}),
        ("post", "/api/aliases/id1/active", {}),
        ("put", "/api/forward-to", {"address": "x" * 321}),
        ("put", "/api/forward-to", {}),
        ("delete", "/api/aliases/" + "i" * 129, None),
        ("patch", "/api/aliases/" + "i" * 129, {"label": "L"}),
        ("post", "/api/aliases/" + "i" * 129 + "/active", {"active": True}),
    ],
)
def test_validation_422(client, svc, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 422
    assert svc.calls == []


def test_status_route(svc):
    c = make_client(svc)
    assert c.get("/api/status").json() == {
        "connected": True,
        "hme_available": True,
        "mode": "single",
        "auth_enabled": False,
    }
    svc.connected = False
    assert c.get("/api/status").json()["connected"] is False
    assert svc.calls[-1] == ("is_connected", (1,), {})
    svc.connected = True
    svc.hme_available = False
    assert c.get("/api/status").json()["hme_available"] is False
    assert c.post("/api/status/recheck").json() == {"connected": True, "hme_available": False}
    assert svc.calls[-1] == ("recheck", (1,), {})


# --- auth enabled -----------------------------------------------------------

@pytest.fixture
def auth_client(svc):
    settings = Settings(
        secret_key=SECRET, auth_enabled=True, password_hash=hash_password("pw-pw-pw")
    )
    return TestClient(
        create_app(settings, service=svc, store=memory_store()), base_url="http://localhost"
    )


def _api_routes(app):
    """(method, concrete path) for every /api route except /api/auth/* and /api/config."""
    def walk(routes, prefix=""):
        for route in routes:
            inner = getattr(route, "original_router", None)  # included routers are wrapped
            if inner is not None:
                yield from walk(inner.routes, prefix + route.include_context.prefix)
            else:
                yield prefix + getattr(route, "path", ""), route

    found = []
    for path, route in walk(app.routes):
        if not path.startswith("/api") or path.startswith("/api/auth/"):
            continue
        if path == "/api/config":
            continue  # the one public route (see tests/test_account.py)
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            found.append((method, concrete))
    return found


def test_every_api_route_requires_auth_cookie(auth_client, svc):
    routes = _api_routes(auth_client.app)
    assert len(routes) >= 12  # guards against the enumeration silently finding nothing
    assert ("DELETE", "/api/connect") in routes and ("GET", "/api/status") in routes
    for method, path in routes:
        r = auth_client.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json() == {"detail": "not_authenticated"}, (method, path)
    assert svc.calls == []


def test_api_works_after_login(auth_client, svc):
    assert auth_client.post("/api/auth/login", json={"password": "pw-pw-pw"}).status_code == 200
    assert COOKIE_NAME in auth_client.cookies
    assert auth_client.get("/api/aliases").status_code == 200
    assert svc.calls[-1][1][0] == 1


# --- security headers -------------------------------------------------------


def assert_security_headers(r):
    assert r.headers["content-security-policy"] == CSP
    assert "unsafe" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"


def test_security_headers_on_api_success_and_errors(client, svc):
    r = client.get("/api/aliases")
    assert_security_headers(r)
    assert r.headers["cache-control"] == "no-store"
    svc.error = SessionExpired(401, "x")
    r = client.get("/api/aliases")
    assert r.status_code == 401
    assert_security_headers(r)
    assert r.headers["cache-control"] == "no-store"
    assert_security_headers(client.put("/api/forward-to", json={}))  # 422


def test_security_headers_on_auth_401(auth_client):
    r = auth_client.get("/api/aliases")
    assert_security_headers(r)
    assert r.headers["cache-control"] == "no-store"


def test_healthz_has_security_headers_but_is_cacheable(client):
    r = client.get("/healthz", headers={"Host": "elsewhere.example"})
    assert r.status_code == 200
    assert_security_headers(r)
    assert "cache-control" not in r.headers


def test_security_headers_on_guard_403s(client):
    r = client.get("/api/aliases", headers={"Host": "evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "bad_host"}
    assert_security_headers(r)
    assert r.headers["cache-control"] == "no-store"
    r = client.post("/api/aliases/generate", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "bad_origin"}
    assert_security_headers(r)


def test_guards_still_block_before_service(client, svc):
    client.post("/api/aliases/generate", headers={"Origin": "http://evil.example"})
    assert svc.calls == []


# --- B2: unreadable stored session -------------------------------------------------


def test_session_unreadable_maps_to_409(client, svc):
    svc.error = SessionUnreadable()
    r = client.get("/api/aliases")
    assert r.status_code == 409
    assert r.json() == {"detail": "session_unreadable"}


def test_status_reports_unreadable_not_500(client, svc):
    svc.error = SessionUnreadable()
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["reason"] == "session_unreadable"


def test_status_has_no_reason_normally(client, svc):
    assert "reason" not in client.get("/api/status").json()


# --- B6: per-user limits on Apple-hitting calls ---------------------------------------


class Now:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def now(monkeypatch):
    import aliasfox.api as api_mod

    n = Now()
    monkeypatch.setattr(api_mod, "_now", n)
    return n


def test_refresh_honours_five_second_minimum_interval(client, svc, now):
    client.get("/api/aliases?refresh=true")
    now.t += 4.9
    r = client.get("/api/aliases?refresh=true")
    assert r.status_code == 200  # served, from the cache path
    now.t += 0.2
    client.get("/api/aliases?refresh=true")
    assert [c[2]["refresh"] for c in svc.calls] == [True, False, True]


def test_early_refresh_does_not_extend_the_window(client, svc, now):
    client.get("/api/aliases?refresh=true")
    for _ in range(3):
        now.t += 1.9
        client.get("/api/aliases?refresh=true")
    assert [c[2]["refresh"] for c in svc.calls][-1] is True  # 5.7 s since the last real refresh


def test_generate_and_reserve_share_ten_per_minute(client, svc, now):
    for i in range(5):
        assert client.post("/api/aliases/generate").status_code == 200
    for i in range(5):
        assert client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L"}).status_code == 200
    n_calls = len(svc.calls)
    r = client.post("/api/aliases/generate")
    assert r.status_code == 429
    assert r.json() == {"detail": "rate_limited"}
    assert int(r.headers["retry-after"]) >= 1
    assert client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L"}).status_code == 429
    assert len(svc.calls) == n_calls  # no Apple call made
    now.t += 61
    assert client.post("/api/aliases/generate").status_code == 200


def test_other_mutations_and_plain_list_not_limited(client, svc, now):
    for _ in range(15):
        assert client.get("/api/aliases").status_code == 200
        assert client.delete("/api/aliases/id1").status_code == 200


def test_three_apple_errors_on_reserve_start_create_cooldown(client, svc, now):
    svc.error = HmeError(1234, "apple said no")
    for _ in range(3):
        assert client.post("/api/aliases", json={"hme": "a@icloud.com", "label": "L"}).status_code == 502
    svc.error = None
    r = client.post("/api/aliases/generate")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    now.t += 301
    assert client.post("/api/aliases/generate").status_code == 200

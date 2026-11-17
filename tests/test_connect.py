import json

import httpx
import pytest
from helpers import make_store
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from aliasfox.apple.connect import (
    AppleUnreachable,
    InvalidCookie,
    connect_with_cookie,
    is_session_valid,
    parse_cookie_header,
    seed_from_env,
)
from aliasfox.config import Settings
from aliasfox.db import AppleSessionRow, init_db
from aliasfox.sessions import AppleSessionState, SessionUnreadable

SECRET = "SECRETVALUE=abc=="
COOKIE = f"X-APPLE-WEBAUTH-USER={SECRET}; other=v2"
HME_URL = "https://p99-maildomainws.icloud.com"
GOOD = {"webservices": {"premiummailsettings": {"url": HME_URL}}, "dsInfo": {"dsid": "777"}}


def validate_ok(req):
    return httpx.Response(200, json=GOOD)


def test_parse_cookie_line():
    assert parse_cookie_header("Cookie: a=b; c=d=e") == {"a": "b", "c": "d=e"}


def test_parse_curl_header():
    assert parse_cookie_header("curl x -H 'cookie: a=b; c=d'") == {"a": "b", "c": "d"}
    assert parse_cookie_header('-H "Cookie: a=b; c=d"') == {"a": "b", "c": "d"}
    assert parse_cookie_header("-b 'a=b; c=d'") == {"a": "b", "c": "d"}


def test_parse_json_export():
    text = json.dumps([{"name": "a", "value": "b=", "domain": ".icloud.com"}, {"name": "c", "value": "d"}])
    assert parse_cookie_header(text) == {"a": "b=", "c": "d"}


def test_parse_quoted_with_newlines():
    assert parse_cookie_header('  "a=b;\n c=d\n"  ') == {"a": "b", "c": "d"}


@pytest.mark.parametrize("text", ["", "   ", "nonsense", "[]", "[1,2]"])
def test_parse_empty_or_unparseable_raises(text):
    with pytest.raises(InvalidCookie):
        parse_cookie_header(text)


def test_missing_webauth_token_raises_invalid_cookie_with_clear_message():
    def boom(req):
        raise AssertionError("must not call network")

    with pytest.raises(InvalidCookie) as ei:
        connect_with_cookie("a=SECRETX; b=y", transport=httpx.MockTransport(boom))
    msg = str(ei.value)
    assert "X-APPLE-WEBAUTH-" in msg and "X-APPLE-DS-WEB-SESSION-TOKEN" in msg
    assert "SECRETX" not in msg


def test_connect_extracts_webservices_url():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json=GOOD, headers=[("set-cookie", "new=1; Path=/")])

    state = connect_with_cookie("Cookie: " + COOKIE, transport=httpx.MockTransport(handler))
    assert state.webservices_url == HME_URL
    assert state.dsid == "777"
    assert state.cookies["X-APPLE-WEBAUTH-USER"] == SECRET
    assert state.cookies["new"] == "1"
    r = seen["req"]
    assert r.method == "POST"
    assert str(r.url).startswith("https://setup.icloud.com/setup/ws/1/validate")
    assert r.headers["origin"] == "https://www.icloud.com"
    assert r.headers["referer"] == "https://www.icloud.com/"
    assert r.headers["content-type"] == "text/plain"
    assert "X-APPLE-WEBAUTH-USER=" + SECRET in r.headers["cookie"]


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(421, text="body SECRETVALUE"),
        httpx.Response(200, json={"webservices": {}}),
        httpx.Response(200, text="not json SECRETVALUE"),
    ],
)
def test_validate_failure_raises_invalid_cookie_without_echoing_cookie(resp):
    with pytest.raises(InvalidCookie) as ei:
        connect_with_cookie(COOKIE, transport=httpx.MockTransport(lambda r: resp))
    assert "SECRETVALUE" not in str(ei.value) and "SECRETVALUE" not in repr(ei.value)


def test_network_error_raises_apple_unreachable_without_echo():
    def handler(req):
        raise httpx.ConnectError("boom SECRETVALUE")

    with pytest.raises(AppleUnreachable) as ei:
        connect_with_cookie(COOKIE, transport=httpx.MockTransport(handler))
    assert "SECRETVALUE" not in str(ei.value)


def test_is_session_valid_true_false_never_raises():
    state = AppleSessionState(cookies={"X-APPLE-WEBAUTH-USER": "v"}, webservices_url=HME_URL)
    assert is_session_valid(state, transport=httpx.MockTransport(validate_ok)) is True
    assert is_session_valid(state, transport=httpx.MockTransport(lambda r: httpx.Response(401))) is False
    assert is_session_valid(state, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) is False

    def boom(req):
        raise httpx.ConnectError("x")

    assert is_session_valid(state, transport=httpx.MockTransport(boom)) is False


@pytest.fixture
def store():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    init_db(eng)
    return make_store(eng)[0]


def make_settings(cookie):
    s = Settings.model_construct(icloud_cookie=cookie)
    return s


def test_seed_skipped_without_env_cookie(store):
    assert seed_from_env(make_settings(None), store) is False
    assert seed_from_env(make_settings(""), store) is False
    assert store.load(1) is None


def test_seed_skipped_when_valid_session_exists(store):
    store.save(1, AppleSessionState(cookies={"X-APPLE-WEBAUTH-USER": "old"}, webservices_url=HME_URL))
    assert seed_from_env(make_settings(COOKIE), store, transport=httpx.MockTransport(validate_ok)) is False
    assert store.load(1).cookies == {"X-APPLE-WEBAUTH-USER": "old"}


def test_seed_runs_when_no_session(store):
    assert seed_from_env(make_settings(COOKIE), store, transport=httpx.MockTransport(validate_ok)) is True
    assert store.load(1).dsid == "777"


def test_seed_runs_when_stored_session_invalid(store):
    store.save(1, AppleSessionState(cookies={"X-APPLE-WEBAUTH-USER": "old"}, webservices_url=HME_URL))
    calls = []

    def handler(req):
        calls.append(req.headers["cookie"])
        if "old" in req.headers["cookie"]:
            return httpx.Response(421)
        return httpx.Response(200, json=GOOD)

    assert seed_from_env(make_settings(COOKIE), store, transport=httpx.MockTransport(handler)) is True
    assert len(calls) == 2
    assert store.load(1).cookies["X-APPLE-WEBAUTH-USER"] == SECRET


def test_seed_raises_when_env_cookie_invalid(store):
    with pytest.raises(InvalidCookie):
        seed_from_env(make_settings(COOKIE), store, transport=httpx.MockTransport(lambda r: httpx.Response(421)))
    assert store.load(1) is None


def test_non_ascii_cookie_raises_invalid_cookie_without_value():
    secret = "X-APPLE-WEBAUTH-USER=caféSECRET"
    with pytest.raises(InvalidCookie) as ei:
        connect_with_cookie(secret, transport=httpx.MockTransport(validate_ok))
    assert "SECRET" not in str(ei.value)


TRUNC_MSG = "cookie looks truncated or corrupted - copy it again from the browser"


def test_parse_rejects_non_ascii_value_without_echo():
    with pytest.raises(InvalidCookie) as e:
        parse_cookie_header("X-APPLE-WEBAUTH-USER=abc…def; other=v")
    assert str(e.value) == TRUNC_MSG
    assert "abc" not in str(e.value)


@pytest.mark.parametrize("bad", ["na(me", "na@me", "na/me", "naéme", "na[me]", "na{me}"])
def test_parse_rejects_bad_cookie_names(bad):
    with pytest.raises(InvalidCookie) as e:
        parse_cookie_header(f"{bad}=value; X-APPLE-WEBAUTH-USER=v")
    assert str(e.value) == TRUNC_MSG


def test_parse_json_export_rejects_non_ascii_value():
    text = json.dumps([{"name": "a", "value": "b…"}])
    with pytest.raises(InvalidCookie) as e:
        parse_cookie_header(text)
    assert str(e.value) == TRUNC_MSG


def test_connect_with_ellipsis_never_reports_unicode_error():
    def boom(req):
        raise AssertionError("must not reach the network")

    with pytest.raises(InvalidCookie) as e:
        connect_with_cookie(
            "X-APPLE-WEBAUTH-USER=abc…", transport=httpx.MockTransport(boom)
        )
    assert "Unicode" not in str(e.value)
    assert str(e.value) == TRUNC_MSG


def test_seed_overwrites_unreadable_stored_session(store):
    # A stored blob the current key cannot read (corrupt / foreign).
    with store._sessions.begin() as s:
        s.add(AppleSessionRow(user_id=1, blob=b"\x00" * 64))
    with pytest.raises(SessionUnreadable):
        store.load(1)
    assert seed_from_env(make_settings(COOKIE), store, transport=httpx.MockTransport(validate_ok)) is True
    assert store.load(1).dsid == "777"


@pytest.mark.parametrize("status", [500, 502, 503])
def test_apple_5xx_is_unreachable_not_invalid_cookie(status):
    with pytest.raises(AppleUnreachable):
        connect_with_cookie(COOKIE, transport=httpx.MockTransport(lambda r: httpx.Response(status)))


def test_timeout_error_is_unreachable():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(AppleUnreachable):
        connect_with_cookie(COOKIE, transport=httpx.MockTransport(handler))


def test_validate_client_has_explicit_bounded_timeout(monkeypatch):
    import aliasfox.apple.connect as mod

    seen = {}
    real = httpx.Client

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(mod.httpx, "Client", spy)
    connect_with_cookie(COOKIE, transport=httpx.MockTransport(validate_ok))
    t = seen["timeout"]
    assert isinstance(t, httpx.Timeout)
    parts = [t.connect, t.read, t.write, t.pool]
    assert all(p is not None for p in parts)
    assert sum(parts) <= 30

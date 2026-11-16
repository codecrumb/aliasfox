"""webservices_url allow-list and hardened httpx clients."""

import httpx
import pytest
from fastapi.testclient import TestClient

import aliasfox.apple.connect as connect_mod
import aliasfox.apple.hme as hme_mod
from aliasfox.apple.connect import connect_with_cookie
from aliasfox.apple.hme import HmeClient, HmeError, UntrustedAppleUrl, check_webservices_url
from aliasfox.apple.login import LoginFlow, _build_state
from aliasfox.sessions import AppleSessionState

GOOD_URLS = [
    "https://p99-maildomainws.icloud.com",
    "https://p1-maildomainws.icloud.com:443",
    "https://P99-MailDomainWS.ICLOUD.COM",
    "https://p99-maildomainws.icloud.com.cn",
    "https://p99.icloud.com/some/path",
]
BAD_URLS = [
    "http://p99-maildomainws.icloud.com",
    "ftp://p99.icloud.com",
    "https://evilicloud.com",
    "https://evil-icloud.com",
    "https://icloud.com",  # apex has no leading dot
    "https://p99.icloud.com.evil.com",
    "https://p99.icloud.com.evil.cn",
    "https://p99.icloud.com@evil.com",
    "https://evil.com@p99.icloud.com",
    "https://user:pw@p99.icloud.com",
    "https://evil.com\\@p99.icloud.com",
    "https://evil.com#@p99.icloud.com",
    "https://p99.icloud.com:8443",
    "https://p99.icloud.com:evil",
    "https://p99.icloud.com.",
    "https://127.0.0.1",
    "https://[::1]",
    "//p99.icloud.com",
    "p99.icloud.com",
    "",
    "https://p99.icloud.com\n.evil.com",
    "https:///p99.icloud.com",
]


@pytest.mark.parametrize("url", GOOD_URLS)
def test_good_urls_pass(url):
    assert check_webservices_url(url) == url


@pytest.mark.parametrize("url", BAD_URLS)
def test_bad_urls_rejected_without_echo(url):
    with pytest.raises(UntrustedAppleUrl) as ei:
        check_webservices_url(url)
    assert isinstance(ei.value, HmeError)
    if url:
        assert url not in str(ei.value) and url not in repr(ei.value)


def test_non_string_rejected():
    with pytest.raises(UntrustedAppleUrl):
        check_webservices_url(None)


@pytest.mark.parametrize("url", BAD_URLS[:8])
def test_hme_client_refuses_untrusted_stored_state(url):
    state = AppleSessionState(cookies={"a": "b"}, webservices_url=url)
    with pytest.raises(UntrustedAppleUrl):
        HmeClient(state, transport=httpx.MockTransport(lambda r: httpx.Response(200)))


@pytest.mark.parametrize("url", BAD_URLS[:8])
def test_connect_with_cookie_rejects_untrusted_validate_url(url):
    def handler(req):
        return httpx.Response(
            200, json={"webservices": {"premiummailsettings": {"url": url}}}
        )

    with pytest.raises(UntrustedAppleUrl):
        connect_with_cookie("X-APPLE-WEBAUTH-USER=v", transport=httpx.MockTransport(handler))


def test_build_state_rejects_untrusted_url():
    class Api:
        class session:
            cookies = []

        webservices = {"premiummailsettings": {"url": "https://evil.example"}}
        data = {}

    with pytest.raises(UntrustedAppleUrl):
        _build_state(Api())


def test_login_start_propagates_untrusted_url_not_generic_failure(tmp_path):
    class Api:
        requires_2fa = False
        requires_2sa = False

        class session:
            cookies = []

        webservices = {"premiummailsettings": {"url": "http://p1.icloud.com"}}
        data = {}

    flow = LoginFlow(service_factory=lambda *a, **k: Api())
    with pytest.raises(UntrustedAppleUrl):
        flow.start(1, "a@b.c", "pw")


def test_untrusted_url_maps_to_502_apple_error_without_echo(tmp_path, monkeypatch):
    import aliasfox.api as api_mod
    from aliasfox.config import Settings
    from aliasfox.main import create_app

    evil = "https://evil.example/leak"

    def handler(req):
        return httpx.Response(200, json={"webservices": {"premiummailsettings": {"url": evil}}})

    monkeypatch.setattr(
        api_mod,
        "connect_with_cookie",
        lambda text: connect_with_cookie(text, transport=httpx.MockTransport(handler)),
    )
    app = create_app(Settings(secret_key="s" * 32, data_dir=tmp_path))
    r = TestClient(app, base_url="http://localhost").post(
        "/api/connect/cookie", json={"cookie": "X-APPLE-WEBAUTH-USER=v"}
    )
    assert r.status_code == 502
    assert r.json()["detail"] == "apple_error"
    assert "evil" not in r.text


def _capture_client_kwargs(monkeypatch, module):
    seen = {}
    real = httpx.Client

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(module.httpx, "Client", spy)
    return seen


def test_hme_client_httpx_hardened(monkeypatch):
    seen = _capture_client_kwargs(monkeypatch, hme_mod)
    state = AppleSessionState(cookies={"a": "b"}, webservices_url=GOOD_URLS[0])
    HmeClient(state, transport=httpx.MockTransport(lambda r: httpx.Response(200))).close()
    assert seen["trust_env"] is False and seen["follow_redirects"] is False


def test_validate_httpx_hardened(monkeypatch):
    seen = _capture_client_kwargs(monkeypatch, connect_mod)
    good = {"webservices": {"premiummailsettings": {"url": GOOD_URLS[0]}}}
    connect_with_cookie(
        "X-APPLE-WEBAUTH-USER=v", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=good))
    )
    assert seen["trust_env"] is False and seen["follow_redirects"] is False


def test_redirects_are_not_followed(monkeypatch):
    def handler(req):
        if req.url.host == "setup.icloud.com":
            return httpx.Response(302, headers={"location": "https://evil.example/"})
        raise AssertionError("redirect followed")

    with pytest.raises(connect_mod.InvalidCookie):
        connect_with_cookie("X-APPLE-WEBAUTH-USER=v", transport=httpx.MockTransport(handler))

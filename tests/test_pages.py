"""Public pages (landing, privacy, terms) served in multi mode only."""
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aliasfox.main as main_mod
from aliasfox.config import ConfigError, Settings
from aliasfox.pages import install_pages
from helpers import MULTI_BASE, FakeNeonVerifier, MultiUserEnv, multi_mode_settings

REPO = "https://github.com/codecrumb/aliasfox"
INDEX = "<!doctype html><html><body><div id=app>spa-index</div></body></html>"
PAGES = ("/", "/privacy", "/terms")


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "favicon.svg").write_text("<svg/>")
    return d


def client(tmp_path, monkeypatch, dist_dir=None, **settings_kw) -> TestClient:
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(main_mod, "NeonTokenVerifier", lambda **_: FakeNeonVerifier())
    app = main_mod.create_app(multi_mode_settings(tmp_path, **settings_kw), dist_dir=dist_dir)
    return TestClient(app, base_url=MULTI_BASE)


def unvalidated_client(**settings_kw) -> TestClient:
    """The pages alone, with settings startup validation would refuse (multi mode now
    requires a plain CONTACT_EMAIL): the rendering stays safe even so."""
    settings = Settings(aliasfox_mode="multi", secret_key="s" * 32, **settings_kw)
    app = FastAPI()
    app.state.settings = settings
    install_pages(app, settings)
    return TestClient(app, base_url=MULTI_BASE)


def test_landing_for_signed_out_visitor(tmp_path, monkeypatch, dist):
    r = client(tmp_path, monkeypatch, dist).get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    html = r.text
    assert "AliasFox manages your iCloud Hide My Email addresses" in html
    assert '<html lang="en">' in html
    assert "<title>" in html and 'name="description"' in html
    assert 'href="/login"' in html
    assert 'href="/privacy"' in html and 'href="/terms"' in html
    assert f'href="{REPO}"' in html
    assert 'href="/site.css"' in html
    assert "not affiliated with or endorsed by Apple" in html
    assert "Apple, iCloud and Hide My Email are trademarks of Apple Inc." in html
    assert "without warranty" in html
    assert "spa-index" not in html


def test_landing_has_no_script_tag_and_no_external_urls_except_repo_link(
    tmp_path, monkeypatch, dist
):
    c = client(tmp_path, monkeypatch, dist, signups_enabled=True)
    for path in PAGES:
        html = c.get(path).text
        lower = html.lower()
        # the only script is the landing page's same-origin hero tilt
        assert lower.count("<script") == (path == "/"), path
        assert "<script" not in lower or '<script src="/hero.js" defer></script>' in lower, path
        assert "<style" not in lower, path
        assert re.search(r"\sstyle\s*=", lower) is None, path
        assert re.search(r"\son[a-z]+\s*=", lower) is None, path  # inline handlers
        for url in re.findall(r"https?://[^\s\"'<>)]+", html):
            assert url.startswith(REPO), (path, url)
    css = c.get("/site.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert css.headers["cache-control"] == "public, max-age=3600"
    assert re.search(r"https?://", css.text) is None
    assert "@import" not in css.text


def test_signed_in_gets_spa_index(tmp_path, monkeypatch, dist):
    env = MultiUserEnv(tmp_path, monkeypatch, ["a"], dist_dir=dist)
    c, _ = env.sign_in("a")
    r = c.get("/")
    assert r.status_code == 200 and r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"
    # privacy/terms stay the static pages for signed-in users
    assert "Privacy" in c.get("/privacy").text
    # a stale cookie (after logout bumps the epoch) gets the landing page again
    stale = c.cookies.get("aliasfox_session")
    assert c.post("/api/auth/logout").status_code in (200, 204)
    anon = env.anonymous()
    anon.cookies.set("aliasfox_session", stale)
    assert "AliasFox manages your iCloud Hide My Email addresses" in anon.get("/").text


def test_signed_in_without_built_spa_gets_404(tmp_path, monkeypatch):
    env = MultiUserEnv(tmp_path, monkeypatch, ["a"])
    c, _ = env.sign_in("a")
    assert c.get("/").status_code == 404


def test_signups_closed_message_shown_when_flag_off(tmp_path, monkeypatch, dist):
    closed = client(tmp_path / "x", monkeypatch, dist, signups_enabled=False).get("/").text
    assert "Sign-ups are closed for now" in closed
    assert 'href="/signup"' not in closed
    assert 'href="/login"' in closed
    opened = client(tmp_path / "y", monkeypatch, dist, signups_enabled=True).get("/").text
    assert "Sign-ups are closed" not in opened
    assert 'href="/signup"' in opened and 'href="/login"' in opened


def test_privacy_names_stored_and_not_stored_items(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch).get("/privacy")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    html = r.text
    for phrase in (
        "encrypted",
        "Your Apple password is never stored",
        "60 seconds",
        "IndexedDB",
        "Neon",
        "Render",
        "password hash",
        "wrapped data key",
        "timestamps",
        "Delete account",
        "Disconnect Apple",
        "HttpOnly",
        "SameSite=Strict",
        "no tracking",
        "broader",
        "datacenter",
        "Draft, not legal advice",
        "2026-11-17",
    ):
        assert phrase in html, phrase
    assert "delete" in html.lower()
    # the combined database + server secret warning, stated plainly
    assert "database and the server secret" in html
    assert "every stored session" in html


def test_terms_state_no_warranty_unofficial_not_affiliated_with_apple_no_guarantee_security_availability(
    tmp_path, monkeypatch
):
    r = client(tmp_path, monkeypatch).get("/terms")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    html = r.text
    for phrase in (
        "unofficial",
        "not affiliated with or endorsed by Apple",
        "as is",
        "without warranty",
        "no guarantee of security or availability",
        "lockout",
        "your own risk",
        "Acceptable use",
        "suspend",
        "limitation of liability",
        "[governing law to be set by the operator]",
        "Draft, not legal advice",
        "2026-11-17",
        "AGPL-3.0-or-later",
    ):
        assert phrase.lower() in html.lower(), phrase


def test_contact_email_is_escaped(tmp_path, monkeypatch):
    evil = '"><script>x</script>'
    with pytest.raises(ConfigError):  # startup refuses it outright
        client(tmp_path, monkeypatch, contact_email=evil)
    c = unvalidated_client(contact_email=evil)
    for path in PAGES:
        html = c.get(path).text
        assert evil not in html
        assert "<script>" not in html.lower()  # only the hero's src= script exists
    privacy = c.get("/privacy").text
    assert "&quot;&gt;&lt;script&gt;x&lt;/script&gt;" in privacy


def test_contact_email_shown_when_set(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch, contact_email="hello@example.com")
    for path in ("/privacy", "/terms"):
        html = c.get(path).text
        assert 'href="mailto:hello@example.com"' in html
        assert "private security reporting" not in html


def test_public_url_trailing_slash_and_html_escaped(tmp_path, monkeypatch):
    c = client(tmp_path / "a", monkeypatch, public_url="https://fox.example/")
    html = c.get("/privacy").text
    assert 'href="https://fox.example/privacy"' in html
    assert "https://fox.example//" not in html
    c = client(tmp_path / "b", monkeypatch, public_url='https://fox.example/x"y/')
    for path in PAGES:
        html = c.get(path).text
        assert 'x"y' not in html
    assert "https://fox.example/x&quot;y/privacy" in c.get("/privacy").text


def test_public_url_unset_omits_absolute_urls(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    for path in PAGES:
        html = c.get(path).text
        assert "{{" not in html and "}}" not in html
        assert 'rel="canonical"' not in html


def test_no_contact_set_falls_back_to_github_issue_text(tmp_path, monkeypatch):
    with pytest.raises(ConfigError):  # multi mode requires CONTACT_EMAIL at startup
        client(tmp_path, monkeypatch, contact_email=None)
    c = unvalidated_client(contact_email=None)
    for path in ("/privacy", "/terms"):
        html = c.get(path).text
        assert "mailto:" not in html
        assert "open an issue" in html
        assert "private security reporting" in html
        assert f"{REPO}/issues" in html


def test_pages_absent_in_single_mode(tmp_path, dist):
    settings = Settings(secret_key="s" * 32, data_dir=tmp_path / "data")
    c = TestClient(main_mod.create_app(settings, dist_dir=dist), base_url="http://localhost")
    for path in ("/", "/privacy", "/terms", "/site.css", "/hero.js", "/hero3d.js"):
        r = c.get(path)
        assert r.status_code == 200 and r.text == INDEX, path  # SPA fallback, unchanged
    bare = TestClient(
        main_mod.create_app(
            Settings(secret_key="s" * 32, data_dir=tmp_path / "data2"), dist_dir=tmp_path / "none"
        ),
        base_url="http://localhost",
    )
    for path in ("/", "/privacy", "/terms", "/site.css", "/hero.js", "/hero3d.js"):
        assert bare.get(path).status_code == 404, path


def test_security_headers_present_on_pages(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    for path in (*PAGES, "/site.css"):
        r = c.get(path, headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 200, path
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["strict-transport-security"].startswith("max-age=")


def test_head_requests_supported(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    for path in (*PAGES, "/site.css"):
        assert c.head(path).status_code == 200, path


def test_placeholders_in_configured_values_are_not_expanded(tmp_path, monkeypatch):
    c = client(
        tmp_path, monkeypatch, signups_enabled=True,
        contact_email="{{SIGNUPS}}@example.com",
        public_url="https://fox.example/{{CONTACT}}",
    )
    html = c.get("/privacy").text
    assert "mailto:{{SIGNUPS}}@example.com" in html
    assert 'href="https://fox.example/{{CONTACT}}/privacy"' in html
    assert html.count('href="/signup"') == 0


def test_privacy_memory_and_retention_wording_matches_code(tmp_path, monkeypatch):
    html = client(tmp_path, monkeypatch).get("/privacy").text
    for phrase in (
        "reuses it for up to 60 seconds",
        "within about two minutes of your last use",
        "when you log out, disconnect Apple or delete your account",
        "a request already in flight",
        "dropped within about two minutes after its limit window ends",
        "ten to eleven minutes",
        "trust token",
        "AliasFox asks the sign-in provider (Neon) to delete your sign-in record when you "
        "delete your account",
        "If that request does not go through, the app tells you, and it is removed on request",
        "the part of your email address before the @ is sent to the sign-in provider as the "
        "account's display name",
        "sign you in, check the session and manage Hide My Email",
    ):
        assert phrase in html, phrase
    assert "not removed automatically" not in html
    assert "only calls Hide My Email" not in html
    assert "sign out of it at Apple" not in html

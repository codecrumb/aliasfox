"""Pin the API-key facts in the privacy page, terms page, README and live-check list."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "src/aliasfox/page_templates"


def _text(path: Path) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", path.read_text())).lower()


def _keys_section() -> str:
    html = (TPL / "privacy.html").read_text()
    m = re.search(r'<h2 id="keys">(.*?)<h2 ', html, re.S)
    assert m
    return _text_of(m.group(1))


def _text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


def test_privacy_keys_section_states_the_facts():
    s = _keys_section()
    for needle in [
        "revoke",
        "deleted when you revoke",
        "deleting your account deletes all your keys",
        "suspended",
        "deleted when the server starts",
        "secret key",
        "every key stops working",
        "forwarding address",
        "alias list is still not stored",
        "60 seconds",
    ]:
        assert needle in s, needle


def test_privacy_stored_list_and_delete_account_cover_keys():
    t = _text(TPL / "privacy.html")
    assert "only a keyed hash of it is stored" in t
    assert "shown to you once" in t
    m = re.search(r"<strong>delete account:</strong>(.*?)</li>", (TPL / "privacy.html").read_text(), re.S | re.I)
    assert m and "api keys" in _text_of(m.group(1))


def test_terms_cover_automated_use_and_key_custody():
    t = _text(TPL / "terms.html")
    assert "your own api keys" in t and "within those limits" in t
    assert "keep your api keys secret" in t


def test_readme_api_keys_section():
    readme = (ROOT / "README.md").read_text()
    m = re.search(r"## API keys, Bitwarden and MCP\n(.*?)(?=\n## )", readme, re.S)
    assert m
    s = re.sub(r"\s+", " ", m.group(1))
    for needle in [
        "Cmd/Ctrl+K", '"API keys"', "`bitwarden`", "readonly", "assistant", "full",
        "Forwarded email alias", "SimpleLogin", "no trailing slash", "https",
        "claude mcp add --transport http aliasfox https://<your-site>/mcp --header \"Authorization: Bearer <key>\"",
        "OAuth", "/api/v1/aliases", "/api/alias/random/new",
        "10 create", "120 requests", "cooldown", "API_KEYS_ENABLED", "503", "/api/keys",
        "bearer", "ALIASFOX_SECRET_KEY", "invalidates all keys",
    ]:
        assert needle in s, needle


def test_live_check_lists_api_key_items():
    t = (ROOT / "docs/superpowers/plans/2026-11-17-live-check.md").read_text()
    m = re.search(r"## Live checks for API keys\n(.*)", t, re.S)
    assert m
    s = m.group(1)
    for needle in ["Bitwarden", "trailing slash", "Claude Code", "API_KEYS_ENABLED", "TRUSTED_PROXY_HOPS", "cooldown", "expired"]:
        assert needle in s, needle
    assert s.count("- [ ]") >= 8

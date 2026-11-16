"""The built web app must satisfy the CSP: no inline script/style, same-origin only."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parent.parent / "src" / "aliasfox" / "static"
INDEX = DIST / "index.html"

pytestmark = pytest.mark.skipif(
    not INDEX.is_file(), reason="web app not built (cd web && npm run build)"
)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.script_bodies: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.attrs: list[tuple[str, str | None]] = []
        self.styles = 0
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.attrs.extend(attrs)
        if tag == "script":
            self.scripts.append(d)
            self._in_script = True
        elif tag == "link":
            self.links.append(d)
        elif tag == "style":
            self.styles += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.script_bodies.append(data)


@pytest.fixture(scope="module")
def parsed() -> tuple[str, _Collector]:
    text = INDEX.read_text(encoding="utf-8")
    c = _Collector()
    c.feed(text)
    return text, c


def test_no_inline_script(parsed):
    _, c = parsed
    assert c.scripts, "expected a module script"
    assert c.script_bodies == []
    for s in c.scripts:
        assert s.get("src"), "script without src"
        assert s.get("type") == "module"


def test_no_style_tag_style_attribute_or_event_handlers(parsed):
    _, c = parsed
    assert c.styles == 0
    names = [n.lower() for n, _ in c.attrs]
    assert "style" not in names
    assert not [n for n in names if n.startswith("on")]


def test_only_same_origin_urls(parsed):
    text, c = parsed
    assert not re.search(r"https?:", text, re.I)
    assert not re.search(r"(?<!:)//", text), "protocol-relative URL"
    for a in c.scripts + c.links:
        ref = a.get("src") or a.get("href")
        assert ref is None or ref.startswith("/assets/") or ref.startswith("/")


def test_referenced_assets_exist_and_css_is_a_file(parsed):
    _, c = parsed
    refs = [a.get("src") or a.get("href") for a in c.scripts + c.links]
    assets = [r for r in refs if r and r.startswith("/assets/")]
    assert assets
    for r in assets:
        assert (DIST / r.lstrip("/")).is_file(), r
    css = [
        a["href"]
        for a in c.links
        if a.get("rel") == "stylesheet" and a.get("href")
    ]
    assert css and all(h.endswith(".css") for h in css)


def _built(ext: str) -> list[Path]:
    return sorted((DIST / "assets").glob(f"*.{ext}"))


def test_css_has_no_remote_urls_or_imports():
    files = _built("css")
    assert files
    for f in files:
        css = f.read_text(encoding="utf-8")
        assert "@import" not in css, f.name
        for m in re.finditer(r"url\(\s*['\"]?\s*([^'\")\s]+)", css, re.I):
            assert not re.match(r"(https?:)?//", m.group(1), re.I), (f.name, m.group(1))


def test_js_bundle_has_no_javascript_urls_or_runtime_style_injection():
    # Checked against the current Svelte build (css: 'external'): none of these appear, so
    # they are asserted absent. (Remote URLs are NOT scanned in JS: the Svelte runtime
    # legitimately contains https://svelte.dev/e/... error links and xmlns namespace URIs.)
    files = _built("js")
    assert files
    for f in files:
        js = f.read_text(encoding="utf-8")
        assert "javascript:" not in js.lower(), f.name
        assert not re.search(r"createElement\(\s*[\"'`]style[\"'`]", js), f.name
        assert "insertRule" not in js, f.name
        assert "<style" not in js, f.name

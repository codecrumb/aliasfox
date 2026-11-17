"""Release test: the real app serving the BUILT web bundle with auth enabled, over a stateful
fake Apple transport. Everything the browser does on first load, in order.

The bundle is built here if it is missing (npm required); it is never silently skipped when
a bundle exists."""

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from aliasfox.auth import COOKIE_NAME
from aliasfox.crypto import derive_cache_key, hash_password
from test_e2e_app import BASE, COOKIE, COOKIE_VALUE, SECRET, build

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = ROOT / "src" / "aliasfox" / "static"
PASSWORD = "correct horse battery staple"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
ALIAS_KEYS = {
    "anonymous_id",
    "hme",
    "label",
    "note",
    "is_active",
    "forward_to",
    "origin",
    "domain",
    "created_ms",
    "origin_app_name",
    "app_bundle_id",
}


ENSURE_BUILD = WEB / "scripts" / "ensure-build.mjs"
BUILD_INPUTS = ("src", "index.html", "package.json", "vite.config.ts", "svelte.config.js")


def _newest_source_mtime(web: Path) -> float:
    newest = 0.0
    for name in BUILD_INPUTS:
        p = web / name
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        newest = max([newest, *(f.stat().st_mtime for f in files)])
    return newest


@pytest.fixture(scope="session")
def dist() -> Path:
    """The built bundle, guaranteed FRESH (built from the current sources).

    Uses the same rule as web/tests/bundle.test.ts (web/scripts/ensure-build.mjs): rebuild when the
    bundle is missing or older than the sources. Without node: skip only when there is no bundle at
    all; an existing bundle is checked with the same mtime rule and a stale one FAILS."""
    node = shutil.which("node")
    if node is None:
        if not (DIST / "index.html").is_file():
            pytest.skip(f"no built bundle at {DIST} and node is not on PATH to build it")
        if _newest_source_mtime(WEB) > (DIST / "index.html").stat().st_mtime:
            pytest.fail("the web bundle is stale and node is not on PATH to rebuild it")
    else:
        r = subprocess.run([node, str(ENSURE_BUILD)], cwd=WEB, capture_output=True, text=True, timeout=300)
        if r.returncode == 5 and not (DIST / "index.html").is_file():
            pytest.skip(f"no built bundle and npm is not available: {r.stderr.strip()}")
        assert r.returncode == 0, f"ensure-build failed ({r.returncode}): {r.stdout}\n{r.stderr}"
    assert (DIST / "index.html").is_file()
    return DIST


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_ensure_build_flags_a_stale_bundle(tmp_path):
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    src = web / "src" / "main.ts"
    src.write_text("x")
    for name in BUILD_INPUTS[1:]:
        (web / name).write_text("x")
    index = tmp_path / "static" / "index.html"
    index.parent.mkdir()
    index.write_text("<html>")

    def check() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", str(ENSURE_BUILD), "--check", "--web", str(web), "--index", str(index)],
            capture_output=True,
            text=True,
        )

    os.utime(src, (1000, 1000))
    for name in BUILD_INPUTS[1:]:
        os.utime(web / name, (1000, 1000))
    os.utime(index, (2000, 2000))
    assert check().returncode == 0  # fresh
    os.utime(src, (3000, 3000))  # a source touched after the build
    r = check()
    assert r.returncode == 3 and r.stdout.strip() == "stale"
    index.unlink()
    assert check().returncode == 4


@pytest.fixture(scope="module")
def pw_hash() -> str:
    return hash_password(PASSWORD)


class FakeApple:
    """Just enough of iCloud's Hide My Email service, with state."""

    def __init__(self) -> None:
        self.aliases: dict[str, dict] = {
            "id1": self._entry("id1", "fox@icloud.com", "shop", True, 1700000000000),
            "id2": self._entry("id2", "owl@icloud.com", "news", False, 1700000001000),
        }
        self.aliases["id2"].update(
            {"originAppName": "Mail", "appBundleId": "com.apple.mobilemail", "domain": "news.example"}
        )
        self.forward_to: str | None = None
        self.seq = 0
        self.paths: list[str] = []

    @staticmethod
    def _entry(id_, hme, label, active, created, note="") -> dict:
        return {
            "anonymousId": id_,
            "hme": hme,
            "label": label,
            "note": note,
            "isActive": active,
            "createTimestamp": created,
            "forwardToEmail": "me@example.com",
            "origin": "ON_DEMAND",
        }

    def ok(self, result: dict) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": result})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "setup.icloud.com":
            return httpx.Response(
                200,
                json={"webservices": {"premiummailsettings": {"url": BASE}}, "dsInfo": {"dsid": "42"}},
            )
        assert COOKIE_VALUE in request.headers["cookie"]
        path = request.url.path
        self.paths.append(path)
        body = json.loads(request.content or b"{}")
        if path == "/v2/hme/list":
            return self.ok({"hmeEmails": list(self.aliases.values())})
        if path == "/v1/hme/generate":
            self.seq += 1
            return self.ok({"hme": f"gen{self.seq}@icloud.com"})
        if path == "/v1/hme/reserve":
            self.seq += 1
            id_ = f"new{self.seq}"
            self.aliases[id_] = self._entry(
                id_, body["hme"], body["label"], True, 1800000000000 + self.seq, body.get("note", "")
            )
            return self.ok({"hmeEmail": self.aliases[id_]})
        if path == "/v1/hme/updateMetaData":
            entry = self.aliases[body["anonymousId"]]
            entry["label"] = body["label"]
            if "note" in body:
                entry["note"] = body["note"]
            return self.ok({})
        if path in ("/v1/hme/deactivate", "/v1/hme/reactivate"):
            self.aliases[body["anonymousId"]]["isActive"] = path.endswith("reactivate")
            return self.ok({})
        if path == "/v1/hme/delete":
            del self.aliases[body["anonymousId"]]
            return self.ok({})
        if path == "/v1/hme/updateForwardTo":
            self.forward_to = body["forwardToEmail"]
            return self.ok({})
        raise AssertionError(f"unexpected Apple call {path}")


@pytest.fixture
def apple() -> FakeApple:
    return FakeApple()


@pytest.fixture
def client(tmp_path, monkeypatch, dist, pw_hash, apple):
    with build(
        tmp_path,
        monkeypatch,
        handler=apple,
        dist_dir=dist,
        auth_enabled=True,
        password_hash=pw_hash,
    ) as c:
        yield c


def login(c) -> None:
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def decode(key: str) -> bytes:
    assert "=" not in key
    return base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))


# --- the shell -----------------------------------------------------------------------------------


def assert_security_headers(r) -> None:
    h = r.headers
    assert h["content-security-policy"] == CSP
    assert "unsafe-inline" not in CSP and "unsafe-eval" not in CSP
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "DENY"


def test_shell_is_the_built_bundle_with_full_security_headers(client, dist):
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == (dist / "index.html").read_text(encoding="utf-8")
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert_security_headers(r)
    # it really is the built app: hashed same-origin module script and stylesheet, no inline code
    scripts = re.findall(r'<script[^>]*\ssrc="(/assets/[^"]+\.js)"', r.text)
    assert len(scripts) == 1 and 'type="module"' in r.text
    assert re.search(r'<link[^>]*rel="stylesheet"[^>]*href="/assets/[^"]+\.css"', r.text)
    assert "<style" not in r.text and not re.search(r"<script(?![^>]*\ssrc=)", r.text)


def test_hashed_assets_are_immutable_with_correct_types(client, dist):
    html = client.get("/").text
    js = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
    css = re.search(r'href="(/assets/[^"]+\.css)"', html).group(1)
    assert re.search(r"-[A-Za-z0-9_-]{6,}\.", js), "asset names are content-hashed"
    for path, ctype in ((js, ("text/javascript", "application/javascript")), (css, ("text/css",))):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].split(";")[0] in ctype
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert_security_headers(r)
        assert r.content == (dist / path.lstrip("/")).read_bytes()
    assert client.get("/assets/missing-abc123.js").status_code == 404


def test_deep_link_serves_the_shell_and_post_to_root_is_405(client, dist):
    index = (dist / "index.html").read_text(encoding="utf-8")
    for path in ("/some/page", "/a/b/c/d", "/index.html"):
        r = client.get(path)
        assert r.status_code == 200 and r.text == index, path
        assert r.headers["cache-control"] == "no-cache"
        assert_security_headers(r)
    assert client.post("/").status_code == 405
    assert client.put("/some/page").status_code == 405


def test_unknown_api_path_is_json_404_not_the_shell(client):
    login(client)
    for method in ("get", "post"):
        r = getattr(client, method)("/api/x")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert "detail" in r.json()
        assert r.headers["cache-control"] == "no-store"
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/assets/..%2f..%2f..%2fpyproject.toml",
        "/assets/%2e%2e/%2e%2e/%2e%2e/pyproject.toml",
        "/..%2f..%2f..%2fpyproject.toml",
        "/..%5c..%5cpyproject.toml",
        "/%00",
        "/index.html%00.txt",
        "//etc/passwd",
        "/assets/../../pyproject.toml",
    ],
)
def test_path_traversal_does_not_leak_files(client, dist, path):
    r = client.get(path)
    index = (dist / "index.html").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert r.status_code in (200, 400, 404)
    assert "[project]" not in r.text and "root:" not in r.text
    assert pyproject[:200] not in r.text
    if r.status_code == 200:
        assert r.text == index  # only ever the shell, never another file
    assert_security_headers(r)


# --- the API behind login ------------------------------------------------------------------------


def test_session_and_status_require_the_login_cookie(client):
    for path in ("/api/session", "/api/status", "/api/aliases"):
        r = client.get(path)
        assert r.status_code == 401 and r.json() == {"detail": "not_authenticated"}, path
        assert r.headers["cache-control"] == "no-store"
    assert "cache_key" not in client.get("/api/session").text


def test_login_session_key_and_logout_epoch(client):
    login(client)
    r = client.get("/api/session")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"cache_key", "mode", "auth_enabled"} and body["auth_enabled"] is True
    k1 = decode(body["cache_key"])
    assert len(k1) == 32 and k1 == derive_cache_key(SECRET, 1, 0)
    assert client.get("/api/session").json()["cache_key"] == body["cache_key"]  # stable per epoch

    old_cookie = client.cookies.get(COOKIE_NAME)
    assert old_cookie
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/session").status_code == 401
    client.cookies.set(COOKIE_NAME, old_cookie)  # replaying the old cookie must not work
    assert client.get("/api/session").status_code == 401
    client.cookies.clear()

    login(client)
    k2 = decode(client.get("/api/session").json()["cache_key"])
    assert len(k2) == 32 and k2 != k1 and k2 == derive_cache_key(SECRET, 1, 1)


def test_wrong_password_is_401_invalid_credentials(client):
    r = client.post("/api/auth/login", json={"password": "nope nope nope"})
    assert r.status_code == 401 and r.json() == {"detail": "invalid_credentials"}
    assert COOKIE_NAME not in r.headers.get("set-cookie", "")


def test_full_user_journey(client, apple):
    login(client)
    st = client.get("/api/status")
    assert st.status_code == 200
    assert st.json() == {"connected": False, "mode": "single", "auth_enabled": True}
    assert client.get("/api/aliases").json() == {"detail": "not_connected"}

    r = client.post("/api/connect/cookie", json={"cookie": COOKIE})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert client.get("/api/status").json()["connected"] is True

    # list: exact key set, mapped values
    r = client.get("/api/aliases")
    assert r.status_code == 200
    listing = r.json()
    assert {a["anonymous_id"] for a in listing} == {"id1", "id2"}
    for a in listing:
        assert set(a) == ALIAS_KEYS
    by_id = {a["anonymous_id"]: a for a in listing}
    assert by_id["id1"]["hme"] == "fox@icloud.com" and by_id["id1"]["is_active"] is True
    assert by_id["id1"]["origin_app_name"] is None and by_id["id1"]["app_bundle_id"] is None
    assert by_id["id2"]["is_active"] is False and by_id["id2"]["domain"] == "news.example"
    assert by_id["id2"]["origin_app_name"] == "Mail"
    assert by_id["id2"]["app_bundle_id"] == "com.apple.mobilemail"
    assert by_id["id1"]["created_ms"] == 1700000000000

    # generate + reserve
    gen = client.post("/api/aliases/generate")
    assert gen.status_code == 200 and set(gen.json()) == {"hme"}
    r = client.post("/api/aliases", json={"hme": gen.json()["hme"], "label": "fresh", "note": "n"})
    assert r.status_code == 200
    created = r.json()
    assert set(created) == ALIAS_KEYS and created["label"] == "fresh" and created["note"] == "n"
    new_id = created["anonymous_id"]
    assert new_id in {a["anonymous_id"] for a in client.get("/api/aliases").json()}

    # patch label and note
    r = client.patch(f"/api/aliases/{new_id}", json={"label": "renamed", "note": "n2"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    got = {a["anonymous_id"]: a for a in client.get("/api/aliases").json()}[new_id]
    assert (got["label"], got["note"]) == ("renamed", "n2")

    # active toggle both ways
    assert client.post(f"/api/aliases/{new_id}/active", json={"active": False}).json() == {"ok": True}
    assert {a["anonymous_id"]: a for a in client.get("/api/aliases").json()}[new_id]["is_active"] is False
    assert client.post(f"/api/aliases/{new_id}/active", json={"active": True}).json() == {"ok": True}
    assert {a["anonymous_id"]: a for a in client.get("/api/aliases").json()}[new_id]["is_active"] is True

    # forward-to
    r = client.put("/api/forward-to", json={"address": "me@example.com"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert apple.forward_to == "me@example.com"

    # delete
    assert client.delete(f"/api/aliases/{new_id}").json() == {"ok": True}
    assert new_id not in {a["anonymous_id"] for a in client.get("/api/aliases").json()}

    # disconnect: back to not connected
    assert client.delete("/api/connect").json() == {"status": "disconnected"}
    assert client.get("/api/status").json()["connected"] is False
    assert client.get("/api/aliases").status_code == 409


def test_api_responses_carry_security_headers_and_no_store(client):
    login(client)
    for r in (client.get("/api/session"), client.get("/api/status"), client.get("/api/x")):
        assert r.headers["cache-control"] == "no-store"
        assert_security_headers(r)

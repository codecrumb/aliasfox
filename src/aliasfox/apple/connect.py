"""Connect to iCloud with a browser-copied cookie and seed the session store.

The validate endpoint, response field names and the web-auth cookie names
below are reconstructed from other projects and unverified against Apple;
a later live spike may adjust the constants.
"""

import json
import re

import httpx

from aliasfox.apple.hme import apply_set_cookies
from aliasfox.sessions import AppleSessionState, SessionStore

# --- Unverified Apple details -------------------------------------------------
VALIDATE_URL = "https://setup.icloud.com/setup/ws/1/validate"
F_WEBSERVICES = "webservices"
F_PREMIUMMAIL = "premiummailsettings"
F_URL = "url"
F_DSINFO = "dsInfo"
F_DSID = "dsid"
# Assumption (unverified): a usable web session carries a cookie starting with
# this prefix, or this exact one.
WEBAUTH_PREFIX = "X-APPLE-WEBAUTH-"
WEBAUTH_EXACT = "X-APPLE-DS-WEB-SESSION-TOKEN"

_ORIGIN = "https://www.icloud.com"
SEED_USER_ID = 1


class InvalidCookie(Exception):
    """Cookie unusable. Messages never contain cookie values or response bodies."""


def _strip_quotes(text: str) -> str:
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


def _parse_pairs(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in text.split(";"):
        name, sep, value = part.partition("=")
        name = name.strip()
        if sep and name and re.fullmatch(r"[^\s=;,\"']+", name):
            cookies[name] = value.strip()
    return cookies


_CURL_RE = re.compile(
    r"""(?:-H|--header)\s+(?P<q1>['"])\s*cookie\s*:(?P<h>.*?)(?P=q1)"""
    r"""|(?:-b|--cookie)\s+(?P<q2>['"])(?P<b>.*?)(?P=q2)""",
    re.IGNORECASE | re.DOTALL,
)


def parse_cookie_header(text: str) -> dict[str, str]:
    raw = _strip_quotes(text or "")
    if not raw:
        raise InvalidCookie("cookie input is empty")
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            cookies = {
                str(c["name"]): str(c["value"])
                for c in data
                if isinstance(c, dict) and c.get("name") and isinstance(c.get("value"), str)
            }
            if cookies:
                return cookies
        raise InvalidCookie("cookie JSON has no name/value entries")
    m = _CURL_RE.search(raw)
    if m:
        raw = m.group("h") if m.group("q1") else m.group("b")
    else:
        raw = re.sub(r"^\s*cookie\s*:", "", raw, flags=re.IGNORECASE)
    cookies = _parse_pairs(_strip_quotes(raw))
    if not cookies:
        raise InvalidCookie("could not parse any name=value cookies from input")
    return cookies


def _has_webauth(cookies: dict[str, str]) -> bool:
    return any(
        n.upper().startswith(WEBAUTH_PREFIX) or n.upper() == WEBAUTH_EXACT for n in cookies
    )


def _validate(
    cookies: dict[str, str], transport: httpx.BaseTransport | None
) -> tuple[str, str | None]:
    """POST validate; return (webservices_url, dsid) or raise InvalidCookie."""
    headers = {
        "Origin": _ORIGIN,
        "Referer": _ORIGIN + "/",
        "Content-Type": "text/plain",
        "Cookie": "; ".join(f"{k}={v}" for k, v in list(cookies.items())),
    }
    try:
        with httpx.Client(transport=transport, timeout=30.0) as http:
            resp = http.post(VALIDATE_URL, headers=headers)
    except (httpx.HTTPError, UnicodeError, ValueError) as exc:
        raise InvalidCookie(f"validate request failed: {type(exc).__name__}") from None
    if not resp.is_success:
        raise InvalidCookie(f"iCloud rejected the cookie (HTTP {resp.status_code})")
    apply_set_cookies(cookies, resp)
    try:
        data = resp.json()
    except ValueError:
        raise InvalidCookie("validate returned invalid JSON") from None
    ws = data.get(F_WEBSERVICES) if isinstance(data, dict) else None
    pm = ws.get(F_PREMIUMMAIL) if isinstance(ws, dict) else None
    url = pm.get(F_URL) if isinstance(pm, dict) else None
    if not isinstance(url, str) or not url:
        raise InvalidCookie("validate response has no premium mail service url")
    ds = data.get(F_DSINFO)
    dsid = ds.get(F_DSID) if isinstance(ds, dict) else None
    return url, (str(dsid) if dsid is not None else None)


def connect_with_cookie(
    text: str, *, transport: httpx.BaseTransport | None = None
) -> AppleSessionState:
    cookies = parse_cookie_header(text)
    if not _has_webauth(cookies):
        raise InvalidCookie(
            f"missing web-auth token: expected a cookie named {WEBAUTH_PREFIX}* "
            f"or {WEBAUTH_EXACT}"
        )
    url, dsid = _validate(cookies, transport)
    return AppleSessionState(cookies=cookies, webservices_url=url, dsid=dsid)


def is_session_valid(
    state: AppleSessionState, *, transport: httpx.BaseTransport | None = None
) -> bool:
    try:
        _validate(dict(state.cookies), transport)
    except Exception:
        return False
    return True


def seed_from_env(
    settings, store: SessionStore, *, transport: httpx.BaseTransport | None = None
) -> bool:
    text = settings.icloud_cookie
    if not text or not text.strip():
        return False
    existing = store.load(SEED_USER_ID)
    if existing is not None and is_session_valid(existing, transport=transport):
        return False
    store.save(SEED_USER_ID, connect_with_cookie(text, transport=transport))
    return True

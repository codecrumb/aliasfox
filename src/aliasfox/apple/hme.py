"""Hide My Email HTTP client.

Endpoints and payload/field names are reconstructed from other open-source
projects and unverified against Apple; they are all kept in the constants
below so a live spike can adjust them in one place.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from aliasfox.sessions import AppleSessionState

# --- Endpoints (unverified) -------------------------------------------------
EP_LIST = "/v2/hme/list"
EP_GENERATE = "/v1/hme/generate"
EP_RESERVE = "/v1/hme/reserve"
EP_UPDATE_METADATA = "/v1/hme/updateMetaData"
EP_DEACTIVATE = "/v1/hme/deactivate"
EP_REACTIVATE = "/v1/hme/reactivate"
EP_DELETE = "/v1/hme/delete"
EP_UPDATE_FORWARD_TO = "/v1/hme/updateForwardTo"

# --- Request/response field names (unverified) ------------------------------
LANG_CODE = "en-us"
F_LIST = "hmeEmails"
F_GENERATED = "hme"
F_RESERVED = "hmeEmail"
F_ID = "anonymousId"
F_HME = "hme"
F_LABEL = "label"
F_NOTE = "note"
F_ACTIVE = "isActive"
F_FORWARD = "forwardToEmail"
F_ORIGIN = "origin"
F_DOMAIN = "domain"
F_CREATED = "createTimestamp"

_ORIGIN = "https://www.icloud.com"
_EXPIRED_STATUSES = (401, 421)
_MAX_MESSAGE = 200


def _cookie_expired(attrs: list[str]) -> bool:
    for attr in attrs:
        key, _, val = attr.partition("=")
        key, val = key.strip().lower(), val.strip()
        if key == "max-age":
            try:
                if int(val) <= 0:
                    return True
            except ValueError:
                pass
        elif key == "expires":
            try:
                if parsedate_to_datetime(val).timestamp() <= time.time():
                    return True
            except (TypeError, ValueError):
                pass
    return False


def apply_set_cookies(cookies: dict[str, str], resp: httpx.Response) -> bool:
    """Apply Set-Cookie headers to `cookies` in place; return True if changed.

    Expired (Max-Age<=0, past Expires) or empty-valued cookies are deleted.
    """
    changed = False
    for raw in resp.headers.get_list("set-cookie"):
        pair, *attrs = raw.split(";")
        name, sep, value = pair.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            continue
        if not value or _cookie_expired(attrs):
            if name in cookies:
                del cookies[name]
                changed = True
        elif cookies.get(name) != value:
            cookies[name] = value
            changed = True
    return changed


class HmeError(Exception):
    """Error from the HME service. Never carries cookies, tokens or headers."""

    def __init__(self, code: int | str | None, message: str) -> None:
        self.code = code
        self.message = str(message)[:_MAX_MESSAGE]
        super().__init__(code, self.message)

    def __str__(self) -> str:
        return f"{type(self).__name__}(code={self.code}): {self.message}"

    __repr__ = __str__


class SessionExpired(HmeError):
    pass


@dataclass
class Alias:
    anonymous_id: str
    hme: str
    label: str
    note: str
    is_active: bool
    forward_to: str
    origin: str
    domain: str
    created_ms: int | None
    raw: dict = field(default_factory=dict, repr=False)


def _parse_alias(d: dict) -> Alias:
    created = d.get(F_CREATED)
    return Alias(
        anonymous_id=d.get(F_ID) or "",
        hme=d.get(F_HME) or "",
        label=d.get(F_LABEL) or "",
        note=d.get(F_NOTE) or "",
        is_active=bool(d.get(F_ACTIVE)),
        forward_to=d.get(F_FORWARD) or "",
        origin=d.get(F_ORIGIN) or "",
        domain=d.get(F_DOMAIN) or "",
        created_ms=int(created) if isinstance(created, (int, float)) else None,
        raw=d,
    )


class HmeClient:
    def __init__(
        self,
        state: AppleSessionState,
        *,
        transport: httpx.BaseTransport | None = None,
        on_state_change: Callable[[AppleSessionState], None] | None = None,
    ) -> None:
        self._state = state
        self._on_state_change = on_state_change
        self._http = httpx.Client(
            base_url=state.webservices_url, transport=transport, timeout=30.0
        )

    def close(self) -> None:
        self._http.close()

    def __repr__(self) -> str:
        return "HmeClient()"

    # -- transport -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        cookie = "; ".join(f"{k}={v}" for k, v in list(self._state.cookies.items()))
        return {
            "Origin": _ORIGIN,
            "Referer": _ORIGIN + "/",
            "Content-Type": "text/plain",
            "Cookie": cookie,
        }

    def _merge_cookies(self, resp: httpx.Response) -> None:
        changed = apply_set_cookies(self._state.cookies, resp)
        if changed and self._on_state_change:
            self._on_state_change(self._state)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        params = {"dsid": self._state.dsid} if self._state.dsid else None
        content = json.dumps(body) if body is not None else None
        try:
            resp = self._http.request(
                method, path, params=params, content=content, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise HmeError(None, f"network error: {type(exc).__name__}") from None
        except (UnicodeError, ValueError):
            # Raised while encoding the request; the message may echo header values.
            raise HmeError(None, "invalid request") from None
        self._merge_cookies(resp)
        if resp.status_code in _EXPIRED_STATUSES:
            raise SessionExpired(resp.status_code, "session expired")
        if not resp.is_success:
            raise HmeError(resp.status_code, f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise HmeError(resp.status_code, "invalid JSON response") from None
        if not isinstance(data, dict):
            raise HmeError(resp.status_code, "unexpected response shape")
        if not data.get("success"):
            err = data.get("error")
            err = err if isinstance(err, dict) else {}
            raise HmeError(err.get("errorCode"), err.get("errorMessage") or "request failed")
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    # -- operations ------------------------------------------------------------
    def list_raw(self) -> dict:
        """Return the raw `result` dict of the list endpoint (read-only)."""
        return self._request("GET", EP_LIST)

    def list(self) -> list[Alias]:
        result = self.list_raw()
        return [_parse_alias(d) for d in result.get(F_LIST) or [] if isinstance(d, dict)]

    def generate(self) -> str:
        result = self._request("POST", EP_GENERATE, {"langCode": LANG_CODE})
        hme = result.get(F_GENERATED)
        if not hme:
            raise HmeError(None, "generate returned no address")
        return hme

    def reserve(self, hme: str, label: str, note: str = "") -> Alias:
        result = self._request(
            "POST", EP_RESERVE, {F_HME: hme, F_LABEL: label, F_NOTE: note}
        )
        entry = result.get(F_RESERVED)
        if not isinstance(entry, dict):
            raise HmeError(None, "reserve returned no alias")
        return _parse_alias(entry)

    def update_metadata(self, anonymous_id: str, label: str, note: str | None = None) -> None:
        body = {F_ID: anonymous_id, F_LABEL: label}
        if note is not None:
            body[F_NOTE] = note
        self._request("POST", EP_UPDATE_METADATA, body)

    def set_active(self, anonymous_id: str, active: bool) -> None:
        self._request("POST", EP_REACTIVATE if active else EP_DEACTIVATE, {F_ID: anonymous_id})

    def delete(self, anonymous_id: str) -> None:
        body = {F_ID: anonymous_id}
        try:
            self._request("POST", EP_DELETE, body)
        except SessionExpired:
            raise
        except HmeError:
            self._request("POST", EP_DEACTIVATE, body)
            self._request("POST", EP_DELETE, body)

    def update_forward_to(self, address: str) -> None:
        self._request("POST", EP_UPDATE_FORWARD_TO, {F_FORWARD: address})

"""Pure-ASGI middleware (no BaseHTTPMiddleware: it re-wraps bodies and swallows
streamed-receive behaviour we depend on)."""

import json
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-write=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS = "max-age=31536000"

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def url_origin(url: str | None) -> str | None:
    """scheme://host[:port] of an http(s) URL, or None when it is not a plain
    one (no userinfo, wildcard, whitespace or CSP separators reach the header)."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or "@" in parts.netloc or not _HOST_RE.match(host):
        return None
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def csp_with_connect_origins(origins: Iterable[str]) -> str:
    """The base CSP with extra origins appended to connect-src."""
    extra = " ".join(origins)
    base = SECURITY_HEADERS["Content-Security-Policy"]
    if not extra:
        return base
    return base.replace("connect-src 'self'", f"connect-src 'self' {extra}", 1)


def scope_is_https(scope: Scope, trusted_proxy: bool) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    if not trusted_proxy:
        return False
    proto = Headers(scope=scope).get("x-forwarded-proto", "")
    return proto.split(",")[-1].strip().lower() == "https"


def _json_body(detail: str) -> bytes:
    return json.dumps({"detail": detail}).encode()


async def _send_json(send: Send, status: int, detail: str) -> None:
    body = _json_body(detail)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Outermost: adds the security headers (and HSTS over https) to every HTTP
    response, including ones produced by inner middleware, and contains any
    unhandled exception as a fixed 500 so even that response carries them."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_proxy: bool = False,
        connect_origins: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.trusted_proxy = trusted_proxy
        origins = [o for o in (url_origin(x) for x in connect_origins) if o is not None]
        self.headers = {
            **SECURITY_HEADERS,
            "Content-Security-Policy": csp_with_connect_origins(origins),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = scope_is_https(scope, self.trusted_proxy)
        api = scope.get("path", "").startswith("/api/")
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers[name] = value
                if api:
                    headers["Cache-Control"] = "no-store"
                if https:
                    headers["Strict-Transport-Security"] = HSTS
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # class name only: messages may carry secrets
            log.error("unhandled error: %s", type(exc).__name__)
            if not started:
                await _send_json(send_wrapper, 500, "internal_error")


class BodyLimitMiddleware:
    """Reject bodies over `max_bytes` with 413, by Content-Length up front or by
    counting streamed bytes (chunked uploads)."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = True  # malformed: fail closed
            if too_big:
                await _send_json(send, 413, "payload_too_large")
                return

        seen = 0
        rejected = False

        async def reject() -> None:
            nonlocal rejected
            if not rejected:
                rejected = True
                await _send_json(send, 413, "payload_too_large")

        async def limited_receive() -> Message:
            nonlocal seen
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    await reject()
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if rejected:
                return  # the app's own (400/500) reaction to the cut-off body
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


def is_key_path(path: str) -> bool:
    """Paths authenticated by API key (never cookies): no origin check, open CORS."""
    return (
        path in ("/api/v1", "/api/alias/random/new", "/mcp")
        or path.startswith(("/api/v1/", "/mcp/"))
    )


_CORS_PREFLIGHT = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-headers", b"Authorization, Authentication, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version"),
    (b"access-control-allow-methods", b"GET, POST, PATCH, DELETE, OPTIONS"),
    (b"access-control-max-age", b"600"),
]


class KeyCorsMiddleware:
    """Wildcard CORS on key paths. Never credentials, never echoes Origin: keys
    ride in a header, so no ambient authority is exposed to foreign pages."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_key_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": _CORS_PREFLIGHT})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Access-Control-Allow-Origin"] = "*"
            await send(message)

        await self.app(scope, receive, send_wrapper)

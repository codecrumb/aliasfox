import ipaddress
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from aliasfox.config import ConfigError, Settings
from aliasfox.crypto import derive_key, verify_password
from aliasfox.ratelimit import RateLimiter

COOKIE_NAME = "aliasfox_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
GLOBAL_MAX_FAILURES = 30
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GUARD_EXEMPT_PATHS = {"/healthz"}
USER_ID = 1


class LoginBody(BaseModel):
    password: str = Field(max_length=1024)


def client_ip(request: Request, *, trusted_proxy: bool) -> str:
    """Best-effort client address for throttling.

    With a trusted proxy we use the RIGHTMOST X-Forwarded-For entry: a single
    trusted proxy appends the real peer to the end, while earlier entries are
    client-controlled and spoofable. Render's actual proxy behaviour is
    unverified.
    """
    if trusted_proxy:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        parts = [p for p in parts if p]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def limiter_key(ip: str) -> str:
    """IPv4 stays the full address; IPv6 collapses to its /64 prefix."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise ConfigError("ALIASFOX_SECRET_KEY is required for auth")
    key = derive_key(settings.secret_key, info=b"aliasfox-cookie-v1")
    return URLSafeTimedSerializer(key.hex())


def _is_https(request: Request, settings: Settings) -> bool:
    if request.url.scheme == "https":
        return True
    return (
        settings.trusted_proxy
        and request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower() == "https"
    )


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


def _throttled(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "too_many_attempts"},
        status_code=429,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def current_user(request: Request) -> int:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return USER_ID
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            data = _serializer(settings).loads(token, max_age=COOKIE_MAX_AGE)
        except BadSignature:  # includes SignatureExpired
            data = None
        if isinstance(data, dict) and data.get("u") == USER_ID:
            return USER_ID
    raise HTTPException(status_code=401, detail="not_authenticated")


def install_auth(app: FastAPI, settings: Settings) -> None:
    _serializer(settings)  # fail fast: no fallback signing key
    app.state.settings = settings
    app.state.login_limiter = limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)
    # Global failed-login budget across all IPs. Trade-off: an attacker can
    # exhaust it and block NEW logins for up to 15 minutes, but cannot log in;
    # existing signed session cookies keep working.
    global_limiter = RateLimiter(GLOBAL_MAX_FAILURES, LOGIN_WINDOW_SECONDS)
    global_key = "global"

    @app.middleware("http")
    async def request_guards(request: Request, call_next):
        if request.url.path not in GUARD_EXEMPT_PATHS:
            host = request.headers.get("host", "")
            if not settings.auth_enabled and _hostname(host) not in LOCAL_HOSTNAMES:
                return JSONResponse({"detail": "bad_host"}, status_code=403)
            origin = request.headers.get("origin")
            if (
                origin is not None
                and request.method in UNSAFE_METHODS
                and urlsplit(origin).netloc.lower() != host.strip().lower()
            ):
                return JSONResponse({"detail": "bad_origin"}, status_code=403)
        return await call_next(request)

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        ip = limiter_key(client_ip(request, trusted_proxy=settings.trusted_proxy))
        # Reserve a slot atomically BEFORE verifying; a success gives it back,
        # so only failures stay counted (and concurrent guesses cannot race past).
        if not limiter.hit(ip):
            return _throttled(limiter.retry_after(ip))
        if not global_limiter.hit(global_key):
            limiter.release(ip)
            return _throttled(global_limiter.retry_after(global_key))
        stored = settings.password_hash
        if not settings.auth_enabled or not stored or not verify_password(stored, body.password):
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
        limiter.release(ip)
        global_limiter.release(global_key)
        response.set_cookie(
            COOKIE_NAME,
            _serializer(settings).dumps({"u": USER_ID}),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
            secure=_is_https(request, settings),
            path="/",
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        response.delete_cookie(
            COOKIE_NAME,
            httponly=True,
            samesite="strict",
            secure=_is_https(request, settings),
            path="/",
        )
        return {"ok": True}

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from aliasfox.config import Settings
from aliasfox.crypto import derive_key, verify_password
from aliasfox.ratelimit import RateLimiter

COOKIE_NAME = "aliasfox_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GUARD_EXEMPT_PATHS = {"/healthz"}
USER_ID = 1


class LoginBody(BaseModel):
    password: str


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


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    key = derive_key(settings.secret_key or "", info=b"aliasfox-cookie-v1")
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
    app.state.settings = settings
    app.state.login_limiter = limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)

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
        ip = client_ip(request, trusted_proxy=settings.trusted_proxy)
        if limiter.blocked(ip):
            return JSONResponse(
                {"detail": "too_many_attempts"},
                status_code=429,
                headers={"Retry-After": str(limiter.retry_after(ip))},
            )
        stored = settings.password_hash
        if not settings.auth_enabled or not stored or not verify_password(stored, body.password):
            limiter.hit(ip)
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
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

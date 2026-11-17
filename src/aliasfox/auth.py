import ipaddress
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from aliasfox.config import ConfigError, Settings
from aliasfox.crypto import derive_key, verify_password
from aliasfox.middleware import scope_is_https
from aliasfox.ratelimit import RateLimiter
from aliasfox.users import UserDirectory

COOKIE_NAME = "aliasfox_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_CONCURRENCY = 2  # Argon2 uses 64 MiB each: bound the memory an anonymous caller can pin
LOGIN_SLOT_WAIT_SECONDS = 5
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GUARD_EXEMPT_PATHS = {"/healthz"}
USER_ID = 1


class LoginBody(BaseModel):
    password: str = Field(max_length=1024)


def client_ip(request: Request, *, trusted_proxy: bool, hops: int = 1) -> str:
    """Best-effort client address for throttling.

    With trusted proxies we use the `hops`-th X-Forwarded-For entry from the
    right: each trusted proxy appends the address it saw, while entries further
    left are client-controlled and spoofable. `hops` must equal the number of
    trusted proxies in front of us (Render's actual chain is unverified). If
    the header has fewer entries than that, it was not built by our chain and
    we fall back to the socket peer.
    """
    if trusted_proxy:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        parts = [p for p in parts if p]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def limiter_key(ip: str) -> str:
    """IPv4 (incl. IPv4-mapped IPv6) stays the full address; IPv6 collapses to its /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class MemoryEpochs:
    """Process-local epochs for apps without a database (unit tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epochs = {USER_ID: 0}

    def get(self, user_id: int) -> int | None:
        return self._epochs.get(user_id)

    def bump(self, user_id: int) -> int:
        with self._lock:
            self._epochs[user_id] = self._epochs.get(user_id, 0) + 1
            return self._epochs[user_id]


def _plain_int(value: object) -> bool:
    # bool is an int subclass (True == 1) and 1.0 == 1: both must be refused.
    return isinstance(value, int) and not isinstance(value, bool)


def _validated_epoch(request: Request, settings: Settings) -> tuple[int, int] | None:
    """(user_id, epoch) from the cookie, if it is signed, unexpired, names an
    existing user and carries that user's current epoch. This is the ONLY
    source of the authenticated user id."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer(settings).loads(token, max_age=COOKIE_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict):
        return None
    user_id, epoch = data.get("u"), data.get("e")
    if not _plain_int(user_id) or not _plain_int(epoch):
        return None
    current = request.app.state.epochs.get(user_id)
    if current is None or epoch != current:
        return None
    return user_id, epoch


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise ConfigError("ALIASFOX_SECRET_KEY is required for auth")
    key = derive_key(settings.secret_key, info=b"aliasfox-cookie-v1")
    return URLSafeTimedSerializer(key.hex())


def _is_https(request: Request, settings: Settings) -> bool:
    return scope_is_https(request.scope, settings.trusted_proxy)


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """Secure when the request is https (directly or via a trusted proxy), and
    also for ANY non-loopback host once auth is on: a TLS-terminating proxy that
    we cannot see must not yield a downgradable session cookie."""
    if _is_https(request, settings):
        return True
    return settings.auth_required and _hostname(request.headers.get("host", "")) not in LOCAL_HOSTNAMES


def issue_cookie(
    response: Response, request: Request, settings: Settings, user_id: int, epoch: int
) -> None:
    """Set the signed session cookie for `user_id` at `epoch`."""
    response.set_cookie(
        COOKIE_NAME,
        _serializer(settings).dumps({"u": user_id, "e": epoch}),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request, settings),
        path="/",
    )


def _throttled(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "too_many_attempts"},
        status_code=429,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def current_user(request: Request) -> int:
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        request.state.session_epoch = 0  # no sessions: constant by construction
        return USER_ID
    validated = _validated_epoch(request, settings)
    if validated is not None:
        user_id, request.state.session_epoch = validated
        return user_id
    raise HTTPException(status_code=401, detail="not_authenticated")


def install_auth(
    app: FastAPI,
    settings: Settings,
    epochs: MemoryEpochs | UserDirectory | None = None,
    *,
    password_login: bool = True,
) -> None:
    """Session cookie validation, request guards and logout.

    `password_login=False` (multi mode) leaves `/api/auth/login` unregistered;
    sign-in then happens through `exchange.install_exchange`.
    """
    _serializer(settings)  # fail fast: no fallback signing key
    app.state.settings = settings
    app.state.epochs = epochs or MemoryEpochs()
    app.state.login_limiter = limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)
    # Process-wide bound on concurrent password verifications. There is
    # deliberately NO global failure lockout: a few attacker IPs could keep the
    # owner out forever. The per-IP limiter plus this bound cap the damage.
    verify_slots = threading.BoundedSemaphore(LOGIN_CONCURRENCY)

    @app.middleware("http")
    async def request_guards(request: Request, call_next):
        if request.url.path not in GUARD_EXEMPT_PATHS:
            host = request.headers.get("host", "")
            if not settings.auth_required and _hostname(host) not in LOCAL_HOSTNAMES:
                return JSONResponse({"detail": "bad_host"}, status_code=403)
            origin = request.headers.get("origin")
            if (
                origin is not None
                and request.method in UNSAFE_METHODS
                and urlsplit(origin).netloc.lower() != host.strip().lower()
            ):
                return JSONResponse({"detail": "bad_origin"}, status_code=403)
        return await call_next(request)

    def login(body: LoginBody, request: Request, response: Response):
        ip = limiter_key(client_ip(
                request, trusted_proxy=settings.trusted_proxy, hops=settings.trusted_proxy_hops
            ))
        # Reserve a slot atomically BEFORE verifying; a success gives it back,
        # so only failures stay counted (and concurrent guesses cannot race past).
        if not limiter.hit(ip):
            return _throttled(limiter.retry_after(ip))
        stored = settings.password_hash
        if not settings.auth_enabled or not stored:
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
        if not verify_slots.acquire(timeout=LOGIN_SLOT_WAIT_SECONDS):
            limiter.release(ip)  # never verified: not a failed guess
            return JSONResponse(
                {"detail": "busy"}, status_code=429, headers={"Retry-After": "5"}
            )
        try:
            ok = verify_password(stored, body.password)
        finally:
            verify_slots.release()
        if not ok:
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
        limiter.release(ip)
        epoch = request.app.state.epochs.get(USER_ID)
        if epoch is None:
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
        issue_cookie(response, request, settings, USER_ID, epoch)
        return {"ok": True}

    if password_login:
        app.post("/api/auth/login")(login)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        # "Logout = log out everywhere": bumping the epoch invalidates every
        # outstanding cookie of THAT user. Only a valid cookie may bump it, and
        # only for the user it names, so nobody can log anyone else out.
        validated = _validated_epoch(request, settings)
        if validated is not None:
            request.app.state.epochs.bump(validated[0])
        response.delete_cookie(
            COOKIE_NAME,
            httponly=True,
            samesite="strict",
            secure=_cookie_secure(request, settings),
            path="/",
        )
        return {"ok": True}

"""JSON API for Hide My Email aliases. Every route requires `current_user`; the
user id handed to the service comes only from there, never from request input."""

import base64
import time
from typing import Annotated

from fastapi.exceptions import RequestValidationError
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from aliasfox.apple.connect import AppleUnreachable, InvalidCookie, connect_with_cookie
from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.apple.login import (
    AppleLoginFailed,
    FlowExpired,
    InvalidCode,
    LoginFlow,
    TooManyFlows,
)
from aliasfox.auth import client_ip, current_user, limiter_key
from aliasfox.crypto import derive_cache_key
from aliasfox.ratelimit import RateLimiter
from aliasfox.service import AliasService, NotConnected
from aliasfox.sessions import SessionStore, SessionUnreadable

APPLE_WINDOW_SECONDS = 15 * 60
LOGIN_PER_IP = 5
LOGIN_GLOBAL = 15
VERIFY_PER_IP = 10
HME_MESSAGE_MAX = 200
REFRESH_MIN_INTERVAL = 5.0
CREATE_PER_MINUTE = 10


def _now() -> float:
    return time.monotonic()

AliasId = Annotated[str, Path(min_length=1, max_length=128)]


class ReserveBody(BaseModel):
    hme: str = Field(min_length=1, max_length=320)
    label: str = Field(max_length=256)
    note: str = Field("", max_length=2048)


class PatchBody(BaseModel):
    label: str = Field(max_length=256)
    note: str | None = Field(None, max_length=2048)


class ActiveBody(BaseModel):
    active: bool


class ForwardBody(BaseModel):
    address: str = Field(min_length=1, max_length=320)


class CookieBody(BaseModel):
    cookie: str = Field(max_length=16384)


class AppleLoginBody(BaseModel):
    apple_id: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    china: bool = False


class VerifyBody(BaseModel):
    flow_id: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


def _rate_limited(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "rate_limited"},
        status_code=429,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def alias_json(a: Alias) -> dict:
    return {
        "anonymous_id": a.anonymous_id,
        "hme": a.hme,
        "label": a.label,
        "note": a.note,
        "is_active": a.is_active,
        "forward_to": a.forward_to,
        "origin": a.origin,
        "domain": a.domain,
        "created_ms": a.created_ms,
        "origin_app_name": a.raw.get("originAppName"),
        "app_bundle_id": a.raw.get("appBundleId"),
    }


def build_router(
    service: AliasService,
    store: SessionStore | None,
    settings,
    login_flow: LoginFlow | None = None,
) -> APIRouter:
    if store is None:
        raise RuntimeError("the connect routes need a SessionStore")
    login_flow = login_flow or LoginFlow()
    login_ip = RateLimiter(LOGIN_PER_IP, APPLE_WINDOW_SECONDS)
    login_global = RateLimiter(LOGIN_GLOBAL, APPLE_WINDOW_SECONDS)
    verify_ip = RateLimiter(VERIFY_PER_IP, APPLE_WINDOW_SECONDS)
    cookie_ip = RateLimiter(VERIFY_PER_IP, APPLE_WINDOW_SECONDS)
    global_key = "global"
    create_limit = RateLimiter(CREATE_PER_MINUTE, 60, clock=lambda: _now())
    last_refresh: dict[int, float] = {}

    def key_of(request: Request) -> str:
        return limiter_key(client_ip(
                request, trusted_proxy=settings.trusted_proxy, hops=settings.trusted_proxy_hops
            ))

    def _create_limited(user_id: int) -> JSONResponse | None:
        key = str(user_id)
        if not create_limit.hit(key):
            return _rate_limited(create_limit.retry_after(key))
        return None

    router = APIRouter(prefix="/api")
    User = Annotated[int, Depends(current_user)]

    @router.get("/session")
    def session(request: Request, user_id: User):
        if not settings.secret_key:
            raise HTTPException(status_code=500, detail="internal_error")
        epoch = request.state.session_epoch  # the one current_user validated
        key = derive_cache_key(settings.secret_key, user_id, epoch)
        return {
            "cache_key": base64.urlsafe_b64encode(key).rstrip(b"=").decode(),
            "mode": settings.aliasfox_mode,
            "auth_enabled": settings.auth_enabled,
        }

    @router.get("/aliases")
    async def list_aliases(user_id: User, refresh: bool = False):
        if refresh:
            now = _now()
            last = last_refresh.get(user_id)
            if last is not None and now - last < REFRESH_MIN_INTERVAL:
                refresh = False  # too soon: serve the cache
            else:
                last_refresh[user_id] = now
        return [alias_json(a) for a in await service.list_aliases(user_id, refresh=refresh)]

    @router.post("/aliases/generate")
    async def generate(user_id: User):
        if (limited := _create_limited(user_id)) is not None:
            return limited
        return {"hme": await service.generate(user_id)}

    @router.post("/aliases")
    async def reserve(body: ReserveBody, user_id: User):
        if (limited := _create_limited(user_id)) is not None:
            return limited
        return alias_json(await service.reserve(user_id, body.hme, body.label, body.note))

    @router.patch("/aliases/{alias_id}")
    async def patch(alias_id: AliasId, body: PatchBody, user_id: User):
        await service.update(user_id, alias_id, body.label, body.note)
        return {"ok": True}

    @router.post("/aliases/{alias_id}/active")
    async def set_active(alias_id: AliasId, body: ActiveBody, user_id: User):
        await service.set_active(user_id, alias_id, body.active)
        return {"ok": True}

    @router.delete("/aliases/{alias_id}")
    async def delete(alias_id: AliasId, user_id: User):
        await service.delete(user_id, alias_id)
        return {"ok": True}

    @router.put("/forward-to")
    async def forward_to(body: ForwardBody, user_id: User):
        await service.set_forward_to(user_id, body.address)
        return {"ok": True}

    @router.get("/status")
    async def status(user_id: User):
        body: dict = {"mode": settings.aliasfox_mode, "auth_enabled": settings.auth_enabled}
        try:
            body = {"connected": await service.is_connected(user_id), **body}
        except SessionUnreadable:
            body = {"connected": False, "reason": "session_unreadable", **body}
        return body

    # --- Apple connect -------------------------------------------------------
    # Limiter slots are reserved BEFORE touching Apple and are NOT given back on
    # failure (each attempt can push a 2FA prompt and failures can lock the
    # account). They are released only if the attempt never reached Apple.

    @router.post("/connect/cookie")
    async def connect_cookie(body: CookieBody, request: Request, user_id: User):
        key = key_of(request)
        if not cookie_ip.hit(key):
            return _rate_limited(cookie_ip.retry_after(key))
        try:
            state = await run_in_threadpool(connect_with_cookie, body.cookie)
        except InvalidCookie as exc:
            return JSONResponse(
                {"detail": "invalid_cookie", "message": str(exc)}, status_code=400
            )
        await service.replace_session(user_id, state)
        return {"status": "ok"}

    @router.post("/connect/login")
    async def connect_login(body: AppleLoginBody, request: Request, user_id: User):
        key = key_of(request)
        if not login_ip.hit(key):
            return _rate_limited(login_ip.retry_after(key))
        if not login_global.hit(global_key):
            login_ip.release(key)
            return _rate_limited(login_global.retry_after(global_key))
        try:
            result = await run_in_threadpool(
                login_flow.start,
                user_id,
                body.apple_id,
                body.password,
                china=body.china,
            )
        except TooManyFlows:
            login_ip.release(key)  # never reached Apple
            login_global.release(global_key)
            raise
        if result.status == "ok":
            await service.replace_session(user_id, result.state)
            return {"status": "ok"}
        return {"status": "needs_2fa", "flow_id": result.flow_id}

    @router.post("/connect/verify")
    async def connect_verify(body: VerifyBody, request: Request, user_id: User):
        key = key_of(request)
        if not verify_ip.hit(key):
            return _rate_limited(verify_ip.retry_after(key))
        state = await run_in_threadpool(login_flow.verify, user_id, body.flow_id, body.code)
        await service.replace_session(user_id, state)
        return {"status": "ok"}

    @router.delete("/connect")
    async def disconnect(user_id: User):
        await run_in_threadpool(login_flow.cancel_user, user_id)
        await service.disconnect(user_id)
        return {"status": "disconnected"}

    return router


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default echoes `input` (and `ctx`): that would leak submitted
        # passwords, cookies and 2FA codes. Keep only loc/msg/type.
        detail = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(SessionUnreadable)
    async def session_unreadable(request: Request, exc: SessionUnreadable):
        return JSONResponse({"detail": "session_unreadable"}, status_code=409)

    @app.exception_handler(AppleUnreachable)
    async def apple_unreachable(request: Request, exc: AppleUnreachable):
        return JSONResponse({"detail": "apple_unreachable"}, status_code=502)

    @app.exception_handler(NotConnected)
    async def not_connected(request: Request, exc: NotConnected):
        return JSONResponse({"detail": "not_connected"}, status_code=409)

    @app.exception_handler(SessionExpired)
    async def session_expired(request: Request, exc: SessionExpired):
        return JSONResponse({"detail": "session_expired"}, status_code=401)

    @app.exception_handler(HmeError)
    async def apple_error(request: Request, exc: HmeError):
        return JSONResponse(
            {"detail": "apple_error", "code": exc.code, "message": (exc.message or "")[:HME_MESSAGE_MAX]},
            status_code=502,
        )

    @app.exception_handler(AppleLoginFailed)
    async def apple_login_failed(request: Request, exc: AppleLoginFailed):
        return JSONResponse({"detail": "apple_login_failed"}, status_code=401)

    @app.exception_handler(InvalidCode)
    async def invalid_code(request: Request, exc: InvalidCode):
        return JSONResponse({"detail": "invalid_code"}, status_code=401)

    @app.exception_handler(FlowExpired)
    async def flow_expired(request: Request, exc: FlowExpired):
        return JSONResponse({"detail": "flow_expired"}, status_code=410)

    @app.exception_handler(TooManyFlows)
    async def too_many_flows(request: Request, exc: TooManyFlows):
        return JSONResponse({"detail": "too_many_attempts"}, status_code=429)

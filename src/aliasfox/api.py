"""JSON API for Hide My Email aliases. Every route requires `current_user`; the
user id handed to the service comes only from there, never from request input."""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.auth import current_user
from aliasfox.service import AliasService, NotConnected
from aliasfox.sessions import SessionStore

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


def build_router(service: AliasService, store: SessionStore | None, settings) -> APIRouter:
    # `store` is unused here; Task 9b's connect routes use it.
    router = APIRouter(prefix="/api")
    User = Annotated[int, Depends(current_user)]

    @router.get("/aliases")
    async def list_aliases(user_id: User, refresh: bool = False):
        return [alias_json(a) for a in await service.list_aliases(user_id, refresh=refresh)]

    @router.post("/aliases/generate")
    async def generate(user_id: User):
        return {"hme": await service.generate(user_id)}

    @router.post("/aliases")
    async def reserve(body: ReserveBody, user_id: User):
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
        return {
            "connected": await service.is_connected(user_id),
            "mode": settings.aliasfox_mode,
            "auth_enabled": settings.auth_enabled,
        }

    return router


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotConnected)
    async def not_connected(request: Request, exc: NotConnected):
        return JSONResponse({"detail": "not_connected"}, status_code=409)

    @app.exception_handler(SessionExpired)
    async def session_expired(request: Request, exc: SessionExpired):
        return JSONResponse({"detail": "session_expired"}, status_code=401)

    @app.exception_handler(HmeError)
    async def apple_error(request: Request, exc: HmeError):
        return JSONResponse(
            {"detail": "apple_error", "code": exc.code, "message": exc.message},
            status_code=502,
        )

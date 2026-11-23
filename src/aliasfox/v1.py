"""Key-authenticated alias API. The user id comes only from the key."""
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from aliasfox.api import AliasId, ActiveBody, PatchBody, alias_json
from aliasfox.apikeys import KeyInfo
from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.createguard import CreateGuard
from aliasfox.keyauth import KeyAuth
from aliasfox.service import AliasService


class CreateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


class CreateBody(BaseModel):
    label: str = Field(max_length=256)
    note: str = Field("", max_length=2048)


async def create_alias(
    service: AliasService, guard: CreateGuard, user_id: int, label: str, note: str = ""
) -> Alias:
    """Generate + reserve one alias under the shared create budget (two hits, like the UI)."""

    def hit() -> None:
        if (wait := guard.check(user_id)) is not None:
            raise CreateLimited(wait)

    try:
        hit()
        try:
            hme = await service.generate(user_id)
            hit()
            alias = await service.reserve(user_id, hme, label, note)
        except HmeError as exc:
            if str(exc.code) != "-41003":
                raise
            hme = await service.generate(user_id)  # Apple asked for a fresh address; no new budget hit
            alias = await service.reserve(user_id, hme, label, note)
    except HmeError:
        guard.record_error(user_id)
        raise
    guard.record_success(user_id)
    return alias


class _V1Route(APIRoute):
    """Maps SessionExpired (global handler: 401) to 503 and CreateLimited to 429, on v1 only."""

    def get_route_handler(self) -> Callable:
        inner = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await inner(request)
            except SessionExpired:
                return JSONResponse({"detail": "session_expired"}, status_code=503)
            except CreateLimited as e:
                return JSONResponse(
                    {"detail": "rate_limited"}, status_code=429,
                    headers={"Retry-After": str(max(1, e.retry_after))},
                )

        return handler


def build_v1_router(service: AliasService, key_auth: KeyAuth, guard: CreateGuard) -> APIRouter:
    router = APIRouter(prefix="/api/v1", route_class=_V1Route)
    Any_ = Annotated[KeyInfo, Depends(key_auth.require())]
    Read = Annotated[KeyInfo, Depends(key_auth.require("read"))]
    Create = Annotated[KeyInfo, Depends(key_auth.require("create"))]
    Edit = Annotated[KeyInfo, Depends(key_auth.require("edit"))]
    Deactivate = Annotated[KeyInfo, Depends(key_auth.require("deactivate"))]
    Delete = Annotated[KeyInfo, Depends(key_auth.require("delete"))]

    @router.get("/me")
    async def me(key: Any_):
        return {
            "name": key.name,
            "scopes": sorted(key.scopes),
            "connected": await service.is_connected(key.user_id),
        }

    @router.get("/aliases")
    async def list_aliases(key: Read):
        return [alias_json(a) for a in await service.list_aliases(key.user_id)]

    @router.post("/aliases", status_code=201)
    async def create(body: CreateBody, key: Create):
        return alias_json(await create_alias(service, guard, key.user_id, body.label, body.note))

    @router.patch("/aliases/{alias_id}")
    async def patch(alias_id: AliasId, body: PatchBody, key: Edit):
        await service.update(key.user_id, alias_id, body.label, body.note)
        return {"ok": True}

    @router.post("/aliases/{alias_id}/active")
    async def set_active(alias_id: AliasId, body: ActiveBody, key: Deactivate):
        await service.set_active(key.user_id, alias_id, body.active)
        return {"ok": True}

    @router.delete("/aliases/{alias_id}")
    async def delete(alias_id: AliasId, key: Delete):
        await service.delete(key.user_id, alias_id)
        return {"ok": True}

    return router

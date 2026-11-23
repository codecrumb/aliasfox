"""Cookie-authenticated API key management. A key presented here is not accepted."""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from aliasfox.apikeys import PRESETS, SCOPES, ApiKeyStore, KeyInfo, TooManyKeys
from aliasfox.auth import current_user
from aliasfox.keyauth import KeyAuth

Scope = Literal["read", "create", "edit", "deactivate", "delete"]


class NewKey(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    preset: Literal["bitwarden", "readonly", "assistant", "full"] | None = None
    scopes: Annotated[list[Scope], Field(min_length=1)] | None = None
    expires_days: Annotated[int, Field(ge=1, le=365)] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "NewKey":
        if (self.preset is None) == (self.scopes is None):
            raise ValueError("give exactly one of preset or scopes")
        return self


def _view(info: KeyInfo) -> dict:
    return {
        "id": info.id,
        "name": info.name,
        "preset": info.preset,
        "scopes": [s for s in SCOPES if s in info.scopes],
        "prefix": info.prefix,
        "created_at": info.created_at,
        "last_used_at": info.last_used_at,
        "expires_at": info.expires_at,
    }


def build_key_router(store: ApiKeyStore, key_auth: KeyAuth) -> APIRouter:
    router = APIRouter(prefix="/api/keys")
    User = Annotated[int, Depends(current_user)]

    @router.get("")
    def list_keys(user_id: User):
        return [_view(k) for k in store.list_for_user(user_id)]

    @router.post("", status_code=201)
    def create_key(body: NewKey, user_id: User):
        scopes = PRESETS[body.preset] if body.preset else body.scopes
        expires = datetime.now(timezone.utc) + timedelta(days=body.expires_days) if body.expires_days else None
        try:
            key, info = store.create(user_id, body.name, scopes, preset=body.preset, expires_at=expires)
        except TooManyKeys:
            return JSONResponse({"detail": "too_many_keys"}, status_code=409)
        return {**_view(info), "key": key}

    @router.delete("/{key_id}")
    def revoke_key(key_id: int, user_id: User):
        if not store.revoke(user_id, key_id):
            raise HTTPException(404, "not_found")
        key_auth.invalidate(key_id)
        return {"ok": True}

    return router

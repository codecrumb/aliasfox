"""SimpleLogin-compatible create endpoint for the Bitwarden client. Errors are {"error": text}."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aliasfox.apple.hme import HmeError, SessionExpired
from aliasfox.config import Settings
from aliasfox.createguard import CreateGuard
from aliasfox.keyauth import KeyAuth, KeyAuthError
from aliasfox.service import AliasService, NotConnected
from aliasfox.v1 import CreateLimited, create_alias


def _err(status: int, text: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(max(1, retry_after or 0))} if status == 429 else None
    return JSONResponse({"error": text}, status_code=status, headers=headers)


def build_simplelogin_router(
    service: AliasService, key_auth: KeyAuth, guard: CreateGuard, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/alias/random/new", status_code=201)
    async def random_new(request: Request, hostname: str = ""):
        try:
            key = key_auth.authenticate(request, "create")
        except KeyAuthError as e:
            if e.status == 401:
                return _err(401, "Invalid API key")
            if e.status == 403:
                return _err(403, "This key cannot create addresses")
            if e.status == 503:
                return _err(503, "API keys are disabled")
            return _err(e.status, "Too many requests", e.retry_after)
        try:
            body = await request.json()
        except Exception:  # missing or invalid JSON means no note
            body = None
        note = body.get("note") if isinstance(body, dict) else None
        note = note[:2048] if isinstance(note, str) else ""
        label = hostname.strip()[:256] or "Bitwarden"
        try:
            alias = await create_alias(service, guard, key.user_id, label, note)
        except CreateLimited as e:
            return _err(429, f"Too many addresses created. Try again in {max(1, e.retry_after)} seconds.", e.retry_after)
        except (NotConnected, SessionExpired):
            url = f" Sign in again at {settings.public_url}" if settings.public_url else ""
            return _err(503, "Apple session expired." + url)
        except HmeError as e:
            return _err(502, f"iCloud: {e.message[:200]}")
        return {"alias": alias.hme}

    return router

"""POST /api/auth/exchange: trade a Neon Auth token for a local session cookie.

The token's `sub` maps to a local user. A known subject signs in. An unknown
subject needs open sign-ups or an email on the allowlist, and then a verified
email; the email is compared, never stored or logged. The session
cookie names the local user id and is validated against the database epoch on
every request (auth.current_user).
"""
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aliasfox.auth import (
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    _throttled,
    client_ip,
    issue_cookie,
    limiter_key,
)
from aliasfox.config import Settings
from aliasfox.neon_jwt import Identity, TokenRejected
from aliasfox.ratelimit import RateLimiter, track
from aliasfox.users import UserDirectory

TOKEN_MAX_LENGTH = 4096
EXCHANGE_MAX_FAILURES = LOGIN_MAX_FAILURES
EXCHANGE_WINDOW_SECONDS = LOGIN_WINDOW_SECONDS


class Verifier(Protocol):
    def verify(self, token: str) -> Identity: ...


class ExchangeBody(BaseModel):
    token: str = Field(max_length=TOKEN_MAX_LENGTH)


def neon_issuer(settings: Settings) -> str:
    """NEON_AUTH_ISSUER, else scheme://host of NEON_AUTH_BASE_URL."""
    if settings.neon_auth_issuer:
        return settings.neon_auth_issuer
    parts = urlsplit(settings.neon_auth_base_url or "")
    return f"{parts.scheme}://{parts.netloc}"


def _deny(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def may_sign_up(settings: Settings, identity: Identity) -> str | None:
    """None if `identity` may create an account, else the refusal detail.

    Closed sign-ups are checked first, so a stranger is not sent through email
    verification only to be refused afterwards. An allowlisted email still has
    to be verified."""
    email = (identity.email or "").strip().lower()
    if not settings.signups_enabled and email not in settings.allowlist:
        return "signups_closed"
    if not identity.email_verified or not email:
        return "email_not_verified"
    return None


def install_exchange(
    app: FastAPI,
    settings: Settings,
    verifier: Verifier,
    directory: UserDirectory,
    *,
    limiter: RateLimiter | None = None,
) -> None:
    """Register POST /api/auth/exchange. Call after `install_auth` (which owns
    the request guards, including the Origin check that covers this POST)."""
    limiter = limiter or RateLimiter(EXCHANGE_MAX_FAILURES, EXCHANGE_WINDOW_SECONDS)
    app.state.exchange_limiter = track(app.state, limiter)

    # Sync handler: FastAPI runs it in the threadpool, so the verifier (JWKS
    # fetch) and the database calls never block the event loop.
    @app.post("/api/auth/exchange")
    def exchange(body: ExchangeBody, request: Request, response: Response):
        ip = limiter_key(client_ip(
            request, trusted_proxy=settings.trusted_proxy, hops=settings.trusted_proxy_hops
        ))
        # Every attempt reserves a slot; only a success gives it back.
        if not limiter.hit(ip):
            return _throttled(limiter.retry_after(ip))
        try:
            identity = verifier.verify(body.token)
        except TokenRejected:
            return _deny(401, "invalid_token")
        if identity.subject in settings.denylist:
            # Suspended: never created. An existing user's cookies die here
            # (cookies carry no subject, so they cannot be refused per request).
            existing = directory.find_by_subject(identity.subject)
            if existing is not None:
                directory.bump(existing)
            return _deny(403, "account_disabled")
        user_id = directory.find_by_subject(identity.subject)
        if user_id is None:
            refusal = may_sign_up(settings, identity)
            if refusal is not None:
                return _deny(403, refusal)
            user_id = directory.create(identity.subject)
        epoch = directory.get(user_id)
        if epoch is None:  # deleted between lookup and now
            return _deny(401, "invalid_token")
        limiter.release(ip)
        issue_cookie(response, request, settings, user_id, epoch)
        return {"ok": True}

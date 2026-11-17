"""Verify Neon Auth (Managed Better Auth) JWTs.

Tokens are EdDSA (Ed25519), short-lived, and carry no custom claims.

ASSUMPTION (unverified, no live token was available): the email is read from
claim ``email`` and verified status from ``email_verified`` or ``emailVerified``
(true only for the boolean ``True``). Confirm this claim shape against a real
token during the owner's live check. If no email claim exists, ``verify``
returns ``email=None, email_verified=False``.

Time checks (exp, nbf, iat age) use the injected ``clock`` so they are
testable; the token is never logged and failures carry a fixed message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import jwt

_REJECTED_MESSAGE = "token rejected"
_FUTURE_IAT_SKEW_SECONDS = 60


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str | None
    email_verified: bool


class TokenRejected(Exception):
    """Raised for any invalid token. Never carries token text."""

    def __init__(self) -> None:
        super().__init__(_REJECTED_MESSAGE)


class NeonTokenVerifier:
    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        jwks_client: Any = None,
        clock: Callable[[], float] = time.time,
        max_age_seconds: int = 300,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._jwks_client = jwks_client
        self._clock = clock
        self._max_age = max_age_seconds

    def _client(self) -> Any:
        # Lazy: constructing the verifier must never do network I/O.
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(
                self._jwks_url, cache_keys=True, lifespan=3600
            )
        return self._jwks_client

    def verify(self, token: str) -> Identity:
        try:
            return self._verify(token)
        except Exception:
            raise TokenRejected() from None

    def _verify(self, token: str) -> Identity:
        if not isinstance(token, str) or not token:
            raise ValueError
        key = self._client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["EdDSA"],
            options={
                "require": ["exp", "sub", "iat"],
                # Time checks are done below against the injected clock.
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "verify_iss": False,
                "verify_aud": False,
            },
        )
        now = self._clock()
        exp, iat = claims["exp"], claims["iat"]
        for v in (exp, iat):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError
        if exp <= now:
            raise ValueError
        nbf = claims.get("nbf")
        if nbf is not None and (
            isinstance(nbf, bool) or not isinstance(nbf, (int, float)) or nbf > now
        ):
            raise ValueError
        if iat < now - self._max_age or iat > now + _FUTURE_IAT_SKEW_SECONDS:
            raise ValueError
        if claims.get("iss") != self._issuer:
            raise ValueError
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise ValueError
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            return Identity(subject=subject, email=None, email_verified=False)
        verified = (
            claims.get("email_verified") is True
            or claims.get("emailVerified") is True
        )
        return Identity(subject=subject, email=email, email_verified=verified)

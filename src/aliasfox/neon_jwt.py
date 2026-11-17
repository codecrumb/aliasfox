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

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable

import jwt

log = logging.getLogger(__name__)

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


class _Invalid(Exception):
    """A token-shaped rejection from our own checks (not an infrastructure error)."""


def _time_claim(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Invalid
    return value


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
            # The JWK set is cached for an hour; individual keys are not cached
            # separately, so a key dropped from the set stops verifying with it.
            self._jwks_client = jwt.PyJWKClient(
                self._jwks_url, cache_keys=False, lifespan=3600, timeout=5
            )
        return self._jwks_client

    def verify(self, token: str) -> Identity:
        try:
            return self._verify(token)
        except (jwt.InvalidTokenError, _Invalid):
            raise TokenRejected() from None
        except Exception as exc:  # JWKS fetch failures, anything unexpected
            # Class name only: never the token, a claim or the exception text.
            log.warning("token verification infrastructure error: %s", type(exc).__name__)
            raise TokenRejected() from None

    def _verify(self, token: str) -> Identity:
        if not isinstance(token, str) or not token:
            raise _Invalid
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
        exp, iat = _time_claim(claims["exp"]), _time_claim(claims["iat"])
        if exp <= now:
            raise _Invalid
        nbf = claims.get("nbf")
        if nbf is not None and _time_claim(nbf) > now:
            raise _Invalid
        if iat < now - self._max_age or iat > now + _FUTURE_IAT_SKEW_SECONDS:
            raise _Invalid
        if claims.get("iss") != self._issuer:
            log.warning("token issuer mismatch")  # never the issuer value
            raise _Invalid
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise _Invalid
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            return Identity(subject=subject, email=None, email_verified=False)
        verified = (
            claims.get("email_verified") is True
            or claims.get("emailVerified") is True
        )
        return Identity(subject=subject, email=email, email_verified=verified)

"""Tests for the Neon Auth JWT verifier."""

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aliasfox.neon_jwt import Identity, NeonTokenVerifier, TokenRejected

ISSUER = "https://auth.example.neon.tech"
NOW = 1_800_000_000.0


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJwksClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return FakeSigningKey(self._public_key)


@pytest.fixture
def private_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def verifier(private_key):
    return NeonTokenVerifier(
        jwks_url="https://auth.example.neon.tech/.well-known/jwks.json",
        issuer=ISSUER,
        jwks_client=FakeJwksClient(private_key.public_key()),
        clock=lambda: NOW,
    )


def make_token(key, **overrides):
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "iat": int(NOW) - 10,
        "exp": int(NOW) + 600,
        "email": "a@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="EdDSA")


def test_valid_token(verifier, private_key):
    ident = verifier.verify(make_token(private_key))
    assert ident == Identity(
        subject="user-123", email="a@example.com", email_verified=True
    )


def test_camelcase_verified_claim(verifier, private_key):
    token = make_token(private_key, email_verified=None, emailVerified=True)
    assert verifier.verify(token).email_verified is True


def test_non_boolean_verified_is_false(verifier, private_key):
    token = make_token(private_key, email_verified="true")
    assert verifier.verify(token).email_verified is False


def test_no_email_claim(verifier, private_key):
    token = make_token(private_key, email=None, email_verified=None)
    assert verifier.verify(token) == Identity("user-123", None, False)


def test_expired(verifier, private_key):
    token = make_token(private_key, exp=int(NOW) - 1, iat=int(NOW) - 10)
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_wrong_issuer(verifier, private_key):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, iss="https://evil.example"))


def test_missing_sub(verifier, private_key):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, sub=None))


@pytest.mark.parametrize("sub", ["", 123, ["x"]])
def test_bad_sub_type_rejected(verifier, private_key, sub):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, sub=sub))


def test_iat_older_than_five_minutes(verifier, private_key):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, iat=int(NOW) - 301))


def test_iat_far_future_rejected(verifier, private_key):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, iat=int(NOW) + 120))


def test_alg_none_rejected(verifier):
    token = jwt.encode(
        {
            "sub": "user-123",
            "iss": ISSUER,
            "iat": int(NOW) - 10,
            "exp": int(NOW) + 600,
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_hs256_with_public_key_as_secret_rejected(private_key):
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    verifier = NeonTokenVerifier(
        jwks_url="https://x/jwks",
        issuer=ISSUER,
        jwks_client=FakeJwksClient(private_key.public_key()),
        clock=lambda: NOW,
    )
    token = jwt.encode(
        {
            "sub": "user-123",
            "iss": ISSUER,
            "iat": int(NOW) - 10,
            "exp": int(NOW) + 600,
        },
        pub_bytes,
        algorithm="HS256",
    )
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_wrong_signing_key(verifier):
    other = Ed25519PrivateKey.generate()
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(other))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "tökén.ünï.çødé", "‮"])
def test_garbage_and_non_ascii_token(verifier, token):
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_non_str_token(verifier):
    with pytest.raises(TokenRejected):
        verifier.verify(None)  # type: ignore[arg-type]


def test_error_text_never_contains_token(verifier, private_key):
    token = make_token(private_key, iss="https://evil.example")
    with pytest.raises(TokenRejected) as ei:
        verifier.verify(token)
    text = str(ei.value) + repr(ei.value)
    assert token not in text
    assert token.split(".")[1] not in text
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__ is True


def test_construction_does_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("PyJWKClient constructed eagerly")

    monkeypatch.setattr(jwt, "PyJWKClient", boom)
    NeonTokenVerifier(jwks_url="https://x/jwks", issuer=ISSUER)

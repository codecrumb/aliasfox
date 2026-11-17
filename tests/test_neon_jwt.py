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


# --- final review F4: claim shapes, other algorithms, infrastructure logging ----------

import base64  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def raw_token(key, payload_json: str) -> str:
    """An EdDSA token over a hand-written JSON payload (NaN/Infinity are not valid
    JSON, so jwt.encode cannot produce them, but Python's json.loads accepts them)."""
    signing_input = _b64(b'{"alg":"EdDSA","typ":"JWT"}') + "." + _b64(payload_json.encode())
    return signing_input + "." + _b64(key.sign(signing_input.encode()))


def base_claims(**overrides):
    claims = {"sub": "user-123", "iss": ISSUER, "iat": int(NOW) - 10, "exp": int(NOW) + 600}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


@pytest.mark.parametrize("missing", ["exp", "iat"])
def test_missing_exp_or_iat_rejected(verifier, private_key, missing):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, **{missing: None}))


def test_nbf_in_future_rejected(verifier, private_key):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, nbf=int(NOW) + 30))
    assert verifier.verify(make_token(private_key, nbf=int(NOW) - 30)).subject == "user-123"


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", ["1800000600", True, False])
def test_wrong_type_time_claims_rejected(verifier, private_key, claim, value):
    with pytest.raises(TokenRejected):
        verifier.verify(make_token(private_key, **{claim: value}))


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_time_claims_rejected(verifier, private_key, claim, value):
    payload = json.dumps(base_claims(**{claim: None})).rstrip("}") + f', "{claim}": {value}}}'
    # sanity: the hand-built token is otherwise valid
    assert verifier.verify(raw_token(private_key, json.dumps(base_claims()))).subject == "user-123"
    with pytest.raises(TokenRejected):
        verifier.verify(raw_token(private_key, payload))


@pytest.mark.parametrize("alg", ["ES256", "RS256"])
def test_non_eddsa_algorithms_rejected(alg):
    key = (
        ec.generate_private_key(ec.SECP256R1())
        if alg == "ES256"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    v = NeonTokenVerifier(
        jwks_url="https://x/jwks",
        issuer=ISSUER,
        jwks_client=FakeJwksClient(key.public_key()),
        clock=lambda: NOW,
    )
    token = jwt.encode(base_claims(), key, algorithm=alg)
    with pytest.raises(TokenRejected):
        v.verify(token)


class FailingJwksClient:
    def __init__(self, exc):
        self.exc = exc

    def get_signing_key_from_jwt(self, token):
        raise self.exc


@pytest.mark.parametrize(
    "exc, name",
    [
        (jwt.PyJWKClientConnectionError("Fail to fetch data from the url, err: secret-detail"),
         "PyJWKClientConnectionError"),
        (RuntimeError("unexpected secret-detail"), "RuntimeError"),
    ],
)
def test_jwks_infrastructure_error_rejected_and_logged_by_class_name(
    private_key, caplog, exc, name
):
    v = NeonTokenVerifier(
        jwks_url="https://x/jwks", issuer=ISSUER,
        jwks_client=FailingJwksClient(exc), clock=lambda: NOW,
    )
    token = make_token(private_key)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TokenRejected):
        v.verify(token)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert text == f"token verification infrastructure error: {name}"
    for record in caplog.records:
        logged = record.getMessage() + repr(record.args)
        assert token not in logged and token.split(".")[1] not in logged
        assert "secret-detail" not in logged


def test_issuer_mismatch_logs_fixed_warning_without_the_issuer(verifier, private_key, caplog):
    caplog.set_level(logging.DEBUG)
    token = make_token(private_key, iss="https://evil-issuer.example")
    with pytest.raises(TokenRejected):
        verifier.verify(token)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["token issuer mismatch"]
    joined = " ".join(messages) + " ".join(repr(r.args) for r in caplog.records)
    assert "evil-issuer" not in joined and ISSUER not in joined and token not in joined


def test_ordinary_invalid_tokens_log_nothing(verifier, private_key, caplog):
    caplog.set_level(logging.DEBUG)
    for token in ("garbage", make_token(Ed25519PrivateKey.generate()),
                  make_token(private_key, exp=int(NOW) - 1)):
        with pytest.raises(TokenRejected):
            verifier.verify(token)
    assert caplog.records == []


def test_real_jwks_client_does_not_cache_keys_and_has_short_timeout(monkeypatch):
    made = {}

    def fake(uri, **kw):
        made.update(kw, uri=uri)
        return FakeJwksClient(None)

    monkeypatch.setattr(jwt, "PyJWKClient", fake)
    NeonTokenVerifier(jwks_url="https://x/jwks", issuer=ISSUER)._client()
    assert made["uri"] == "https://x/jwks"
    assert made["cache_keys"] is False and made["timeout"] == 5 and made["lifespan"] == 3600

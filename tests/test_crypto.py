import pytest

from aliasfox.crypto import (
    DecryptionError,
    decrypt,
    derive_key,
    encrypt,
    hash_password,
    verify_password,
)


@pytest.fixture
def key():
    return derive_key("test-secret")


def test_roundtrip(key):
    assert decrypt(key, encrypt(key, b"hello")) == b"hello"


def test_roundtrip_with_aad(key):
    assert decrypt(key, encrypt(key, b"hi", aad=b"ctx"), aad=b"ctx") == b"hi"


def test_nonce_is_random(key):
    assert encrypt(key, b"same") != encrypt(key, b"same")


def test_wrong_key_fails(key):
    blob = encrypt(key, b"x")
    with pytest.raises(DecryptionError):
        decrypt(derive_key("other"), blob)


def test_tampered_blob_fails(key):
    blob = bytearray(encrypt(key, b"x"))
    blob[-1] ^= 1
    with pytest.raises(DecryptionError):
        decrypt(key, bytes(blob))


def test_short_blob_fails(key):
    with pytest.raises(DecryptionError):
        decrypt(key, b"short")


def test_wrong_aad_fails(key):
    blob = encrypt(key, b"x", aad=b"a")
    with pytest.raises(DecryptionError):
        decrypt(key, blob, aad=b"b")


def test_derive_key_is_deterministic_and_32_bytes():
    a = derive_key("s")
    assert a == derive_key("s")
    assert len(a) == 32
    assert a != derive_key("s", info=b"other")


def test_password_hash_verifies_and_is_argon2id():
    h = hash_password("pw")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "pw") is True


def test_wrong_password_false():
    assert verify_password(hash_password("pw"), "nope") is False


def test_malformed_hash_false():
    assert verify_password("garbage", "pw") is False

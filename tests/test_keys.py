import pytest

from aliasfox.crypto import DecryptionError, decrypt, derive_key
from aliasfox.keys import KeyRing

SECRET = "s" * 32


def test_roundtrip():
    ring = KeyRing(SECRET)
    data_key, wrapped = ring.new_wrapped(7)
    assert ring.unwrap(7, wrapped) == data_key
    assert data_key not in wrapped


def test_wrapped_key_bound_to_user():
    ring = KeyRing(SECRET)
    _, wrapped = ring.new_wrapped(1)
    with pytest.raises(DecryptionError):
        ring.unwrap(2, wrapped)


def test_wrong_master_secret_fails():
    _, wrapped = KeyRing(SECRET).new_wrapped(1)
    with pytest.raises(DecryptionError):
        KeyRing("t" * 32).unwrap(1, wrapped)


def test_data_keys_are_random_32_bytes():
    ring = KeyRing(SECRET)
    keys = {ring.new_wrapped(1)[0] for _ in range(20)}
    assert len(keys) == 20
    assert all(len(k) == 32 for k in keys)


def test_wrap_uses_documented_derivation_and_aad():
    data_key, wrapped = KeyRing(SECRET).new_wrapped(42)
    wrap_key = derive_key(SECRET, info=b"aliasfox-wrap-v1")
    assert decrypt(wrap_key, wrapped, aad=b"wrap|42") == data_key


def test_wrap_key_is_domain_separated_from_session_key():
    # The legacy session key (default info) must not unwrap data keys.
    _, wrapped = KeyRing(SECRET).new_wrapped(1)
    with pytest.raises(DecryptionError):
        decrypt(derive_key(SECRET), wrapped, aad=b"wrap|1")


def test_truncated_wrapped_key_fails():
    with pytest.raises(DecryptionError):
        KeyRing(SECRET).unwrap(1, b"short")

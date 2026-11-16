import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_LEN = 12
_hasher = PasswordHasher()


class DecryptionError(Exception):
    """Raised when a blob cannot be decrypted or authenticated."""


def derive_key(secret: str, *, info: bytes = b"aliasfox-session-v1") -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=info
    ).derive(secret.encode())


def encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, *, aad: bytes = b"") -> bytes:
    if len(blob) < _NONCE_LEN + 16:
        raise DecryptionError("invalid ciphertext")
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad)
    except InvalidTag:
        raise DecryptionError("decryption failed") from None


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(hash: str, pw: str) -> bool:
    try:
        return _hasher.verify(hash, pw)
    except (VerificationError, InvalidHashError):
        return False


def derive_cache_key(secret_key: str, user_id: int, epoch: int) -> bytes:
    """Browser-cache key: domain-separated from the session-encryption and
    cookie-signing keys, and rotated by the session epoch (so logout changes it)."""
    return derive_key(
        secret_key, info=b"aliasfox-cache-v1|" + str(user_id).encode() + b"|" + str(epoch).encode()
    )

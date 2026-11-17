"""Per-user data keys, wrapped under a key derived from ALIASFOX_SECRET_KEY.

Each user gets 32 random bytes as a data key. It is stored only wrapped:
AES-256-GCM under `derive_key(secret, info=b"aliasfox-wrap-v1")` with AAD
`b"wrap|<user_id>"`, so a wrapped key copied into another user's row fails to
unwrap. Never log or raise with key material.
"""
import os

from aliasfox.crypto import DecryptionError, decrypt, derive_key, encrypt

DATA_KEY_LEN = 32
WRAP_INFO = b"aliasfox-wrap-v1"


class KeyRing:
    def __init__(self, secret: str) -> None:
        self._wrap_key = derive_key(secret, info=WRAP_INFO)

    def __repr__(self) -> str:
        return "KeyRing(<redacted>)"

    @staticmethod
    def _aad(user_id: int) -> bytes:
        return b"wrap|" + str(user_id).encode()

    def new_wrapped(self, user_id: int) -> tuple[bytes, bytes]:
        data_key = os.urandom(DATA_KEY_LEN)
        return data_key, encrypt(self._wrap_key, data_key, aad=self._aad(user_id))

    def unwrap(self, user_id: int, wrapped: bytes) -> bytes:
        data_key = decrypt(self._wrap_key, wrapped, aad=self._aad(user_id))
        if len(data_key) != DATA_KEY_LEN:
            raise DecryptionError("invalid data key")
        return data_key

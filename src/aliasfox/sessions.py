import json
from dataclasses import asdict, dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from aliasfox.crypto import DecryptionError, decrypt, encrypt
from aliasfox.db import AppleSessionRow


class SessionUnreadable(Exception):
    """The stored session cannot be decrypted or parsed (rotated key, corruption).
    Fixed message: never carries blob or key material."""

    def __init__(self) -> None:
        super().__init__("stored session is unreadable; reconnect")


@dataclass
class AppleSessionState:
    cookies: dict[str, str] = field(repr=False)
    webservices_url: str
    dsid: str | None = None
    extra: dict = field(default_factory=dict, repr=False)


class SessionStore:
    def __init__(self, engine: Engine, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("key must be 32 bytes")
        self._key = key
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _aad(user_id: int) -> bytes:
        return str(user_id).encode()

    def save(self, user_id: int, state: AppleSessionState) -> None:
        plaintext = json.dumps(asdict(state)).encode()
        blob = encrypt(self._key, plaintext, aad=self._aad(user_id))
        with self._sessions.begin() as s:
            row = s.get(AppleSessionRow, user_id)
            if row is None:
                s.add(AppleSessionRow(user_id=user_id, blob=blob))
            else:
                row.blob = blob

    def load(self, user_id: int) -> AppleSessionState | None:
        with self._sessions() as s:
            row = s.get(AppleSessionRow, user_id)
            blob = row.blob if row else None
        if blob is None:
            return None
        try:
            data = json.loads(decrypt(self._key, blob, aad=self._aad(user_id)))
            return AppleSessionState(**data)
        except (DecryptionError, ValueError, TypeError, KeyError):
            raise SessionUnreadable() from None

    def delete(self, user_id: int) -> None:
        with self._sessions.begin() as s:
            row = s.get(AppleSessionRow, user_id)
            if row is not None:
                s.delete(row)

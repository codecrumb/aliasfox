import json
from dataclasses import asdict, dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from aliasfox.crypto import decrypt, encrypt
from aliasfox.db import AppleSessionRow


@dataclass
class AppleSessionState:
    cookies: dict[str, str] = field(repr=False)
    webservices_url: str
    dsid: str | None = None
    extra: dict = field(default_factory=dict)


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
        data = json.loads(decrypt(self._key, blob, aad=self._aad(user_id)))
        return AppleSessionState(**data)

    def delete(self, user_id: int) -> None:
        with self._sessions.begin() as s:
            row = s.get(AppleSessionRow, user_id)
            if row is not None:
                s.delete(row)

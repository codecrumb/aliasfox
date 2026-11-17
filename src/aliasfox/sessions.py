import json
from dataclasses import asdict, dataclass, field

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from aliasfox.crypto import DecryptionError, decrypt, encrypt
from aliasfox.db import AppleSessionRow, User
from aliasfox.keys import KeyRing


class SessionUnreadable(Exception):
    """The stored session cannot be decrypted or parsed (rotated key, corruption,
    missing user or wrapped key).
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
    """Apple session blobs, each encrypted under its user's own data key.

    The data key is unwrapped from `users.wrapped_key` on every operation and is
    never cached. The blob's AAD is the user id, so a blob copied into another
    user's row fails to decrypt even before the key mismatch.
    """

    def __init__(self, engine: Engine, keyring: KeyRing) -> None:
        self._keyring = keyring
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _aad(user_id: int) -> bytes:
        return str(user_id).encode()

    def _data_key(self, s: Session, user_id: int) -> bytes:
        """Missing user, missing wrapped key or unwrap failure: SessionUnreadable."""
        wrapped = s.scalar(select(User.wrapped_key).where(User.id == user_id))
        if wrapped is None:
            raise SessionUnreadable()
        try:
            return self._keyring.unwrap(user_id, wrapped)
        except DecryptionError:
            raise SessionUnreadable() from None

    def save(self, user_id: int, state: AppleSessionState) -> None:
        plaintext = json.dumps(asdict(state)).encode()
        with self._sessions.begin() as s:
            blob = encrypt(self._data_key(s, user_id), plaintext, aad=self._aad(user_id))
            row = s.get(AppleSessionRow, user_id)
            if row is None:
                s.add(AppleSessionRow(user_id=user_id, blob=blob))
            else:
                row.blob = blob

    def load(self, user_id: int) -> AppleSessionState | None:
        with self._sessions() as s:
            blob = s.scalar(select(AppleSessionRow.blob).where(AppleSessionRow.user_id == user_id))
            if blob is None:
                return None
            key = self._data_key(s, user_id)
        try:
            data = json.loads(decrypt(key, blob, aad=self._aad(user_id)))
            return AppleSessionState(**data)
        except (DecryptionError, ValueError, TypeError, KeyError):
            raise SessionUnreadable() from None

    def rekey(self, user_id: int) -> None:
        """Give an EXISTING user a new data key and delete their Apple session
        (which no key we hold can read any more), in one transaction. Raises
        SessionUnreadable, changing nothing, if the user does not exist."""
        with self._sessions.begin() as s:
            _, wrapped = self._keyring.new_wrapped(user_id)
            if not s.execute(
                update(User).where(User.id == user_id).values(wrapped_key=wrapped)
            ).rowcount:
                raise SessionUnreadable()
            s.execute(delete(AppleSessionRow).where(AppleSessionRow.user_id == user_id))

    def delete(self, user_id: int) -> None:
        with self._sessions.begin() as s:
            row = s.get(AppleSessionRow, user_id)
            if row is not None:
                s.delete(row)

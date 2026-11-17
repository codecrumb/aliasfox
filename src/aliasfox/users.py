"""The `users` table: identity (auth subject), wrapped data key, session epoch.

Also serves as the epochs store for auth (`get`/`bump`), replacing DbEpochs.
"""
import secrets

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from aliasfox.crypto import DecryptionError
from aliasfox.db import DEFAULT_USER_ID, AppleSessionRow, User
from aliasfox.keys import KeyRing

SINGLE_SUBJECT = "single"
# New users start at a random epoch in [1, 2**30], leaving headroom below the
# int32 column limit for bumps. On SQLite a deleted highest id can be reused;
# a random start keeps the deleted user's cookie {u: N, e: start} from
# matching the new user. Epochs are only compared for equality and bumped by 1.
_START_EPOCH_RANGE = 2**30


def _start_epoch() -> int:
    return secrets.randbelow(_START_EPOCH_RANGE) + 1


class UserDirectory:
    def __init__(self, engine: Engine, keyring: KeyRing) -> None:
        self._keyring = keyring
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    # --- epochs protocol (read on every authenticated request) -----------------

    def get(self, user_id: int) -> int | None:
        with self._sessions() as s:
            return s.scalar(select(User.session_epoch).where(User.id == user_id))

    def bump(self, user_id: int) -> int:
        with self._sessions.begin() as s:
            s.execute(
                update(User)
                .where(User.id == user_id)
                .values(session_epoch=User.session_epoch + 1)
            )
            return s.scalar(select(User.session_epoch).where(User.id == user_id))

    # --- identity ----------------------------------------------------------------

    def find_by_subject(self, subject: str) -> int | None:
        with self._sessions() as s:
            return s.scalar(select(User.id).where(User.auth_subject == subject))

    def create(self, subject: str) -> int:
        """Create the user for `subject` with a fresh wrapped data key, or return
        the existing id (including when a concurrent create wins the race)."""
        existing = self.find_by_subject(subject)
        if existing is not None:
            return existing
        try:
            with self._sessions.begin() as s:
                user = User(auth_subject=subject, session_epoch=_start_epoch())
                s.add(user)
                s.flush()  # assigns the id the wrapped key is bound to
                _, user.wrapped_key = self._keyring.new_wrapped(user.id)
            return user.id
        except IntegrityError:
            existing = self.find_by_subject(subject)
            if existing is None:
                raise
            return existing

    def _unwraps(self, user_id: int, wrapped: bytes) -> bool:
        try:
            self._keyring.unwrap(user_id, wrapped)
        except DecryptionError:
            return False
        return True

    def ensure_single_user(self) -> int:
        """Single mode: user DEFAULT_USER_ID with subject "single" and a wrapped key.

        If the stored wrapped key does not unwrap under the current secret (the
        secret was rotated), user 1 is re-keyed and its Apple session row, which
        no key we hold can read, is deleted in the same transaction. Single mode
        only: multi mode never calls this.
        """
        for _ in range(2):
            try:
                with self._sessions.begin() as s:
                    user = s.get(User, DEFAULT_USER_ID)
                    if user is None:
                        _, wrapped = self._keyring.new_wrapped(DEFAULT_USER_ID)
                        s.add(
                            User(
                                id=DEFAULT_USER_ID,
                                auth_subject=SINGLE_SUBJECT,
                                wrapped_key=wrapped,
                            )
                        )
                    elif user.auth_subject != SINGLE_SUBJECT:
                        raise RuntimeError("user 1 is not the single-mode user")
                    elif user.wrapped_key is None:
                        _, wrapped = self._keyring.new_wrapped(DEFAULT_USER_ID)
                        # Conditional: a concurrent starter may have keyed it already.
                        s.execute(
                            update(User)
                            .where(User.id == DEFAULT_USER_ID, User.wrapped_key.is_(None))
                            .values(wrapped_key=wrapped)
                        )
                    elif not self._unwraps(DEFAULT_USER_ID, user.wrapped_key):
                        _, wrapped = self._keyring.new_wrapped(DEFAULT_USER_ID)
                        rekeyed = s.execute(
                            update(User)
                            .where(
                                User.id == DEFAULT_USER_ID,
                                User.wrapped_key == user.wrapped_key,
                            )
                            .values(wrapped_key=wrapped)
                        )
                        if rekeyed.rowcount:
                            s.execute(
                                delete(AppleSessionRow).where(
                                    AppleSessionRow.user_id == DEFAULT_USER_ID
                                )
                            )
                return DEFAULT_USER_ID
            except IntegrityError:
                continue  # another process inserted the row; re-check it
        raise RuntimeError("could not ensure the single-mode user")

    def delete(self, user_id: int) -> None:
        """Remove the user's Apple session and user row (with its wrapped key).

        Callers must also drop all in-process per-user state for this id: the
        AliasService cache, generation and lock entries (`AliasService.disconnect`
        bumps the generation, deletes the stored session and clears the cache;
        `AliasService.forget` clears only the cache), the per-user rate-limit
        entries and any pending login flow. On SQLite a
        deleted highest id can be reused by the next `create`, and a new user
        must never inherit that state.
        """
        with self._sessions.begin() as s:
            s.execute(delete(AppleSessionRow).where(AppleSessionRow.user_id == user_id))
            s.execute(delete(User).where(User.id == user_id))

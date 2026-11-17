"""The `users` table: identity (auth subject), wrapped data key, session epoch.

Also serves as the epochs store for auth (`get`/`bump`), replacing DbEpochs.
"""
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from aliasfox.db import DEFAULT_USER_ID, AppleSessionRow, User
from aliasfox.keys import KeyRing

SINGLE_SUBJECT = "single"


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
                user = User(auth_subject=subject)
                s.add(user)
                s.flush()  # assigns the id the wrapped key is bound to
                _, user.wrapped_key = self._keyring.new_wrapped(user.id)
            return user.id
        except IntegrityError:
            existing = self.find_by_subject(subject)
            if existing is None:
                raise
            return existing

    def ensure_single_user(self) -> int:
        """Single mode: user DEFAULT_USER_ID with subject "single" and a wrapped key."""
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
                    elif user.wrapped_key is None:
                        _, wrapped = self._keyring.new_wrapped(DEFAULT_USER_ID)
                        # Conditional: a concurrent starter may have keyed it already.
                        s.execute(
                            update(User)
                            .where(User.id == DEFAULT_USER_ID, User.wrapped_key.is_(None))
                            .values(wrapped_key=wrapped)
                        )
                return DEFAULT_USER_ID
            except IntegrityError:
                continue  # another process inserted the row; re-check it
        raise RuntimeError("could not ensure the single-mode user")

    def delete(self, user_id: int) -> None:
        """Remove the user's Apple session and user row (with its wrapped key)."""
        with self._sessions.begin() as s:
            s.execute(delete(AppleSessionRow).where(AppleSessionRow.user_id == user_id))
            s.execute(delete(User).where(User.id == user_id))

"""Shared test helpers."""
from sqlalchemy import Engine

from aliasfox.keys import KeyRing
from aliasfox.sessions import SessionStore
from aliasfox.users import UserDirectory

TEST_SECRET = "k" * 32


def make_store(engine: Engine, secret: str = TEST_SECRET) -> tuple[SessionStore, UserDirectory]:
    """A SessionStore and UserDirectory over `engine` (already init_db'd), with the
    single-mode user (id 1) created and keyed, as create_app does in single mode."""
    keyring = KeyRing(secret)
    users = UserDirectory(engine, keyring)
    users.ensure_single_user()
    return SessionStore(engine, keyring), users

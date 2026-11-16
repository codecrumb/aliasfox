import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from aliasfox.crypto import DecryptionError
from aliasfox.db import AppleSessionRow, init_db
from aliasfox.sessions import AppleSessionState, SessionStore

KEY = b"k" * 32


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    init_db(eng)
    return eng


@pytest.fixture
def store(engine):
    return SessionStore(engine, KEY)


def _state():
    return AppleSessionState(
        cookies={"X-APPLE-WEBAUTH-TOKEN": "super-secret-cookie-value"},
        webservices_url="https://p01-maildomainws.icloud.com",
        dsid="12345",
        extra={"a": [1, 2], "b": {"c": None}},
    )


def test_save_load_roundtrip(store):
    store.save(1, _state())
    assert store.load(1) == _state()
    store.save(1, AppleSessionState({}, "u", None, {}))  # upsert
    assert store.load(1) == AppleSessionState({}, "u", None, {})


def test_blob_in_db_contains_no_cookie_value(store, engine):
    store.save(1, _state())
    with Session(engine) as s:
        blob = s.scalar(select(AppleSessionRow.blob).where(AppleSessionRow.user_id == 1))
    assert blob
    assert b"super-secret-cookie-value" not in blob
    assert b"icloud.com" not in blob


def test_blob_bound_to_user(store, engine):
    store.save(1, _state())
    with Session(engine) as s:
        blob = s.scalar(select(AppleSessionRow.blob).where(AppleSessionRow.user_id == 1))
        s.add(AppleSessionRow(user_id=2, blob=blob))
        s.commit()
    with pytest.raises(DecryptionError):
        store.load(2)


def test_load_missing_returns_none(store):
    assert store.load(1) is None


def test_delete(store):
    store.save(1, _state())
    store.delete(1)
    assert store.load(1) is None
    store.delete(1)  # idempotent


def test_bad_key_length_rejected(engine):
    with pytest.raises(ValueError):
        SessionStore(engine, b"short")


def test_repr_does_not_contain_cookie_values():
    state = AppleSessionState(cookies={"c": "SECRETVALUE"}, webservices_url="https://x")
    assert "SECRETVALUE" not in repr(state)

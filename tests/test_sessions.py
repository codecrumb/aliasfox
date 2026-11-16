import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from aliasfox.crypto import DecryptionError
from aliasfox.db import AppleSessionRow, init_db
from aliasfox.sessions import AppleSessionState, SessionStore, SessionUnreadable

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


def test_extra_not_in_repr():
    state = AppleSessionState(
        cookies={}, webservices_url="https://x", extra={"trust_token": "TRUSTTOKENVALUE"}
    )
    assert "TRUSTTOKENVALUE" not in repr(state)
    assert "TRUSTTOKENVALUE" not in str(state)


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
    with pytest.raises(SessionUnreadable):
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


def _put_blob(engine, blob, user_id=1):
    with Session(engine) as s, s.begin():
        row = s.get(AppleSessionRow, user_id)
        if row is None:
            s.add(AppleSessionRow(user_id=user_id, blob=blob))
        else:
            row.blob = blob


def test_rotated_secret_key_is_session_unreadable(store, engine):
    store.save(1, _state())
    other = SessionStore(engine, b"z" * 32)
    with pytest.raises(SessionUnreadable) as ei:
        other.load(1)
    assert "reconnect" in str(ei.value)
    assert "super-secret" not in str(ei.value)


@pytest.mark.parametrize(
    "plaintext",
    [b"not json", b"[1, 2]", b'{"webservices_url": "x"}', b'{"cookies": {}, "webservices_url": "x", "bogus": 1}', b"\xff\xfe"],
)
def test_corrupt_plaintext_is_session_unreadable(store, engine, plaintext):
    from aliasfox.crypto import encrypt

    _put_blob(engine, encrypt(KEY, plaintext, aad=b"1"))
    with pytest.raises(SessionUnreadable):
        store.load(1)


def test_truncated_blob_is_session_unreadable(store, engine):
    _put_blob(engine, b"short")
    with pytest.raises(SessionUnreadable):
        store.load(1)

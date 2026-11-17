import pytest
from helpers import make_store
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from aliasfox.crypto import encrypt
from aliasfox.db import AppleSessionRow, User, init_db
from aliasfox.keys import KeyRing
from aliasfox.sessions import AppleSessionState, SessionStore, SessionUnreadable

SECRET = "k" * 32


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
def pair(engine):
    return make_store(engine, SECRET)


@pytest.fixture
def store(pair):
    return pair[0]


@pytest.fixture
def users(pair):
    return pair[1]


def _state(cookie="super-secret-cookie-value"):
    return AppleSessionState(
        cookies={"X-APPLE-WEBAUTH-TOKEN": cookie},
        webservices_url="https://p01-maildomainws.icloud.com",
        dsid="12345",
        extra={"a": [1, 2], "b": {"c": None}},
    )


def _blob(engine, user_id):
    with Session(engine) as s:
        return s.scalar(select(AppleSessionRow.blob).where(AppleSessionRow.user_id == user_id))


def _wrapped(engine, user_id):
    with Session(engine) as s:
        return s.scalar(select(User.wrapped_key).where(User.id == user_id))


def _put_blob(engine, blob, user_id=1):
    with Session(engine) as s, s.begin():
        row = s.get(AppleSessionRow, user_id)
        if row is None:
            s.add(AppleSessionRow(user_id=user_id, blob=blob))
        else:
            row.blob = blob


def _set_wrapped(engine, user_id, wrapped):
    with Session(engine) as s, s.begin():
        s.execute(update(User).where(User.id == user_id).values(wrapped_key=wrapped))


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


def test_save_load_roundtrip_per_user(store, users):
    a = users.create("sub-a")
    b = users.create("sub-b")
    store.save(a, _state("cookie-a"))
    store.save(b, _state("cookie-b"))
    assert store.load(a) == _state("cookie-a")
    assert store.load(b) == _state("cookie-b")
    assert store.load(1) is None


def test_blob_in_db_contains_no_cookie_value(store, engine):
    store.save(1, _state())
    blob = _blob(engine, 1)
    assert blob
    assert b"super-secret-cookie-value" not in blob
    assert b"icloud.com" not in blob


def test_blob_swapped_between_users_is_unreadable(store, users, engine):
    a = users.create("sub-a")
    b = users.create("sub-b")
    store.save(a, _state("cookie-a"))
    store.save(b, _state("cookie-b"))
    _put_blob(engine, _blob(engine, a), user_id=b)
    with pytest.raises(SessionUnreadable):
        store.load(b)
    assert store.load(a) == _state("cookie-a")


def test_blob_copied_to_user_without_session_is_unreadable(store, users, engine):
    a = users.create("sub-a")
    b = users.create("sub-b")
    store.save(a, _state())
    _put_blob(engine, _blob(engine, a), user_id=b)
    with pytest.raises(SessionUnreadable):
        store.load(b)


def test_wrapped_key_swapped_between_users_is_unreadable(store, users, engine):
    a = users.create("sub-a")
    b = users.create("sub-b")
    store.save(a, _state("cookie-a"))
    # B's row now holds A's wrapped key and A's blob: AAD binds both to A.
    _set_wrapped(engine, b, _wrapped(engine, a))
    _put_blob(engine, _blob(engine, a), user_id=b)
    with pytest.raises(SessionUnreadable):
        store.load(b)
    with pytest.raises(SessionUnreadable):
        store.save(b, _state("cookie-b"))
    assert store.load(a) == _state("cookie-a")


def test_save_for_unknown_user_raises(store, engine):
    with pytest.raises(SessionUnreadable):
        store.save(999, _state())
    with Session(engine) as s:
        assert s.get(User, 999) is None
        assert s.get(AppleSessionRow, 999) is None


def test_missing_wrapped_key_is_unreadable(store, users, engine):
    a = users.create("sub-a")
    store.save(a, _state())
    _set_wrapped(engine, a, None)
    with pytest.raises(SessionUnreadable):
        store.load(a)
    with pytest.raises(SessionUnreadable):
        store.save(a, _state())


def test_blob_for_deleted_user_is_unreadable(store, users, engine):
    a = users.create("sub-a")
    store.save(a, _state())
    blob = _blob(engine, a)
    users.delete(a)
    assert store.load(a) is None  # nothing stored
    _put_blob(engine, blob, user_id=a)  # orphan blob: no user, no key
    with pytest.raises(SessionUnreadable):
        store.load(a)


def test_error_message_has_no_key_material(store, users, engine):
    a = users.create("sub-a")
    store.save(a, _state())
    wrapped = _wrapped(engine, a)
    blob = _blob(engine, a)
    data_key = KeyRing(SECRET).unwrap(a, wrapped)
    b = users.create("sub-b")
    _set_wrapped(engine, b, wrapped)
    _put_blob(engine, blob, user_id=b)
    for call in (lambda: store.load(b), lambda: store.save(b, _state()), lambda: store.save(999, _state())):
        with pytest.raises(SessionUnreadable) as ei:
            call()
        msg = str(ei.value)
        assert msg == "stored session is unreadable; reconnect"
        # no chained DecryptionError either way
        assert ei.value.__cause__ is None
        assert ei.value.__context__ is None or ei.value.__suppress_context__
        for secret in (wrapped, blob, data_key):
            assert secret.hex() not in msg and repr(secret) not in msg
        assert SECRET not in msg


def test_load_missing_returns_none(store):
    assert store.load(1) is None


def test_delete(store):
    store.save(1, _state())
    store.delete(1)
    assert store.load(1) is None
    store.delete(1)  # idempotent


def test_store_holds_no_cached_data_key(store, engine):
    store.save(1, _state())
    # Rotating user 1's wrapped key out from under the store takes effect at once.
    _set_wrapped(engine, 1, KeyRing(SECRET).new_wrapped(1)[1])
    with pytest.raises(SessionUnreadable):
        store.load(1)


def test_repr_does_not_contain_cookie_values():
    state = AppleSessionState(cookies={"c": "SECRETVALUE"}, webservices_url="https://x")
    assert "SECRETVALUE" not in repr(state)


def test_rotated_secret_key_is_session_unreadable(store, engine):
    store.save(1, _state())
    other = SessionStore(engine, KeyRing("z" * 32))
    with pytest.raises(SessionUnreadable) as ei:
        other.load(1)
    assert "reconnect" in str(ei.value)
    assert "super-secret" not in str(ei.value)


@pytest.mark.parametrize(
    "plaintext",
    [b"not json", b"[1, 2]", b'{"webservices_url": "x"}', b'{"cookies": {}, "webservices_url": "x", "bogus": 1}', b"\xff\xfe"],
)
def test_corrupt_plaintext_is_session_unreadable(store, engine, plaintext):
    data_key = KeyRing(SECRET).unwrap(1, _wrapped(engine, 1))
    _put_blob(engine, encrypt(data_key, plaintext, aad=b"1"))
    with pytest.raises(SessionUnreadable):
        store.load(1)


def test_truncated_blob_is_session_unreadable(store, engine):
    _put_blob(engine, b"short")
    with pytest.raises(SessionUnreadable):
        store.load(1)


def test_blob_aad_alone_binds_blob_to_user(store, users, engine):
    a = users.create("sub-a")
    b = users.create("sub-b")
    # B's own data key, but the AAD of A: only the blob-layer AAD can reject it.
    b_key = KeyRing(SECRET).unwrap(b, _wrapped(engine, b))
    plaintext = b'{"cookies": {}, "webservices_url": "x"}'
    _put_blob(engine, encrypt(b_key, plaintext, aad=str(a).encode()), user_id=b)
    with pytest.raises(SessionUnreadable):
        store.load(b)
    # control: the same plaintext with B's own AAD loads
    _put_blob(engine, encrypt(b_key, plaintext, aad=str(b).encode()), user_id=b)
    assert store.load(b) == AppleSessionState({}, "x")

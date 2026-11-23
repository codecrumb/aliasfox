from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from aliasfox.apikeys import MAX_KEYS_PER_USER, ApiKeyStore, TooManyKeys
from aliasfox.config import Settings
from aliasfox.db import init_db, make_engine
from aliasfox.keys import KeyRing
from aliasfox.users import UserDirectory

SECRET = "s" * 32


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
    init_db(eng)
    return eng


@pytest.fixture
def store(engine):
    return ApiKeyStore(engine, SECRET)


@pytest.fixture
def uid(engine):
    return UserDirectory(engine, KeyRing(SECRET)).create("sub-a")


def test_key_format(store, uid):
    key, info = store.create(uid, "k", ["read"])
    assert key.startswith("afk_") and len(key) == 47
    assert key[4:12] == info.prefix
    assert info.scopes == frozenset({"read"})


def test_hash_is_keyed_and_plaintext_absent(store, engine, uid):
    key, _ = store.create(uid, "k", ["read"])
    assert store.lookup(key) is not None
    assert ApiKeyStore(engine, "o" * 32).lookup(key) is None
    with engine.connect() as c:
        for row in c.execute(text("select * from api_keys")):
            for v in row:
                assert key not in (v.hex() if isinstance(v, bytes) else str(v))


@pytest.mark.parametrize("scopes,name", [([], "k"), (["bogus"], "k"), (["read"], "  ")])
def test_scopes_validated(store, uid, scopes, name):
    with pytest.raises(ValueError):
        store.create(uid, name, scopes)


def test_lookup_unknown_and_expired_and_revoked_are_none(store, uid):
    assert store.lookup("afk_" + "x" * 43) is None
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired, _ = store.create(uid, "e", ["read"], expires_at=past)
    assert store.lookup(expired) is None
    live, info = store.create(uid, "l", ["read"], expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    assert store.lookup(live).id == info.id
    assert store.revoke(uid, info.id) is True
    assert store.lookup(live) is None


def test_revoke_other_users_key_is_false(store, engine, uid):
    other = UserDirectory(engine, KeyRing(SECRET)).create("sub-b")
    key, info = store.create(uid, "k", ["read"])
    assert store.revoke(other, info.id) is False
    assert store.lookup(key) is not None


def test_twenty_one_keys_raises_too_many_keys(store, uid):
    for i in range(MAX_KEYS_PER_USER):
        store.create(uid, f"k{i}", ["read"])
    with pytest.raises(TooManyKeys):
        store.create(uid, "one-too-many", ["read"])
    assert len(store.list_for_user(uid)) == MAX_KEYS_PER_USER


def test_touch_sets_last_used(store, uid):
    key, info = store.create(uid, "k", ["read"])
    assert info.last_used_at is None
    store.touch(info.id)
    assert store.lookup(key).last_used_at is not None


def test_delete_for_user(store, engine, uid):
    other = UserDirectory(engine, KeyRing(SECRET)).create("sub-b")
    store.create(uid, "a", ["read"])
    store.create(uid, "b", ["read"])
    keep, _ = store.create(other, "c", ["read"])
    assert store.delete_for_user(uid) == 2
    assert store.list_for_user(uid) == []
    assert store.lookup(keep) is not None


def test_delete_for_subjects(store, engine, uid):
    other = UserDirectory(engine, KeyRing(SECRET)).create("sub-b")
    store.create(uid, "a", ["read"])
    keep, _ = store.create(other, "c", ["read"])
    assert store.delete_for_subjects(frozenset()) == 0
    assert store.delete_for_subjects(frozenset({"sub-a", "nobody"})) == 1
    assert store.list_for_user(uid) == []
    assert store.lookup(keep) is not None


def test_unknown_scope_in_row_is_dropped_on_lookup(store, engine, uid):
    key, info = store.create(uid, "k", ["read", "create"])
    with engine.begin() as c:
        c.execute(text("update api_keys set scopes='read,bogus' where id=:i"), {"i": info.id})
    assert store.lookup(key).scopes == frozenset({"read"})


def test_lookup_none_when_stored_scopes_all_unknown(store, engine, uid):
    key, info = store.create(uid, "k", ["read"])
    with engine.begin() as c:
        c.execute(text("update api_keys set scopes='bogus' where id=:i"), {"i": info.id})
    assert store.lookup(key) is None


@pytest.mark.parametrize("bad", [None, 123, b"afk_x", "afk_\ud800"])
def test_lookup_unhashable_input_is_none(store, bad):
    assert store.lookup(bad) is None


def test_digest_is_public_and_matches_stored_hash(store, engine, uid):
    key, _ = store.create(uid, "k", ["read"])
    with engine.connect() as c:
        stored = c.execute(text("select key_hash from api_keys")).scalar_one()
    assert store.digest(key) == stored
    with pytest.raises(UnicodeEncodeError):
        store.digest("afk_\ud800")

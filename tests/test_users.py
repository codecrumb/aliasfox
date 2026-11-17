import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aliasfox.config import Settings
from aliasfox.db import DEFAULT_USER_ID, AppleSessionRow, User, init_db, make_engine
from aliasfox.keys import KeyRing
from aliasfox.users import UserDirectory

SECRET = "s" * 32


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(Settings(secret_key=SECRET, data_dir=tmp_path))
    init_db(eng)
    return eng


@pytest.fixture
def ring():
    return KeyRing(SECRET)


@pytest.fixture
def users(engine, ring):
    return UserDirectory(engine, ring)


def _count(engine, sql):
    with engine.connect() as c:
        return c.execute(text(sql)).scalar()


def _wrapped(engine, user_id):
    with Session(engine) as s:
        return s.scalar(select(User.wrapped_key).where(User.id == user_id))


def test_init_db_does_not_create_default_user(engine):
    assert _count(engine, "select count(*) from users") == 0


def test_create_returns_distinct_ids(users, engine, ring):
    a = users.create("sub-a")
    b = users.create("sub-b")
    assert a != b
    assert users.find_by_subject("sub-a") == a
    assert users.find_by_subject("sub-b") == b
    # each row carries its own wrapped key, bound to its own id
    wa, wb = _wrapped(engine, a), _wrapped(engine, b)
    assert wa and wb and wa != wb
    assert ring.unwrap(a, wa) != ring.unwrap(b, wb)


def test_create_same_subject_twice_returns_same_id(users, engine):
    first = users.create("sub")
    assert users.create("sub") == first
    assert _count(engine, "select count(*) from users") == 1


def test_concurrent_create_one_row(users, engine, ring):
    barrier = threading.Barrier(8)
    results, errors = [], []

    def worker():
        try:
            barrier.wait()
            results.append(users.create("racer"))
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(results)) == 1
    assert _count(engine, "select count(*) from users") == 1
    uid = results[0]
    assert ring.unwrap(uid, _wrapped(engine, uid))


def test_find_by_subject_unknown_is_none(users):
    assert users.find_by_subject("nobody") is None


def test_bump_only_affects_that_user(users):
    a = users.create("a")
    b = users.create("b")
    assert users.get(a) == 0
    assert users.bump(a) == 1
    assert users.bump(a) == 2
    assert users.get(a) == 2
    assert users.get(b) == 0


def test_get_unknown_user_is_none(users):
    assert users.get(999) is None


def test_delete_removes_session_and_user_and_wrapped_key(users, engine):
    a = users.create("a")
    b = users.create("b")
    with Session(engine) as s, s.begin():
        s.add(AppleSessionRow(user_id=a, blob=b"x" * 40))
        s.add(AppleSessionRow(user_id=b, blob=b"y" * 40))
    users.delete(a)
    assert users.get(a) is None
    assert users.find_by_subject("a") is None
    assert _count(engine, f"select count(*) from users where id={a}") == 0
    assert _count(engine, f"select count(*) from apple_sessions where user_id={a}") == 0
    # the other user is untouched
    assert users.get(b) == 0
    assert _count(engine, f"select count(*) from apple_sessions where user_id={b}") == 1
    users.delete(a)  # absent: no error
    users.delete(12345)


def test_ensure_single_user_idempotent(users, engine, ring):
    assert users.ensure_single_user() == DEFAULT_USER_ID
    wrapped = _wrapped(engine, DEFAULT_USER_ID)
    assert ring.unwrap(DEFAULT_USER_ID, wrapped)
    assert users.ensure_single_user() == DEFAULT_USER_ID
    assert _wrapped(engine, DEFAULT_USER_ID) == wrapped  # not re-keyed
    assert users.find_by_subject("single") == DEFAULT_USER_ID
    assert _count(engine, "select count(*) from users") == 1


def test_ensure_single_user_adds_key_to_legacy_row(engine, ring):
    # A pre-multi-user database: user 1 exists (migrated) with no wrapped key.
    with Session(engine) as s, s.begin():
        s.add(User(id=DEFAULT_USER_ID, auth_subject="single", session_epoch=3))
    users = UserDirectory(engine, ring)
    assert users.ensure_single_user() == DEFAULT_USER_ID
    assert ring.unwrap(DEFAULT_USER_ID, _wrapped(engine, DEFAULT_USER_ID))
    assert users.get(DEFAULT_USER_ID) == 3  # epoch preserved


def test_repr_has_no_key_material(users):
    assert "KeyRing(<redacted>)" in repr(users._keyring)


def test_create_app_single_mode_ensures_keyed_user(tmp_path):
    from aliasfox.main import create_app

    settings = Settings(secret_key=SECRET, data_dir=tmp_path)
    create_app(settings)
    eng = make_engine(settings)
    assert _count(eng, "select count(*) from users") == 1
    assert KeyRing(SECRET).unwrap(DEFAULT_USER_ID, _wrapped(eng, DEFAULT_USER_ID))


def test_create_app_multi_mode_creates_no_user(tmp_path, monkeypatch):
    from aliasfox.main import create_app

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    settings = Settings(
        secret_key=SECRET,
        aliasfox_mode="multi",
        database_url=f"sqlite:///{tmp_path}/multi.db",
        neon_auth_base_url="https://auth.example",
        neon_auth_jwks_url="https://auth.example/jwks",
        trusted_proxy=True,
    )
    create_app(settings)
    assert _count(make_engine(settings), "select count(*) from users") == 0

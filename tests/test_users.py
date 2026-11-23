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
    ea, eb = users.get(a), users.get(b)
    assert users.bump(a) == ea + 1
    assert users.bump(a) == ea + 2
    assert users.get(a) == ea + 2
    assert users.get(b) == eb


def test_new_users_start_at_random_epoch(users):
    # A random start keeps a deleted user's cookie {u: N, e: start} from
    # matching a new user that reuses id N (SQLite has no AUTOINCREMENT here).
    epochs = [users.get(users.create(f"s{i}")) for i in range(2)]
    assert epochs != [0, 0]
    assert epochs[0] != epochs[1]
    for e in epochs:
        assert isinstance(e, int) and 0 < e < 2**31 - 1


def test_get_unknown_user_is_none(users):
    assert users.get(999) is None


def test_delete_removes_session_and_user_and_wrapped_key(users, engine):
    a = users.create("a")
    b = users.create("b")
    with Session(engine) as s, s.begin():
        s.add(AppleSessionRow(user_id=a, blob=b"x" * 40))
        s.add(AppleSessionRow(user_id=b, blob=b"y" * 40))
    eb = users.get(b)
    users.delete(a)
    assert users.get(a) is None
    assert users.find_by_subject("a") is None
    assert _count(engine, f"select count(*) from users where id={a}") == 0
    assert _count(engine, f"select count(*) from apple_sessions where user_id={a}") == 0
    # the other user is untouched
    assert users.get(b) == eb
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
    from helpers import multi_mode_settings

    settings = multi_mode_settings(tmp_path, secret_key=SECRET)
    create_app(settings)
    assert _count(make_engine(settings), "select count(*) from users") == 0


def test_ensure_single_user_rekeys_after_secret_rotation(engine):
    from aliasfox.sessions import AppleSessionState, SessionStore

    old = KeyRing(SECRET)
    UserDirectory(engine, old).ensure_single_user()
    SessionStore(engine, old).save(DEFAULT_USER_ID, AppleSessionState({"c": "v"}, "https://x"))
    new = KeyRing("r" * 32)
    users = UserDirectory(engine, new)
    assert users.ensure_single_user() == DEFAULT_USER_ID
    assert new.unwrap(DEFAULT_USER_ID, _wrapped(engine, DEFAULT_USER_ID))
    store = SessionStore(engine, new)
    assert store.load(DEFAULT_USER_ID) is None  # unreadable blob was dropped
    store.save(DEFAULT_USER_ID, AppleSessionState({"c": "w"}, "https://y"))
    assert store.load(DEFAULT_USER_ID) == AppleSessionState({"c": "w"}, "https://y")


def test_ensure_single_user_same_secret_keeps_key_and_session(engine, ring):
    from aliasfox.sessions import AppleSessionState, SessionStore

    users = UserDirectory(engine, ring)
    users.ensure_single_user()
    store = SessionStore(engine, ring)
    state = AppleSessionState({"c": "v"}, "https://x")
    store.save(DEFAULT_USER_ID, state)
    wrapped = _wrapped(engine, DEFAULT_USER_ID)
    with Session(engine) as s:
        blob = s.get(AppleSessionRow, DEFAULT_USER_ID).blob
    users.ensure_single_user()
    assert _wrapped(engine, DEFAULT_USER_ID) == wrapped
    with Session(engine) as s:
        assert s.get(AppleSessionRow, DEFAULT_USER_ID).blob == blob
    assert store.load(DEFAULT_USER_ID) == state


def test_ensure_single_user_rejects_foreign_row_1(engine, ring):
    with Session(engine) as s, s.begin():
        s.add(User(id=DEFAULT_USER_ID, auth_subject="someone-else"))
    with pytest.raises(RuntimeError) as ei:
        UserDirectory(engine, ring).ensure_single_user()
    assert "someone-else" not in str(ei.value)
    assert _wrapped(engine, DEFAULT_USER_ID) is None


# --- single -> multi switch (final review F8) ---------------------------------------------


def test_remove_single_user_removes_row_and_session(users, engine, ring):
    from aliasfox.sessions import AppleSessionState, SessionStore

    users.ensure_single_user()
    other = users.create("sub-other")
    store = SessionStore(engine, ring)
    for uid in (DEFAULT_USER_ID, other):
        store.save(uid, AppleSessionState({"c": "v"}, "https://ws.example"))
    assert users.remove_single_user() is True
    assert users.find_by_subject("single") is None
    assert _count(engine, f"select count(*) from apple_sessions where user_id={DEFAULT_USER_ID}") == 0
    assert store.load(other).cookies == {"c": "v"}  # nobody else touched
    assert users.remove_single_user() is False  # nothing left to remove


def _single_db_with_session(tmp_path):
    """A single-mode deployment over the SQLite file multi mode will use, with a
    saved Apple session; returns the owner's single-mode cookie value."""
    from helpers import MULTI_SECRET, multi_mode_settings, session_cookie

    from aliasfox.main import create_app
    from aliasfox.sessions import AppleSessionState, SessionStore

    url = multi_mode_settings(tmp_path).database_url
    single = Settings(secret_key=MULTI_SECRET, database_url=url, data_dir=tmp_path)
    app = create_app(single)
    eng = make_engine(single)
    SessionStore(eng, KeyRing(MULTI_SECRET)).save(
        DEFAULT_USER_ID, AppleSessionState({"acct": "owner"}, "https://ws.example")
    )
    return eng, session_cookie(MULTI_SECRET, DEFAULT_USER_ID, app.state.epochs.get(DEFAULT_USER_ID))


def test_multi_startup_removes_single_mode_account(tmp_path, monkeypatch, caplog):
    from helpers import MultiUserEnv

    from aliasfox.auth import COOKIE_NAME

    eng, old_cookie = _single_db_with_session(tmp_path)
    assert _count(eng, "select count(*) from apple_sessions") == 1
    with caplog.at_level("INFO", logger="aliasfox.main"):
        env = MultiUserEnv(tmp_path, monkeypatch, ["a"])
    assert _count(eng, "select count(*) from users where auth_subject='single'") == 0
    assert _count(eng, "select count(*) from apple_sessions") == 0
    assert [r.getMessage() for r in caplog.records if r.name == "aliasfox.main"] == [
        "removed the single-mode account"
    ]
    stale = env.anonymous()
    stale.cookies.set(COOKIE_NAME, old_cookie)
    assert stale.get("/api/status").status_code == 401
    c, uid = env.sign_in("a")  # the first multi user is created and its cookie works
    assert c.get("/api/status").status_code == 200
    stale.cookies.set(COOKIE_NAME, old_cookie)
    assert stale.get("/api/status").status_code == 401  # still dead, even if id 1 was reused


def test_multi_startup_leaves_multi_users_alone(tmp_path, monkeypatch):
    from helpers import MultiUserEnv

    env = MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])
    ca, a = env.sign_in("a")
    env.connect(ca, "acct-a")
    before = env.raw("select id, auth_subject, wrapped_key, session_epoch from users").all()
    blob = env.blob(a)
    again = MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])
    assert again.raw("select id, auth_subject, wrapped_key, session_epoch from users").all() == before
    assert again.blob(a) == blob
    assert ca.get("/api/status").status_code == 200


def test_single_mode_never_removes_the_single_user(tmp_path, monkeypatch):
    from aliasfox.main import create_app

    def boom(self):
        raise AssertionError("remove_single_user called in single mode")

    monkeypatch.setattr(UserDirectory, "remove_single_user", boom, raising=False)
    from helpers import MULTI_SECRET

    eng, _ = _single_db_with_session(tmp_path)
    create_app(Settings(secret_key=MULTI_SECRET, database_url=str(eng.url), data_dir=tmp_path))
    assert _count(eng, "select count(*) from users where auth_subject='single'") == 1


def test_revoke_subjects_bumps_only_listed_existing_users(users):
    a, b = users.create("a"), users.create("b")
    ea, eb = users.get(a), users.get(b)
    assert users.revoke_subjects(frozenset({"a", "nobody"})) == 1
    assert users.get(a) == ea + 1 and users.get(b) == eb
    assert users.revoke_subjects(frozenset()) == 0


def test_delete_user_removes_keys(users, engine):
    from aliasfox.apikeys import ApiKeyStore

    uid = users.create("sub-k")
    other = users.create("sub-o")
    store = ApiKeyStore(engine, SECRET)
    store.create(uid, "a", ["read"])
    keep, _ = store.create(other, "b", ["read"])
    users.delete(uid)
    assert store.list_for_user(uid) == []
    assert store.lookup(keep) is not None

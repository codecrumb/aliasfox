import sqlite3

from sqlalchemy import create_engine, inspect, text

from aliasfox.config import Settings
from aliasfox.db import DEFAULT_USER_ID, init_db, make_engine


def _engine(tmp_path):
    return make_engine(Settings(secret_key="k" * 32, data_dir=tmp_path))


def _cols(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_fresh_db_reaches_head(tmp_path):
    engine = _engine(tmp_path)
    init_db(engine)
    assert _cols(engine, "users") >= {
        "id", "auth_subject", "wrapped_key", "created_at", "session_epoch",
    }
    assert "email" not in _cols(engine, "users")
    assert "apple_sessions" in inspect(engine).get_table_names()
    with engine.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0002"
        row = c.execute(
            text("select auth_subject, wrapped_key from users where id=:i"),
            {"i": DEFAULT_USER_ID},
        ).one()
    assert tuple(row) == ("single", None)


def test_old_schema_is_upgraded(tmp_path):
    con = sqlite3.connect(tmp_path / "aliasfox.db")
    con.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, created_at DATETIME, "
        "session_epoch INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute(
        "CREATE TABLE apple_sessions (user_id INTEGER PRIMARY KEY, blob BLOB, updated_at DATETIME)"
    )
    con.execute("INSERT INTO users (id, email, session_epoch) VALUES (1, 'a@b.c', 4)")
    con.execute("INSERT INTO apple_sessions (user_id, blob) VALUES (1, x'00')")
    con.commit()
    con.close()
    engine = _engine(tmp_path)
    init_db(engine)
    cols = _cols(engine, "users")
    assert {"auth_subject", "wrapped_key"} <= cols
    assert "email" not in cols
    with engine.connect() as c:
        assert tuple(
            c.execute(text("select auth_subject, session_epoch from users where id=1")).one()
        ) == ("single", 4)
        assert c.execute(text("select count(*) from apple_sessions")).scalar() == 1


def test_init_db_idempotent(tmp_path):
    engine = _engine(tmp_path)
    init_db(engine)
    init_db(engine)
    with engine.connect() as c:
        assert c.execute(text("select count(*) from users")).scalar() == 1


def test_in_memory_engine_works():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(engine)
    assert "auth_subject" in _cols(engine, "users")

import sqlite3

from sqlalchemy import create_engine, inspect, text

from aliasfox.config import Settings
from aliasfox.db import DEFAULT_USER_ID, init_db, make_engine
from aliasfox.keys import KeyRing
from aliasfox.users import UserDirectory


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
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0003"
        # init_db seeds no rows: single mode creates its user via
        # UserDirectory.ensure_single_user(); multi mode has no default user.
        assert c.execute(text("select count(*) from users")).scalar() == 0


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
    UserDirectory(engine, KeyRing("k" * 32)).ensure_single_user()
    init_db(engine)
    with engine.connect() as c:
        assert c.execute(text("select count(*) from users")).scalar() == 1
        assert c.execute(
            text("select auth_subject from users where id=:i"), {"i": DEFAULT_USER_ID}
        ).scalar() == "single"


def test_in_memory_engine_works():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(engine)
    assert "auth_subject" in _cols(engine, "users")


def _legacy_db(tmp_path):
    con = sqlite3.connect(tmp_path / "aliasfox.db")
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, created_at DATETIME)")
    con.execute("INSERT INTO users (id) VALUES (1)")
    con.commit()
    con.close()


def test_alembic_cli_path_adopts_legacy_db_without_secret_key(tmp_path, monkeypatch):
    from pathlib import Path

    import aliasfox
    from alembic import command
    from alembic.config import Config

    _legacy_db(tmp_path)
    monkeypatch.delenv("ALIASFOX_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cfg = Config()  # no connection attribute: the CLI path
    cfg.set_main_option("script_location", str(Path(aliasfox.__file__).parent / "migrations"))
    command.upgrade(cfg, "head")
    engine = _engine(tmp_path)
    assert {"auth_subject", "wrapped_key", "session_epoch"} <= _cols(engine, "users")
    assert "email" not in _cols(engine, "users")
    with engine.connect() as c:
        assert c.execute(text("select auth_subject from users where id=1")).scalar() == "single"
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0003"


def test_api_keys_table_created_and_dropped(tmp_path):
    from pathlib import Path

    import aliasfox
    from alembic import command
    from alembic.config import Config

    engine = _engine(tmp_path)
    init_db(engine)
    assert _cols(engine, "api_keys") >= {
        "id", "user_id", "name", "preset", "scopes", "key_hash", "prefix",
        "created_at", "last_used_at", "expires_at",
    }
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(aliasfox.__file__).parent / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0002")
    assert "api_keys" not in inspect(engine).get_table_names()

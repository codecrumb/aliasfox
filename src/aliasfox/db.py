from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, Engine, LargeBinary, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from aliasfox.config import Settings

DEFAULT_USER_ID = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    auth_subject: Mapped[str] = mapped_column(unique=True)
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Bumped on logout; signed into the session cookie so logout revokes every
    # outstanding cookie ("log out everywhere").
    session_epoch: Mapped[int] = mapped_column(default=0, server_default="0")


class AppleSessionRow(Base):
    __tablename__ = "apple_sessions"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


def _normalise_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(settings: Settings) -> Engine:
    if settings.database_url:
        return create_engine(_normalise_url(settings.database_url), pool_pre_ping=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{settings.data_dir}/aliasfox.db",
        connect_args={"check_same_thread": False},
    )


def _alembic_config(connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def _adopt_legacy_schema(conn) -> None:
    """Bring a pre-Alembic database to the 0001 shape, then stamp it."""
    insp = inspect(conn)
    if "users" not in insp.get_table_names() or "alembic_version" in insp.get_table_names():
        return
    if "session_epoch" not in {c["name"] for c in insp.get_columns("users")}:
        conn.execute(text("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0"))
    if "apple_sessions" not in insp.get_table_names():
        conn.execute(
            text(
                "CREATE TABLE apple_sessions (user_id INTEGER NOT NULL PRIMARY KEY, "
                "blob BLOB NOT NULL, updated_at DATETIME NOT NULL)"
                if conn.dialect.name == "sqlite"
                else "CREATE TABLE apple_sessions (user_id INTEGER NOT NULL PRIMARY KEY, "
                "blob BYTEA NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
            )
        )
    command.stamp(_alembic_config(conn), "0001")


def init_db(engine: Engine) -> None:
    with engine.begin() as conn:
        _adopt_legacy_schema(conn)
        command.upgrade(_alembic_config(conn), "head")
    with Session(engine) as s, s.begin():
        if s.get(User, DEFAULT_USER_ID) is None:
            s.add(User(id=DEFAULT_USER_ID, auth_subject="single"))

from datetime import datetime, timezone

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
    email: Mapped[str | None] = mapped_column(default=None)
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


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    # Minimal dev-DB upgrade until plan 3 introduces Alembic migrations.
    if "session_epoch" not in {c["name"] for c in inspect(engine).get_columns("users")}:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0")
            )
    with Session(engine) as s, s.begin():
        if s.get(User, DEFAULT_USER_ID) is None:
            s.add(User(id=DEFAULT_USER_ID))

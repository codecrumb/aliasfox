import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

from aliasfox.db import _normalise_url, adopt_legacy_schema

# The app passes its own connection (works for in-memory SQLite and shared pools).
# The CLI (`alembic -c alembic.ini ...`) builds its own engine from DATABASE_URL /
# DATA_DIR (no ALIASFOX_SECRET_KEY needed) and adopts pre-Alembic databases the
# same way init_db does.
connection = context.config.attributes.get("connection")


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=None, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if connection is not None:
    _run(connection)
else:
    url = os.environ.get("DATABASE_URL")
    if url:
        engine = create_engine(_normalise_url(url), pool_pre_ping=True)
    else:
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{data_dir}/aliasfox.db")
    with engine.begin() as conn:
        adopt_legacy_schema(conn)
        _run(conn)

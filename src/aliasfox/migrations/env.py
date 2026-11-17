from alembic import context
from sqlalchemy import create_engine

# The app passes its own connection (works for in-memory SQLite and shared pools).
# The CLI (`alembic -c alembic.ini ...`) falls back to DATABASE_URL / sqlalchemy.url.
connection = context.config.attributes.get("connection")

if connection is not None:
    context.configure(connection=connection, target_metadata=None, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    from aliasfox.config import get_settings
    from aliasfox.db import make_engine

    engine = (
        create_engine(context.config.get_main_option("sqlalchemy.url"))
        if context.config.get_main_option("sqlalchemy.url")
        else make_engine(get_settings())
    )
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()

from fastapi import FastAPI

from aliasfox.api import build_router, install_error_handlers
from aliasfox.auth import install_auth
from aliasfox.config import Settings, get_settings, validate_startup
from aliasfox.crypto import derive_key
from aliasfox.db import init_db, make_engine
from aliasfox.service import AliasService
from aliasfox.sessions import SessionStore


def create_app(
    settings: Settings | None = None,
    *,
    service: AliasService | None = None,
    store: SessionStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    validate_startup(settings)
    if service is None:
        engine = make_engine(settings)
        init_db(engine)
        store = SessionStore(engine, derive_key(settings.secret_key))
        service = AliasService(store)
    app = FastAPI(title="AliasFox")
    install_auth(app, settings)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(service, store, settings))
    install_error_handlers(app)
    return app

from fastapi import FastAPI, Request

from aliasfox.api import build_router, install_error_handlers
from aliasfox.apple.login import LoginFlow
from aliasfox.auth import install_auth
from aliasfox.config import Settings, get_settings, validate_startup
from aliasfox.crypto import derive_key
from aliasfox.db import init_db, make_engine
from aliasfox.service import AliasService
from aliasfox.sessions import SessionStore


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def install_security_headers(app: FastAPI) -> None:
    """Add last so it is the OUTERMOST middleware: the Host/Origin guards still run
    before any route, but their 403 responses pass back through here."""

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def create_app(
    settings: Settings | None = None,
    *,
    service: AliasService | None = None,
    store: SessionStore | None = None,
    login_flow: LoginFlow | None = None,
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

    app.include_router(build_router(service, store, settings, login_flow or LoginFlow()))
    install_error_handlers(app)
    install_security_headers(app)
    return app

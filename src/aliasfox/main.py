import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.concurrency import run_in_threadpool

from aliasfox.api import build_router, install_error_handlers
from aliasfox.apple.connect import seed_from_env
from aliasfox.apple.login import LoginFlow, cleanup_stale_login_dirs
from aliasfox.auth import install_auth
from aliasfox.config import Settings, get_settings, validate_startup
from aliasfox.crypto import derive_key
from aliasfox.db import init_db, make_engine
from aliasfox.middleware import (  # noqa: F401  (SECURITY_HEADERS re-exported)
    SECURITY_HEADERS,
    BodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from aliasfox.service import AliasService
from aliasfox.sessions import SessionStore

log = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60

def make_lifespan(settings: Settings, store: SessionStore | None, flow: LoginFlow):
    async def sweep_loop() -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await run_in_threadpool(flow.sweep)
            except Exception as exc:
                log.warning("login flow sweep failed: %s", type(exc).__name__)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_stale_login_dirs(flow.ttl_seconds)
        # pyicloud logs the Apple ID at DEBUG; httpx logs full request URLs.
        for noisy in ("pyicloud", "httpx"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        if settings.icloud_cookie and store is not None:
            try:
                await run_in_threadpool(seed_from_env, settings, store)
            except Exception as exc:  # never crash startup; never log cookie text
                log.warning("ICLOUD_COOKIE seed failed: %s", type(exc).__name__)
        task = asyncio.create_task(sweep_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await run_in_threadpool(flow.shutdown)

    return lifespan


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
    flow = login_flow or LoginFlow()
    app = FastAPI(
        title="AliasFox",
        lifespan=make_lifespan(settings, store, flow),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,  # no scheme-downgrading redirects
    )
    install_auth(app, settings)

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(service, store, settings, flow))
    install_error_handlers(app)
    # add_middleware: last added is outermost.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, trusted_proxy=settings.trusted_proxy)
    return app

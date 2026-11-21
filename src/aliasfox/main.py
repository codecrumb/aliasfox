import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.concurrency import run_in_threadpool

from aliasfox.api import build_router, install_error_handlers
from aliasfox.apple.connect import seed_from_env
from aliasfox.apple.login import LoginFlow, cleanup_stale_login_dirs
from aliasfox.auth import install_auth
from aliasfox.config import Settings, get_settings, validate_startup
from aliasfox.db import init_db, make_engine
from aliasfox.exchange import install_exchange, neon_issuer
from aliasfox.keys import KeyRing
from aliasfox.middleware import (  # noqa: F401  (SECURITY_HEADERS re-exported)
    SECURITY_HEADERS,
    BodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from aliasfox.neon_jwt import NeonTokenVerifier
from aliasfox.pages import install_pages
from aliasfox.service import AliasService
from aliasfox.sessions import SessionStore
from aliasfox.users import UserDirectory
from aliasfox.web import install_web

log = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60

async def sweep_memory(app: FastAPI, flow: LoginFlow) -> None:
    """One pass of the periodic in-memory cleanup: expired login flows, alias
    lists past their TTL, and rate-limiter keys (IP addresses) whose window has
    ended. The privacy policy's "removed from memory within about two minutes"
    relies on this running every SWEEP_INTERVAL_SECONDS."""
    try:
        await run_in_threadpool(flow.sweep)
    except Exception as exc:
        log.warning("login flow sweep failed: %s", type(exc).__name__)
    try:
        app.state.alias_service.evict_expired()
    except Exception as exc:
        log.warning("alias cache sweep failed: %s", type(exc).__name__)
    for limiter in getattr(app.state, "limiters", ()):
        try:
            limiter.sweep()
        except Exception as exc:
            log.warning("rate limiter sweep failed: %s", type(exc).__name__)


def make_lifespan(settings: Settings, store: SessionStore | None, flow: LoginFlow):
    async def sweep_loop(app: FastAPI) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            await sweep_memory(app, flow)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_stale_login_dirs(flow.ttl_seconds)
        # pyicloud logs the Apple ID at DEBUG; httpx logs full request URLs.
        for noisy in ("pyicloud", "httpx"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        # Single mode only: in multi mode there is no owner to seed it for.
        if settings.aliasfox_mode == "single" and settings.icloud_cookie and store is not None:
            try:
                await run_in_threadpool(seed_from_env, settings, store)
            except Exception as exc:  # never crash startup; never log cookie text
                log.warning("ICLOUD_COOKIE seed failed: %s", type(exc).__name__)
        task = asyncio.create_task(sweep_loop(app))
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
    dist_dir: Path | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    validate_startup(settings)
    multi = settings.aliasfox_mode == "multi"
    epochs = None
    if service is None or multi:  # multi mode always needs the users table
        engine = make_engine(settings)
        init_db(engine)
        keyring = KeyRing(settings.secret_key)
        epochs = UserDirectory(engine, keyring)
        if not multi:
            epochs.ensure_single_user()  # multi mode has no default user
        elif epochs.remove_single_user():  # left over from a switch from single mode
            log.info("removed the single-mode account")
        if multi and (n := epochs.revoke_subjects(settings.denylist)):
            log.info("revoked sessions of %d suspended user(s)", n)
        if service is None:
            store = SessionStore(engine, keyring)
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
    app.state.alias_service = service  # logout and the lifespan sweep drop its cache
    install_auth(app, settings, epochs, password_login=not multi)
    if multi:
        verifier = NeonTokenVerifier(
            jwks_url=settings.neon_auth_jwks_url, issuer=neon_issuer(settings)
        )
        install_exchange(app, settings, verifier, epochs)

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(
        build_router(
            service, store, settings, flow, directory=epochs, limiters=app.state.limiters
        )
    )
    install_error_handlers(app)
    install_pages(app, settings)  # multi mode only; before the SPA catch-all
    install_web(app, dist_dir)  # after the API router: its catch-all must not shadow /api
    # add_middleware: last added is outermost.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(BodyLimitMiddleware)
    # Multi mode: the browser talks to Neon Auth directly, so its origin joins connect-src.
    connect_origins = (settings.neon_auth_base_url,) if multi and settings.neon_auth_base_url else ()
    app.add_middleware(
        SecurityHeadersMiddleware,
        trusted_proxy=settings.trusted_proxy,
        connect_origins=connect_origins,
    )
    return app

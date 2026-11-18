"""Hosts the built web app (Vite output) next to the API.

Mount order matters: `/assets` is a mount registered before the catch-all, and
the catch-all itself refuses `/api/*` and `/healthz` so unknown API paths keep
their JSON 404."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.convertors import Convertor, register_url_convertor
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

DEFAULT_DIST = Path(__file__).parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
PUBLIC_FILE_CACHE = "public, max-age=86400"  # unhashed icons/manifest: stable names, rarely change
RESERVED = ("api", "healthz", "assets")


class ImmutableStaticFiles(StaticFiles):
    """Hashed build assets: cache forever."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE
        return response


class _SpaPathConvertor(Convertor):
    """Like `path`, but never matches the reserved prefixes: those requests fall
    through to the real routes (native 404/405) instead of being swallowed."""

    regex = r"(?!(?:" + "|".join(RESERVED) + r")(?:/|$)).*"

    def convert(self, value: str) -> str:
        return str(value)

    def to_string(self, value: str) -> str:
        return str(value)


register_url_convertor("spa_path", _SpaPathConvertor())


def _safe_file(root: Path, rel: str) -> Path | None:
    """Resolve `rel` under `root`; None unless it is a regular file inside root
    after symlink resolution."""
    if not rel or "\x00" in rel:
        return None
    try:
        candidate = (root / rel).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def install_web(app: FastAPI, dist_dir: Path | None = None) -> None:
    if dist_dir is None:
        env = os.environ.get("WEB_DIST_DIR")
        dist_dir = Path(env) if env else DEFAULT_DIST
    try:
        root = Path(dist_dir).resolve()
    except (OSError, RuntimeError):
        return
    index = root / "index.html"
    if not index.is_file():
        return  # nothing built: the app still starts, `/` is the normal 404
    app.state.spa_index = index  # pages.py serves it at `/` to signed-in users

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=assets), name="assets")

    @app.api_route(
        "/{full_path:spa_path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def spa(full_path: str):
        target = _safe_file(root, full_path)
        # index.html (also the SPA fallback) must always revalidate
        cache = PUBLIC_FILE_CACHE if target and target != index else NO_CACHE
        return FileResponse(target or index, headers={"Cache-Control": cache})

"""Hosts the built web app (Vite output) next to the API.

Mount order matters: `/assets` is a mount registered before the catch-all, and
the catch-all itself refuses `/api/*` and `/healthz` so unknown API paths keep
their JSON 404."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

DEFAULT_DIST = Path(__file__).parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
RESERVED = ("api", "healthz")


class ImmutableStaticFiles(StaticFiles):
    """Hashed build assets: cache forever."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE
        return response


def _reserved(path: str) -> bool:
    first = path.lstrip("/").split("/", 1)[0]
    return first in RESERVED


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

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=assets), name="assets")

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def spa(full_path: str, request: Request):
        if request.method not in ("GET", "HEAD") or _reserved(full_path):
            raise HTTPException(status_code=404)
        target = _safe_file(root, full_path)
        return FileResponse(
            target or index, headers={"Cache-Control": NO_CACHE}
        )

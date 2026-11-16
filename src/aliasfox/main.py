from fastapi import FastAPI

from aliasfox.config import Settings, get_settings, validate_startup


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    validate_startup(settings)
    app = FastAPI(title="AliasFox")
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app

"""FastAPI application factory and command-line entry point."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .database import Database
from .playback import InMemoryPlaybackController, PlaybackController
from .routes import captive, media, playback, playlists, system, text_panels
from .storage import MediaStorage


class JsonFormatter(logging.Formatter):
    """Single-line JSON logs suitable for journald and log collectors."""

    _standard = set(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._standard and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def create_app(
    settings: Optional[Settings] = None,
    playback_controller: Optional[PlaybackController] = None,
) -> FastAPI:
    configured = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configured.ensure_directories()
        app.state.database.initialize()
        logging.getLogger(__name__).info("application_started")
        yield
        app.state.playback.stop()
        logging.getLogger(__name__).info("application_stopped")

    configure_logging(configured.log_level)
    app = FastAPI(
        title="RGB Matrix Player",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = configured
    app.state.database = Database(configured.database_path)
    app.state.storage = MediaStorage(configured)
    app.state.playback = playback_controller or InMemoryPlaybackController()

    app.include_router(system.router)
    app.include_router(media.router)
    app.include_router(text_panels.router)
    app.include_router(playlists.router)
    app.include_router(playback.router)
    app.include_router(captive.router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root() -> HTMLResponse:
        index = static_dir / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<!doctype html><html><head><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Matrix Player</title>"
            "</head><body><h1>Matrix Player</h1><p>Service is ready.</p></body></html>"
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("matrix_player.app:app", host="0.0.0.0", port=8000)

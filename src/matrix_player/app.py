"""FastAPI application factory and command-line entry point."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .database import Database
from .matrix import MatrixAdapter, MatrixConfig, MockMatrixAdapter, RGBMatrixAdapter
from .playback import (
    DatabasePlaybackController,
    PlaybackController,
    PlaybackService,
    UnavailablePlaybackController,
)
from .routes import captive, media, playback, playlists, system, text_panels
from .storage import MediaStorage
from .transcode import VideoProxyWorker


class NoCacheStaticFiles(StaticFiles):
    """Serves UI assets with revalidation so appliance updates take effect."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
    matrix_adapter: Optional[MatrixAdapter] = None,
) -> FastAPI:
    configured = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configured.ensure_directories()
        app.state.database.initialize()
        app.state.proxies.start()
        app.state.proxies.submit_missing(
            row["filename"]
            for row in app.state.database.fetch_all(
                "SELECT filename FROM media WHERE media_type = 'video' ORDER BY id DESC"
            )
        )
        if playback_controller is None:
            try:
                adapter = matrix_adapter or _create_matrix(configured)
                app.state.matrix = adapter
                app.state.playback = DatabasePlaybackController(
                    PlaybackService(adapter), app.state.database, app.state.storage
                )
                app.state.matrix_connected = True
                app.state.matrix_error = None
            except Exception as error:
                message = f"matrix initialization failed: {error}"
                logging.getLogger(__name__).exception(
                    "matrix_initialization_failed", extra={"matrix_mode": configured.matrix_mode}
                )
                app.state.matrix = None
                app.state.matrix_connected = False
                app.state.matrix_error = message
                app.state.playback = UnavailablePlaybackController(message)
        if configured.autoplay_default and app.state.matrix_connected:
            default_playlist = app.state.database.fetch_one(
                """SELECT id FROM playlists
                   WHERE is_default = 1 AND is_enabled = 1
                   ORDER BY id LIMIT 1"""
            )
            if default_playlist is not None:
                playlist_id = int(default_playlist["id"])
                try:
                    app.state.playback.start(playlist_id)
                    logging.getLogger(__name__).info(
                        "default_playlist_started", extra={"playlist_id": playlist_id}
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "default_playlist_start_failed", extra={"playlist_id": playlist_id}
                    )
        try:
            logging.getLogger(__name__).info(
                "application_started",
                extra={
                    "matrix_mode": configured.matrix_mode,
                    "matrix_connected": app.state.matrix_connected,
                },
            )
            yield
        finally:
            app.state.proxies.close()
            try:
                app.state.playback.close()
            except Exception:
                logging.getLogger(__name__).exception("playback_shutdown_failed")
            app.state.matrix_connected = False
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
    app.state.proxies = VideoProxyWorker(app.state.storage)
    app.state.playback = playback_controller
    app.state.matrix = matrix_adapter
    app.state.matrix_connected = matrix_adapter is not None
    app.state.matrix_error = None

    app.include_router(system.router)
    app.include_router(media.router)
    app.include_router(text_panels.router)
    app.include_router(playlists.router)
    app.include_router(playback.router)
    app.include_router(captive.router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", NoCacheStaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root() -> HTMLResponse:
        index = static_dir / "index.html"
        if index.is_file():
            return HTMLResponse(
                index.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache"},
            )
        return HTMLResponse(
            "<!doctype html><html><head><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Matrix Player</title>"
            "</head><body><h1>Matrix Player</h1><p>Service is ready.</p></body></html>"
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/{path:path}", include_in_schema=False)
    def captive_fallback(path: str):
        if path == "api" or path.startswith(("api/", "static/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return RedirectResponse("/", status_code=302)

    return app


def _create_matrix(settings: Settings) -> MatrixAdapter:
    if settings.matrix_mode == "mock":
        return MockMatrixAdapter((settings.matrix_width, settings.matrix_height))
    return RGBMatrixAdapter(
        MatrixConfig(
            rows=settings.matrix_rows,
            cols=settings.matrix_cols,
            chain_length=settings.matrix_chain_length,
            parallel=settings.matrix_parallel,
            multiplexing=settings.matrix_multiplexing,
            pixel_mapper_config=settings.matrix_pixel_mapper,
            pwm_lsb_nanoseconds=settings.matrix_pwm_lsb_nanoseconds,
            gpio_slowdown=settings.matrix_gpio_slowdown,
        )
    )


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("matrix_player.app:app", host="0.0.0.0", port=80)

"""Playback state control endpoints."""

from io import BytesIO
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..database import Database
from ..playback import PlaybackController, PlaybackError
from ..schemas import PlaybackCommand, PlaybackState, SendCommand
from .dependencies import get_database, get_playback


router = APIRouter(prefix="/api/playback", tags=["playback"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PlaybackState)
@router.get("/state", response_model=PlaybackState)
def playback_state(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    return playback.state()


def _require_playable(database: Database, playlist_id: int) -> None:
    row = database.fetch_one("SELECT id, is_enabled FROM playlists WHERE id = ?", (playlist_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="playlist not found")
    if not row["is_enabled"]:
        raise HTTPException(status_code=409, detail="playlist is disabled")


@router.post("/start", response_model=PlaybackState)
def start(
    command: PlaybackCommand,
    database: Database = Depends(get_database),
    playback: PlaybackController = Depends(get_playback),
) -> PlaybackState:
    if command.playlist_id is None:
        raise HTTPException(status_code=422, detail="playlist_id is required")
    _require_playable(database, command.playlist_id)
    try:
        state = playback.start(command.playlist_id, from_start=command.from_start)
    except PlaybackError as error:
        logger.warning(
            "playback_start_failed",
            extra={"playlist_id": command.playlist_id, "error_code": error.code},
        )
        raise HTTPException(
            status_code=409, detail={"code": error.code, "message": str(error)}
        ) from error
    logger.info("playback_started", extra={"playlist_id": command.playlist_id})
    return state


@router.get("/frame")
def current_frame(request: Request) -> Response:
    """PNG of what the panel is showing right now, for the live mirror in the UI."""
    matrix = request.app.state.matrix
    frame = getattr(matrix, "frame", None) if matrix is not None else None
    if frame is None:
        raise HTTPException(status_code=503, detail="matrix unavailable")
    output = BytesIO()
    frame.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/send", response_model=PlaybackState)
def send(
    command: SendCommand,
    database: Database = Depends(get_database),
    playback: PlaybackController = Depends(get_playback),
) -> PlaybackState:
    """Interrupts playback to show one item until the viewer releases it."""
    if command.target_type == "playlist":
        _require_playable(database, command.target_id)
    try:
        if command.target_type == "playlist":
            state = playback.start(command.target_id)
        elif command.target_type == "media":
            state = playback.send_media(
                command.target_id, command.fit_mode.value, command.duration_ms,
                fit_blur=command.fit_blur,
            )
        else:
            state = playback.send_text_panel(command.target_id, command.duration_ms)
    except PlaybackError as error:
        logger.warning(
            "playback_send_failed",
            extra={
                "target_type": command.target_type,
                "target_id": command.target_id,
                "error_code": error.code,
            },
        )
        status_code = 404 if error.code.endswith("_not_found") else 409
        raise HTTPException(
            status_code=status_code, detail={"code": error.code, "message": str(error)}
        ) from error
    logger.info(
        "playback_sent",
        extra={"target_type": command.target_type, "target_id": command.target_id},
    )
    return state


@router.post("/release", response_model=PlaybackState)
def release(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    """Ends a direct send and resumes the playlist it interrupted."""
    return _control("release", playback)


def _control(action: str, playback: PlaybackController) -> PlaybackState:
    try:
        state = getattr(playback, action)()
    except ValueError as error:
        code = error.code if isinstance(error, PlaybackError) else "invalid_state"
        logger.warning("playback_control_failed", extra={"action": action, "error_code": code})
        raise HTTPException(
            status_code=409, detail={"code": code, "message": str(error)}
        ) from error
    logger.info("playback_changed", extra={"action": action, "status": state.status.value})
    return state


@router.post("/pause", response_model=PlaybackState)
def pause(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    return _control("pause", playback)


@router.post("/resume", response_model=PlaybackState)
def resume(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    return _control("resume", playback)


@router.post("/stop", response_model=PlaybackState)
def stop(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    return _control("stop", playback)

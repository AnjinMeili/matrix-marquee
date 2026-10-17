"""Playback state control endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..database import Database
from ..playback import PlaybackController, PlaybackError
from ..schemas import PlaybackCommand, PlaybackState
from .dependencies import get_database, get_playback


router = APIRouter(prefix="/api/playback", tags=["playback"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PlaybackState)
@router.get("/state", response_model=PlaybackState)
def playback_state(playback: PlaybackController = Depends(get_playback)) -> PlaybackState:
    return playback.state()


@router.post("/start", response_model=PlaybackState)
def start(
    command: PlaybackCommand,
    database: Database = Depends(get_database),
    playback: PlaybackController = Depends(get_playback),
) -> PlaybackState:
    if command.playlist_id is None:
        raise HTTPException(status_code=422, detail="playlist_id is required")
    row = database.fetch_one(
        "SELECT id, is_enabled FROM playlists WHERE id = ?", (command.playlist_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="playlist not found")
    if not row["is_enabled"]:
        raise HTTPException(status_code=409, detail="playlist is disabled")
    try:
        state = playback.start(command.playlist_id)
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

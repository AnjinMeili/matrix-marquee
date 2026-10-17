"""Panel brightness, gamma, and the calibration test card."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import display
from ..database import Database
from ..playback import PlaybackController, PlaybackError
from ..schemas import DisplaySettings, PlaybackState
from .dependencies import get_database, get_playback

router = APIRouter(prefix="/api/display", tags=["display"])
logger = logging.getLogger(__name__)


def _settings(correction: display.Correction) -> DisplaySettings:
    return DisplaySettings(brightness=correction.brightness, gamma=round(correction.gamma, 2))


@router.get("", response_model=DisplaySettings)
def get_display(database: Database = Depends(get_database)) -> DisplaySettings:
    return _settings(display.load(database))


@router.put("", response_model=DisplaySettings)
def update_display(
    body: DisplaySettings, request: Request, database: Database = Depends(get_database)
) -> DisplaySettings:
    """Saves and applies brightness and gamma; the panel changes straight away."""
    correction = display.Correction(brightness=body.brightness, gamma=round(body.gamma, 2))
    display.save(database, correction)
    matrix = request.app.state.matrix
    if matrix is not None:
        matrix.set_correction(correction)
    logger.info(
        "display_settings_changed",
        extra={"brightness": correction.brightness, "gamma": correction.gamma},
    )
    return _settings(correction)


@router.post("/test-card", response_model=PlaybackState)
def show_test_card(
    request: Request, playback: PlaybackController = Depends(get_playback)
) -> PlaybackState:
    """Puts the calibration card on the sign until "Back to playlist"."""
    matrix = request.app.state.matrix
    settings = request.app.state.settings
    size = tuple(matrix.size) if matrix is not None else (settings.matrix_width, settings.matrix_height)
    try:
        return playback.show_image(display.test_card(size), "Test card")
    except PlaybackError as error:
        raise HTTPException(status_code=409, detail={"code": error.code, "message": str(error)}) from error

"""Shared FastAPI dependencies."""

from fastapi import HTTPException, Request

from ..config import Settings
from ..database import Database
from ..playback import PlaybackController
from ..storage import MediaStorage


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> MediaStorage:
    return request.app.state.storage


def get_playback(request: Request) -> PlaybackController:
    return request.app.state.playback



def require_operator(request: Request) -> None:
    """Settings changes need a phone that has entered the operator PIN, once one is set."""
    from ..access import COOKIE

    if not request.app.state.access.allows(request.cookies.get(COOKIE)):
        raise HTTPException(
            status_code=403,
            detail={"code": "operator_pin_required", "message": "Enter the operator PIN in Settings to change this."},
        )

"""Shared FastAPI dependencies."""

from fastapi import Request

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


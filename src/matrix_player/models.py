"""Domain model definitions."""

from enum import Enum


class MediaType(str, Enum):
    IMAGE = "image"
    GIF = "gif"
    VIDEO = "video"


class ItemType(str, Enum):
    MEDIA = "media"
    TEXT_PANEL = "text_panel"


class TransitionType(str, Enum):
    NONE = "none"
    FADE = "fade"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"
    ROTATE = "rotate"


class FitMode(str, Enum):
    FIT = "fit"
    CROP = "crop"
    LETTERBOX = "letterbox"


class PlaybackStatus(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


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
    FADE_OUT = "fade_out"
    ZOOM = "zoom"
    PIXEL_DISSOLVE = "pixel_dissolve"
    FLASH = "flash"
    IMPACT = "impact"
    MORPH = "morph"


class PlayMode(str, Enum):
    LOOP = "loop"
    SHUFFLE = "shuffle"
    RANDOM = "random"
    ONCE = "once"


class PlayBy(str, Enum):
    TIME = "time"
    REPEATS = "repeats"


class FitMode(str, Enum):
    FIT = "fit"
    CROP = "crop"
    LETTERBOX = "letterbox"
    STRETCH = "stretch"


class PlaybackStatus(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


"""Validated API request and response models."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from .models import FitMode, ItemType, MediaType, PlaybackStatus, TransitionType


class ApiModel(BaseModel):
    model_config = {"from_attributes": True}


class Media(ApiModel):
    id: int
    filename: str
    display_name: str
    media_type: MediaType
    mime_type: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None
    frame_rate: Optional[float] = None
    file_size_bytes: int
    thumbnail_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TextPanelBase(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(max_length=2000)
    font_family: str = Field(default="default", max_length=100)
    font_size: int = Field(default=12, ge=4, le=128)
    bold: bool = False
    italic: bool = False
    alignment: str = Field(default="center", pattern="^(left|center|right)$")
    color_mode: str = Field(default="solid", pattern="^(solid|rainbow|plasma)$")
    color_value: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    border_style: str = Field(default="none", pattern="^(none|solid|dashed|double)$")
    border_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class TextPanelCreate(TextPanelBase):
    pass


class TextPanelUpdate(TextPanelBase):
    pass


class TextPanel(TextPanelBase):
    id: int
    created_at: datetime
    updated_at: datetime


class PlaylistItemBase(ApiModel):
    position: Optional[int] = Field(default=None, ge=0)
    item_type: ItemType
    media_id: Optional[int] = None
    text_panel_id: Optional[int] = None
    duration_ms: int = Field(default=5000, ge=100, le=86_400_000)
    repeat_count: int = Field(default=1, ge=1, le=10000)
    transition_type: TransitionType = TransitionType.NONE
    transition_duration_ms: int = Field(default=500, ge=0, le=60_000)
    fit_mode: FitMode = FitMode.FIT

    @model_validator(mode="after")
    def validate_target(self) -> "PlaylistItemBase":
        if self.item_type == ItemType.MEDIA:
            if self.media_id is None or self.text_panel_id is not None:
                raise ValueError("media items require media_id only")
        elif self.text_panel_id is None or self.media_id is not None:
            raise ValueError("text_panel items require text_panel_id only")
        return self


class PlaylistItemCreate(PlaylistItemBase):
    pass


class PlaylistItemUpdate(PlaylistItemBase):
    pass


class PlaylistItem(PlaylistItemBase):
    id: int
    playlist_id: int
    position: int


class PlaylistBase(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    is_default: bool = False
    is_enabled: bool = True


class PlaylistCreate(PlaylistBase):
    pass


class PlaylistUpdate(PlaylistBase):
    pass


class Playlist(PlaylistBase):
    id: int
    created_at: datetime
    updated_at: datetime
    items: List[PlaylistItem] = Field(default_factory=list)


class PlaybackCommand(ApiModel):
    playlist_id: Optional[int] = None


class SendCommand(ApiModel):
    target_type: str = Field(pattern="^(media|text_panel|playlist)$")
    target_id: int = Field(ge=1)
    fit_mode: FitMode = FitMode.FIT
    duration_ms: Optional[int] = Field(default=None, ge=100, le=600000)


class PlaybackState(ApiModel):
    status: PlaybackStatus
    playlist_id: Optional[int] = None
    item_id: Optional[int] = None
    source_type: Optional[str] = None
    source_label: Optional[str] = None
    resume_playlist_id: Optional[int] = None
    changed_at: datetime
    error: Optional[str] = None


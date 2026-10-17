"""Animated previews of playlist items and run-throughs, rendered by the sign's own engine.

Previews are shortened (each item is capped) so they render in about a second
on the Pi, and only one renders at a time so a burst of edits cannot starve
the matrix.
"""

from io import BytesIO
import threading
from typing import List, Mapping, Optional, Sequence, Tuple

from PIL import Image

from .database import Database
from .models import ItemType, PlayBy
from .playback import (
    PlaybackError,
    TEXT_COLUMNS,
    build_media_source,
    build_text_source,
    text_query,
)
from .rendering import FrameSource, Transition, transition_frames
from .storage import MediaStorage

ITEM_PREVIEW_SECONDS = 4.0
RUN_THROUGH_SECONDS = 2.5
LEAD_IN_SECONDS = 0.6
MAX_FRAMES = 400

_render_lock = threading.Lock()


def _normalize(image: Image.Image, size: Tuple[int, int]) -> Image.Image:
    image = image.convert("RGB")
    return image if image.size == size else image.resize(size, Image.Resampling.LANCZOS)


def sample(
    source: FrameSource,
    size: Tuple[int, int],
    fps: float,
    seconds: float,
    loops: Optional[int] = None,
) -> List[Image.Image]:
    """Samples a source at a fixed rate, looping it like the player does."""
    target = max(1, round(seconds * fps))
    step = 1.0 / fps
    output: List[Image.Image] = []
    elapsed = 0.0
    next_tick = 0.0
    passes = 0
    try:
        while len(output) < target:
            produced = False
            frames = iter(source)
            try:
                for frame in frames:
                    produced = True
                    end = elapsed + max(frame.duration, 1e-3)
                    image = None
                    while next_tick < end and len(output) < target:
                        if image is None:
                            image = _normalize(frame.image, size)
                        output.append(image)
                        next_tick += step
                    elapsed = end
                    if len(output) >= target:
                        break
            finally:
                close = getattr(frames, "close", None)
                if close is not None:
                    close()
            if not produced:
                break
            passes += 1
            if loops is not None and passes >= loops:
                break
    finally:
        source.close()
    return output


def encode_animation(frames: Sequence[Image.Image], fps: float) -> bytes:
    """Animated WebP: on the Pi it encodes ~8x faster than GIF at a third of the size,
    which matters for photo-heavy playlists sent over the sign's Wi-Fi."""
    output = BytesIO()
    frames[0].save(
        output, format="WEBP", save_all=True, append_images=list(frames[1:]),
        duration=round(1000 / fps), loop=0, quality=70, method=0,
    )
    return output.getvalue()


class PreviewRenderer:
    def __init__(self, database: Database, storage: MediaStorage, size: Tuple[int, int]) -> None:
        self.database = database
        self.storage = storage
        self.size = size

    def _source(self, item: Mapping[str, object]) -> Tuple[FrameSource, bool]:
        """Builds an item's frame source; also says whether it is an animation."""
        duration = int(item["duration_ms"]) / 1000.0
        if item["item_type"] == ItemType.MEDIA.value:
            row = self.database.fetch_one(
                "SELECT filename, media_type FROM media WHERE id = ?", (item["media_id"],)
            )
            if row is None:
                raise PlaybackError("media_not_found", "media not found")
            source = build_media_source(
                self.storage, row["filename"], self.size, duration, str(item["fit_mode"]),
                row["media_type"],
            )
            return source, row["media_type"] != "image"
        row = self.database.fetch_one(text_query("tp.id = ?"), (item["text_panel_id"],))
        if row is None:
            raise PlaybackError("text_panel_not_found", "text panel not found")
        values = {key: row[key] for key in TEXT_COLUMNS}
        return build_text_source(self.storage, values, row["background_filename"], self.size, duration), False

    def _item_frames(self, item: Mapping[str, object], fps: float, cap: float) -> List[Image.Image]:
        source, animated = self._source(item)
        duration = int(item["duration_ms"]) / 1000.0
        if animated and item.get("play_by") == PlayBy.REPEATS.value:
            return sample(source, self.size, fps, cap, loops=int(item.get("repeat_count") or 1))
        minimum = float(getattr(source, "min_seconds", 0.0))
        return sample(source, self.size, fps, min(cap, max(duration, minimum)))

    def _entrance(
        self, previous: Image.Image, first: Image.Image, item: Mapping[str, object], fps: float
    ) -> List[Image.Image]:
        kind = str(item.get("transition_type") or "none")
        seconds = int(item.get("transition_duration_ms") or 0) / 1000.0
        if kind == "none" or seconds <= 0:
            return []
        count = max(2, round(seconds * fps))
        return list(transition_frames(previous, first, Transition(kind), count))[:-1]

    def _sequence(self, items: Sequence[Mapping[str, object]], fps: float, cap: float, lead_in: Optional[Mapping[str, object]]) -> List[Image.Image]:
        frames: List[Image.Image] = []
        previous = Image.new("RGB", self.size)
        if lead_in is not None:
            try:
                held = self._item_frames(lead_in, fps, LEAD_IN_SECONDS)
            except (PlaybackError, OSError, ValueError, RuntimeError):
                held = []
            frames += held
            previous = held[-1] if held else previous
        for item in items:
            if len(frames) >= MAX_FRAMES:
                break
            try:
                shown = self._item_frames(item, fps, cap)
            except (PlaybackError, OSError, ValueError, RuntimeError) as error:
                shown = [self._error_card(str(error))] * max(1, round(fps))
            if not shown:
                continue
            frames += self._entrance(previous, shown[0], item, fps)
            frames += shown
            previous = shown[-1]
        return frames[:MAX_FRAMES] or [Image.new("RGB", self.size)]

    def _error_card(self, message: str) -> Image.Image:
        from .rendering import TextRenderer, TextStyle

        style = TextStyle(font_family="sans", color_value="#ffffff", background_color="#7a0000", outline=False)
        return TextRenderer(f"Can't show: {message}", self.size, style).frame()

    def item_preview(self, item: Mapping[str, object], previous: Optional[Mapping[str, object]], fps: float = 12.0) -> bytes:
        """The previous item, then this item's entrance and its first seconds."""
        with _render_lock:
            return encode_animation(self._sequence([item], fps, ITEM_PREVIEW_SECONDS, previous), fps)

    def run_through(self, items: Sequence[Mapping[str, object]], fps: float = 8.0) -> bytes:
        """Several items in playlist order, each shortened, with their real entrances."""
        with _render_lock:
            return encode_animation(self._sequence(items, fps, RUN_THROUGH_SECONDS, None), fps)

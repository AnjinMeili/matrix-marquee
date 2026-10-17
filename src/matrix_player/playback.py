"""Thread-safe playback controls and the matrix render worker."""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Callable,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Union,
)

from PIL import Image

from .database import Database
from .matrix import MatrixAdapter
from .models import ItemType, PlaybackStatus
from .rendering import (
    Frame,
    FrameSource,
    GifFrameSource,
    ImageFrameSource,
    TextFrameSource,
    Transition,
    VideoFrameSource,
    transition_frames,
)
from .schemas import PlaybackState
from .storage import MediaStorage


class PlaybackController(Protocol):
    def state(self) -> PlaybackState: ...
    def start(self, playlist_id: int) -> PlaybackState: ...
    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState: ...
    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState: ...
    def release(self) -> PlaybackState: ...
    def pause(self) -> PlaybackState: ...
    def resume(self) -> PlaybackState: ...
    def stop(self) -> PlaybackState: ...
    def close(self) -> None: ...


class InMemoryPlaybackController:
    """Small API-facing state controller used when no renderer is configured."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = PlaybackState(
            status=PlaybackStatus.STOPPED,
            changed_at=datetime.now(timezone.utc),
        )

    def state(self) -> PlaybackState:
        with self._lock:
            return self._state.model_copy()

    def _set(
        self,
        status: PlaybackStatus,
        playlist_id: Optional[int],
        source_type: Optional[str] = None,
        source_label: Optional[str] = None,
        resume_playlist_id: Optional[int] = None,
    ) -> PlaybackState:
        self._state = PlaybackState(
            status=status,
            playlist_id=playlist_id,
            source_type=source_type,
            source_label=source_label,
            resume_playlist_id=resume_playlist_id,
            changed_at=datetime.now(timezone.utc),
        )
        return self._state.model_copy()

    def start(self, playlist_id: int) -> PlaybackState:
        with self._lock:
            return self._set(PlaybackStatus.PLAYING, playlist_id, "playlist")

    def _send(self, source_type: str, target_id: int) -> PlaybackState:
        with self._lock:
            interrupted = (
                self._state.playlist_id
                if self._state.status != PlaybackStatus.STOPPED
                else None
            )
            return self._set(
                PlaybackStatus.PLAYING,
                None,
                source_type,
                f"{source_type}:{target_id}",
                interrupted,
            )

    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState:
        return self._send("media", media_id)

    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState:
        return self._send("text_panel", panel_id)

    def release(self) -> PlaybackState:
        with self._lock:
            if self._state.source_type not in {"media", "text_panel"}:
                raise ValueError("no item is being shown")
            resume_id = self._state.resume_playlist_id
            if resume_id is None:
                return self._set(PlaybackStatus.STOPPED, None)
            return self._set(PlaybackStatus.PLAYING, resume_id, "playlist")

    def pause(self) -> PlaybackState:
        with self._lock:
            if self._state.status != PlaybackStatus.PLAYING:
                raise ValueError("playback is not playing")
            return self._set(
                PlaybackStatus.PAUSED,
                self._state.playlist_id,
                self._state.source_type,
                self._state.source_label,
                self._state.resume_playlist_id,
            )

    def resume(self) -> PlaybackState:
        with self._lock:
            if self._state.status != PlaybackStatus.PAUSED:
                raise ValueError("playback is not paused")
            return self._set(
                PlaybackStatus.PLAYING,
                self._state.playlist_id,
                self._state.source_type,
                self._state.source_label,
                self._state.resume_playlist_id,
            )

    def stop(self) -> PlaybackState:
        with self._lock:
            return self._set(PlaybackStatus.STOPPED, None)

    def close(self) -> None:
        self.stop()


class RendererState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


Source = Union[
    FrameSource,
    Image.Image,
    str,
    Path,
    Iterable[Frame],
    Callable[[], Iterable[Frame]],
]

DEFAULT_SEND_DURATION_MS = 10000


@dataclass
class PlaybackItem:
    source: Source
    duration: Optional[float] = None
    repeats: int = 1
    transition: Union[Transition, str] = Transition.CUT
    transition_duration: float = 0.35
    item_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.duration is not None and self.duration < 0:
            raise ValueError("duration cannot be negative")
        if self.repeats <= 0:
            raise ValueError("repeats must be positive")
        if self.transition_duration < 0:
            raise ValueError("transition_duration cannot be negative")


class PlaybackService:
    """Single-worker playback service that blanks output on stop or error."""

    def __init__(
        self,
        matrix: MatrixAdapter,
        transition_fps: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if transition_fps <= 0:
            raise ValueError("transition_fps must be positive")
        self.matrix = matrix
        self.transition_fps = transition_fps
        self._clock = clock
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = RendererState.STOPPED
        self._error: Optional[BaseException] = None
        self._current_item_id: Optional[int] = None

    @property
    def state(self) -> RendererState:
        with self._lock:
            return self._state

    @property
    def error(self) -> Optional[BaseException]:
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        return self.state in {RendererState.STARTING, RendererState.PLAYING, RendererState.PAUSED}

    @property
    def current_item_id(self) -> Optional[int]:
        with self._lock:
            return self._current_item_id

    def start(self, items: Iterable[Union[PlaybackItem, Source]], loop: bool = False) -> None:
        normalized = [
            item if isinstance(item, PlaybackItem) else PlaybackItem(item)
            for item in items
        ]
        if not normalized:
            raise ValueError("playback requires at least one item")
        self.stop()
        with self._condition:
            self._stop_event.clear()
            self._error = None
            self._current_item_id = None
            self._state = RendererState.STARTING
            self._thread = threading.Thread(
                target=self._run, args=(normalized, loop), name="matrix-playback", daemon=True
            )
            self._thread.start()

    def pause(self) -> bool:
        with self._condition:
            if self._state not in {RendererState.STARTING, RendererState.PLAYING}:
                return False
            self._state = RendererState.PAUSED
            return True

    def resume(self) -> bool:
        with self._condition:
            if self._state is not RendererState.PAUSED:
                return False
            self._state = RendererState.PLAYING
            self._condition.notify_all()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        with self._condition:
            thread = self._thread
            self._stop_event.set()
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._condition:
            self._thread = None
            self._state = RendererState.STOPPED
            self._current_item_id = None
        self._safe_blank()

    def close(self) -> None:
        self.stop()
        self.matrix.close()

    def _safe_blank(self) -> None:
        try:
            self.matrix.blank()
        except Exception:
            pass

    def _wait_ready(self) -> bool:
        with self._condition:
            while self._state is RendererState.PAUSED and not self._stop_event.is_set():
                self._condition.wait()
            return not self._stop_event.is_set()

    def _delay(self, seconds: float) -> bool:
        deadline = self._clock() + max(0.0, seconds)
        while True:
            if not self._wait_ready():
                return False
            remaining = deadline - self._clock()
            if remaining <= 0:
                return True
            if self._stop_event.wait(min(remaining, 0.05)):
                return False
            with self._condition:
                if self._state is RendererState.PAUSED:
                    paused_at = self._clock()
                    while self._state is RendererState.PAUSED and not self._stop_event.is_set():
                        self._condition.wait()
                    deadline += self._clock() - paused_at

    def _source(self, source: Source) -> Iterable[Frame]:
        if isinstance(source, FrameSource):
            return source
        if isinstance(source, Image.Image):
            return ImageFrameSource(source, self.matrix.size)
        if isinstance(source, (str, Path)):
            path = Path(source)
            if path.suffix.lower() == ".gif":
                return GifFrameSource(path, self.matrix.size)
            if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
                return VideoFrameSource(path, self.matrix.size)
            return ImageFrameSource(path, self.matrix.size)
        return source() if callable(source) else source

    def _play_item(
        self, item: PlaybackItem, previous: Optional[Image.Image]
    ) -> Optional[Image.Image]:
        first = True
        current = previous
        for _ in range(item.repeats):
            started = self._clock()
            source = self._source(item.source)
            try:
                for frame in source:
                    if not self._wait_ready():
                        return current
                    image = frame.image.convert("RGB")
                    if image.size != self.matrix.size:
                        image = image.resize(self.matrix.size, Image.Resampling.LANCZOS)
                    if first and previous is not None and Transition(item.transition) is not Transition.CUT:
                        count = max(1, round(item.transition_duration * self.transition_fps))
                        step = item.transition_duration / count
                        for rendered in transition_frames(previous, image, item.transition, count):
                            if not self._wait_ready():
                                return current
                            self.matrix.show(rendered)
                            current = rendered
                            if not self._delay(step):
                                return current
                    else:
                        self.matrix.show(image)
                        current = image
                    first = False
                    delay = frame.duration
                    if item.duration is not None:
                        remaining = item.duration - (self._clock() - started)
                        if remaining <= 0:
                            break
                        delay = min(delay, remaining)
                    if not self._delay(delay):
                        return current
                    if item.duration is not None and self._clock() - started >= item.duration:
                        break
            finally:
                if isinstance(source, FrameSource):
                    source.close()
        return current

    def _run(self, items: List[PlaybackItem], loop: bool) -> None:
        try:
            with self._condition:
                if self._state is RendererState.STARTING:
                    self._state = RendererState.PLAYING
            previous: Optional[Image.Image] = None
            while not self._stop_event.is_set():
                for item in items:
                    if self._stop_event.is_set():
                        break
                    with self._condition:
                        self._current_item_id = item.item_id
                    previous = self._play_item(item, previous)
                if not loop:
                    break
            if not self._stop_event.is_set():
                with self._condition:
                    self._state = RendererState.STOPPED
                self._safe_blank()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._state = RendererState.ERROR
            self._safe_blank()
        finally:
            with self._condition:
                self._thread = None


class PlaybackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DatabasePlaybackController:
    """Loads persisted playlists and controls the matrix render worker."""

    def __init__(
        self, service: PlaybackService, database: Database, storage: MediaStorage
    ) -> None:
        self.service = service
        self.database = database
        self.storage = storage
        self._lock = threading.RLock()
        self._playlist_id: Optional[int] = None
        self._source_type: Optional[str] = None
        self._source_label: Optional[str] = None
        self._resume_playlist_id: Optional[int] = None
        self._changed_at = datetime.now(timezone.utc)

    @staticmethod
    def _transition(value: str) -> Transition:
        return Transition.CUT if value == "none" else Transition(value)

    def _media_source(self, filename: Optional[str], duration: float, fit_mode: str) -> Source:
        if not filename:
            raise PlaybackError("media_not_found", "media record is missing")
        path = self.storage.media_path(filename)
        if not path.is_file():
            raise PlaybackError("media_file_missing", "media file is missing")
        suffix = path.suffix.lower()
        if suffix == ".gif":
            return GifFrameSource(path, self.service.matrix.size, fit_mode)
        if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            return VideoFrameSource(path, self.service.matrix.size)
        return ImageFrameSource(path, self.service.matrix.size, duration, fit_mode)

    def _text_source(self, row: Mapping[str, object], duration: float) -> Source:
        if row["content"] is None:
            raise PlaybackError("text_panel_not_found", "text panel is missing")
        font = None if row["font_family"] == "default" else row["font_family"]
        return TextFrameSource(
            row["content"],
            self.service.matrix.size,
            duration,
            font=font,
            font_size=row["font_size"],
            color=row["color_value"],
            align=row["alignment"],
            border_color=(row["border_color"] if row["border_style"] != "none" else None),
            border_width=1 if row["border_style"] != "none" else 0,
        )

    def _items(self, playlist_id: int) -> List[PlaybackItem]:
        rows = self.database.fetch_all(
            """SELECT pi.*, m.filename,
                      tp.content, tp.font_family, tp.font_size, tp.alignment,
                      tp.color_value, tp.border_style, tp.border_color
               FROM playlist_items pi
               LEFT JOIN media m ON m.id = pi.media_id
               LEFT JOIN text_panels tp ON tp.id = pi.text_panel_id
               WHERE pi.playlist_id = ? ORDER BY pi.position, pi.id""",
            (playlist_id,),
        )
        items: List[PlaybackItem] = []
        for row in rows:
            duration = row["duration_ms"] / 1000.0
            if row["item_type"] == ItemType.MEDIA.value:
                source = self._media_source(row["filename"], duration, row["fit_mode"])
            else:
                source = self._text_source(row, duration)
            items.append(
                PlaybackItem(
                    source=source,
                    duration=duration,
                    repeats=row["repeat_count"],
                    transition=self._transition(row["transition_type"]),
                    transition_duration=row["transition_duration_ms"] / 1000.0,
                    item_id=row["id"],
                )
            )
        if not items:
            raise PlaybackError("empty_playlist", "playlist has no items")
        return items

    def state(self) -> PlaybackState:
        renderer_state = self.service.state
        status = {
            RendererState.STOPPED: PlaybackStatus.STOPPED,
            RendererState.STARTING: PlaybackStatus.PLAYING,
            RendererState.PLAYING: PlaybackStatus.PLAYING,
            RendererState.PAUSED: PlaybackStatus.PAUSED,
            RendererState.ERROR: PlaybackStatus.ERROR,
        }[renderer_state]
        error = self.service.error
        with self._lock:
            stopped = status == PlaybackStatus.STOPPED
            return PlaybackState(
                status=status,
                playlist_id=self._playlist_id if not stopped else None,
                item_id=self.service.current_item_id,
                source_type=self._source_type if not stopped else None,
                source_label=self._source_label if not stopped else None,
                resume_playlist_id=self._resume_playlist_id if not stopped else None,
                changed_at=self._changed_at,
                error=str(error) if error is not None else None,
            )

    def start(self, playlist_id: int) -> PlaybackState:
        items = self._items(playlist_id)
        row = self.database.fetch_one(
            "SELECT name FROM playlists WHERE id = ?", (playlist_id,)
        )
        self.service.start(items, loop=True)
        with self._lock:
            self._playlist_id = playlist_id
            self._source_type = "playlist"
            self._source_label = row["name"] if row is not None else None
            self._resume_playlist_id = None
            self._changed_at = datetime.now(timezone.utc)
        return self.state()

    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState:
        row = self.database.fetch_one(
            "SELECT filename, display_name FROM media WHERE id = ?", (media_id,)
        )
        if row is None:
            raise PlaybackError("media_not_found", "media not found")
        duration = (duration_ms or DEFAULT_SEND_DURATION_MS) / 1000.0
        source = self._media_source(row["filename"], duration, fit_mode)
        self._send(source, duration, "media", row["display_name"])
        return self.state()

    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState:
        row = self.database.fetch_one(
            """SELECT name, content, font_family, font_size, alignment,
                      color_value, border_style, border_color
               FROM text_panels WHERE id = ?""",
            (panel_id,),
        )
        if row is None:
            raise PlaybackError("text_panel_not_found", "text panel not found")
        duration = (duration_ms or DEFAULT_SEND_DURATION_MS) / 1000.0
        source = self._text_source(row, duration)
        self._send(source, duration, "text_panel", row["name"])
        return self.state()

    def _send(self, source: Source, duration: float, source_type: str, label: str) -> None:
        with self._lock:
            interrupted = self._playlist_id if self.service.is_running else None
        item = PlaybackItem(
            source=source,
            duration=duration,
            repeats=1,
            transition=Transition.CUT,
            transition_duration=0.0,
        )
        self.service.start([item], loop=True)
        with self._lock:
            self._playlist_id = None
            self._source_type = source_type
            self._source_label = label
            self._resume_playlist_id = interrupted
            self._changed_at = datetime.now(timezone.utc)

    def release(self) -> PlaybackState:
        """Ends a direct send and restores the playlist it interrupted."""
        with self._lock:
            if self._source_type not in {"media", "text_panel"}:
                raise PlaybackError("not_sending", "no item is being shown")
            resume_id = self._resume_playlist_id
        if resume_id is None:
            return self.stop()
        try:
            return self.start(resume_id)
        except PlaybackError:
            return self.stop()

    def pause(self) -> PlaybackState:
        if not self.service.pause():
            raise PlaybackError("not_playing", "playback is not playing")
        with self._lock:
            self._changed_at = datetime.now(timezone.utc)
        return self.state()

    def resume(self) -> PlaybackState:
        if not self.service.resume():
            raise PlaybackError("not_paused", "playback is not paused")
        with self._lock:
            self._changed_at = datetime.now(timezone.utc)
        return self.state()

    def stop(self) -> PlaybackState:
        self.service.stop()
        with self._lock:
            self._playlist_id = None
            self._source_type = None
            self._source_label = None
            self._resume_playlist_id = None
            self._changed_at = datetime.now(timezone.utc)
        return self.state()

    def close(self) -> None:
        self.service.close()

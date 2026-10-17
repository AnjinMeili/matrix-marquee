"""Thread-safe playback controls and the matrix render worker."""

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Callable,
    Dict,
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
from .models import ItemType, PlaybackStatus, PlayBy, PlayMode
from .rendering import (
    Frame,
    FrameSource,
    GifFrameSource,
    ImageFrameSource,
    TextFrameSource,
    Transition,
    VideoFrameSource,
    load_background,
    style_from_mapping,
    transition_frames,
)
from .schemas import PlaybackState
from .storage import MediaStorage

logger = logging.getLogger(__name__)
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class PlaybackController(Protocol):
    def state(self) -> PlaybackState: ...
    def start(self, playlist_id: int, from_start: bool = False) -> PlaybackState: ...
    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState: ...
    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState: ...
    def show_image(self, image: Image.Image, label: str) -> PlaybackState: ...
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

    def start(self, playlist_id: int, from_start: bool = False) -> PlaybackState:
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

    def show_image(self, image: Image.Image, label: str) -> PlaybackState:
        return self._send("media", 0)

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
FAILED_PASS_BACKOFF_SECONDS = 2.0
RESYNC_SECONDS = 0.25


@dataclass
class PlaybackItem:
    """One playlist entry.

    play_by "time" shows the item for ``duration`` seconds, looping animations
    to fill it; "repeats" plays the animation ``repeats`` complete times. With
    no duration, "time" plays the source ``repeats`` times at its own pace.
    """

    source: Source
    duration: Optional[float] = None
    repeats: int = 1
    transition: Union[Transition, str] = Transition.CUT
    transition_duration: float = 0.35
    item_id: Optional[int] = None
    play_by: str = PlayBy.TIME.value
    label: Optional[str] = None

    def __post_init__(self) -> None:
        if self.duration is not None and self.duration < 0:
            raise ValueError("duration cannot be negative")
        if self.repeats <= 0:
            raise ValueError("repeats must be positive")
        if self.transition_duration < 0:
            raise ValueError("transition_duration cannot be negative")


ItemCallback = Callable[[int, PlaybackItem], None]


class PlaybackService:
    """Single-worker playback service that skips bad items and blanks on stop."""

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
        self._local = threading.local()
        self._thread: Optional[threading.Thread] = None
        self._state = RendererState.STOPPED
        self._error: Optional[BaseException] = None
        self._current_item_id: Optional[int] = None
        self._current_index: Optional[int] = None
        self._paused_total = 0.0
        self._frames_shown = 0
        self._warnings: Dict[object, str] = {}

    @property
    def state(self) -> RendererState:
        with self._lock:
            return self._state

    @property
    def error(self) -> Optional[BaseException]:
        with self._lock:
            return self._error

    @property
    def warnings(self) -> List[str]:
        with self._lock:
            return list(self._warnings.values())

    @property
    def is_running(self) -> bool:
        return self.state in {RendererState.STARTING, RendererState.PLAYING, RendererState.PAUSED}

    @property
    def current_item_id(self) -> Optional[int]:
        with self._lock:
            return self._current_item_id

    @property
    def current_index(self) -> Optional[int]:
        with self._lock:
            return self._current_index

    def start(
        self,
        items: Iterable[Union[PlaybackItem, Source]],
        loop: bool = False,
        mode: Optional[str] = None,
        start_index: int = 0,
        on_item: Optional[ItemCallback] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        normalized = [
            item if isinstance(item, PlaybackItem) else PlaybackItem(item)
            for item in items
        ]
        if not normalized:
            raise ValueError("playback requires at least one item")
        selected = PlayMode(mode or (PlayMode.LOOP if loop else PlayMode.ONCE)).value
        self.stop(blank=False)
        with self._condition:
            # Each run owns its stop event so a straggling worker can never be
            # revived by the next start.
            self._stop_event = threading.Event()
            self._error = None
            self._warnings = {}
            self._current_item_id = None
            self._current_index = None
            self._state = RendererState.STARTING
            self._thread = threading.Thread(
                target=self._run,
                args=(normalized, selected, start_index, self._stop_event, on_item, on_finished),
                name="matrix-playback",
                daemon=True,
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

    def stop(self, timeout: float = 5.0, blank: bool = True) -> None:
        with self._condition:
            thread = self._thread
            self._stop_event.set()
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("playback_worker_stop_timeout")
        with self._condition:
            self._thread = None
            self._state = RendererState.STOPPED
            self._current_item_id = None
            self._current_index = None
        if blank:
            self._safe_blank()

    def close(self) -> None:
        self.stop()
        self.matrix.close()

    def _safe_blank(self) -> None:
        try:
            self.matrix.blank()
        except Exception:
            logger.exception("matrix_blank_failed")

    # Timing uses a play clock that excludes paused time, so pausing never
    # eats into an item's display time.
    def _now(self) -> float:
        return self._clock() - self._paused_total

    def _run_stop(self) -> threading.Event:
        return getattr(self._local, "stop", None) or self._stop_event

    def _stopping(self) -> bool:
        return self._run_stop().is_set()

    def _wait_ready(self) -> bool:
        with self._condition:
            if self._state is RendererState.PAUSED and not self._stopping():
                began = self._clock()
                while self._state is RendererState.PAUSED and not self._stopping():
                    self._condition.wait(0.25)
                self._paused_total += self._clock() - began
            return not self._stopping()

    def _delay(self, seconds: float) -> bool:
        deadline = self._now() + max(0.0, seconds)
        while True:
            if not self._wait_ready():
                return False
            remaining = deadline - self._now()
            if remaining <= 0:
                return True
            if self._run_stop().wait(min(remaining, 0.05)):
                return False

    def _source(self, source: Source) -> Iterable[Frame]:
        if isinstance(source, FrameSource):
            return source
        if isinstance(source, Image.Image):
            return ImageFrameSource(source, self.matrix.size)
        if isinstance(source, (str, Path)):
            path = Path(source)
            if path.suffix.lower() == ".gif":
                return GifFrameSource(path, self.matrix.size)
            if path.suffix.lower() in VIDEO_SUFFIXES:
                return VideoFrameSource(path, self.matrix.size)
            return ImageFrameSource(path, self.matrix.size)
        return source() if callable(source) else source

    def _show(self, image: Image.Image) -> None:
        self.matrix.show(image)
        self._frames_shown += 1

    def _normalize(self, image: Image.Image) -> Image.Image:
        image = image.convert("RGB")
        if image.size != self.matrix.size:
            image = image.resize(self.matrix.size, Image.Resampling.LANCZOS)
        return image

    def _transition(self, previous: Image.Image, image: Image.Image, item: PlaybackItem) -> bool:
        count = max(1, round(item.transition_duration * self.transition_fps))
        step = item.transition_duration / count
        deadline = self._now()
        for rendered in transition_frames(previous, image, item.transition, count):
            if not self._wait_ready():
                return False
            deadline += step
            if self._now() < deadline:
                self._show(rendered)
            if not self._delay(deadline - self._now()):
                return False
        self._show(image)
        return True

    def _play_item(
        self, item: PlaybackItem, previous: Optional[Image.Image]
    ) -> Optional[Image.Image]:
        source = self._source(item.source)
        limit = item.duration if item.play_by == PlayBy.TIME.value else None
        if limit is not None:
            limit = max(limit, float(getattr(source, "min_seconds", 0.0)))
        loops = item.repeats
        wants_transition = previous is not None and Transition(item.transition) is not Transition.CUT
        current = previous
        first = True
        passes = 0
        started = self._now()
        try:
            while True:
                produced = False
                frames: Iterator[Frame] = iter(source)
                try:
                    due = self._now()
                    ready_at = due
                    for frame in frames:
                        produced = True
                        if not self._wait_ready():
                            return current
                        # Time spent waiting for this frame shows whether the
                        # source itself (e.g. a heavy video decode) is the bottleneck.
                        decode_seconds = self._now() - ready_at
                        if limit is not None and self._now() - started >= limit:
                            return current
                        image = self._normalize(frame.image)
                        if first and wants_transition:
                            if not self._transition(previous, image, item):
                                return image
                            current = image
                            due = self._now()
                        elif self._now() < due + frame.duration or first:
                            # Frames that are already late are dropped so slow
                            # decodes keep real-time pace instead of slowing down.
                            self._show(image)
                            current = image
                        elif decode_seconds >= frame.duration or self._now() - due > RESYNC_SECONDS:
                            # The source is slower than real time, so dropping cannot
                            # catch up and would freeze the sign; show it and resync.
                            self._show(image)
                            current = image
                            due = self._now()
                        first = False
                        due += frame.duration
                        wait_until = due if limit is None else min(due, started + limit)
                        if not self._delay(wait_until - self._now()):
                            return current
                        ready_at = self._now()
                finally:
                    close = getattr(frames, "close", None)
                    if close is not None:
                        close()
                if not produced:
                    raise PlaybackError("no_frames", "item produced no frames")
                passes += 1
                if limit is not None:
                    if self._now() - started >= limit:
                        break
                elif passes >= loops:
                    break
        finally:
            if isinstance(source, FrameSource):
                source.close()
        return current

    @staticmethod
    def _order(count: int, mode: str, start: int) -> Iterator[int]:
        rng = random.Random()
        start = start % count
        if mode == PlayMode.RANDOM.value:
            index = start
            while True:
                yield index
                if count > 1:
                    index = rng.choice([candidate for candidate in range(count) if candidate != index])
        elif mode == PlayMode.SHUFFLE.value:
            rest = [index for index in range(count) if index != start]
            rng.shuffle(rest)
            sequence = [start] + rest
            while True:
                yield from sequence
                last = sequence[-1]
                sequence = list(range(count))
                rng.shuffle(sequence)
                if count > 1 and sequence[0] == last:
                    sequence[0], sequence[-1] = sequence[-1], sequence[0]
        else:
            index = start
            while True:
                yield index
                index += 1
                if index >= count:
                    if mode == PlayMode.ONCE.value:
                        return
                    index = 0

    def _warning_key(self, item: PlaybackItem) -> object:
        return item.item_id if item.item_id is not None else id(item)

    def _run(
        self,
        items: List[PlaybackItem],
        mode: str,
        start_index: int,
        stop_event: threading.Event,
        on_item: Optional[ItemCallback],
        on_finished: Optional[Callable[[], None]],
    ) -> None:
        self._local.stop = stop_event
        try:
            with self._condition:
                if self._state is RendererState.STARTING:
                    self._state = RendererState.PLAYING
            previous: Optional[Image.Image] = None
            empty_streak = 0
            for index in self._order(len(items), mode, start_index):
                if self._stopping():
                    break
                item = items[index]
                with self._condition:
                    self._current_item_id = item.item_id
                    self._current_index = index
                if on_item is not None:
                    try:
                        on_item(index, item)
                    except Exception:
                        logger.exception("playback_item_callback_failed")
                shown_before = self._frames_shown
                try:
                    previous = self._play_item(item, previous)
                    with self._condition:
                        self._warnings.pop(self._warning_key(item), None)
                except Exception as error:
                    # One unreadable item must never take the whole sign dark.
                    label = item.label or (f"item {item.item_id}" if item.item_id else "item")
                    with self._condition:
                        first_failure = self._warning_key(item) not in self._warnings
                        self._warnings[self._warning_key(item)] = f"Skipped {label}: {error}"
                    if first_failure:
                        logger.warning(
                            "playback_item_failed",
                            extra={"item_id": item.item_id, "error": str(error)},
                        )
                if self._stopping():
                    break
                if self._frames_shown == shown_before:
                    empty_streak += 1
                    if empty_streak >= len(items):
                        # Nothing in a whole pass could be shown; back off
                        # rather than spin, and keep retrying.
                        empty_streak = 0
                        if not self._delay(FAILED_PASS_BACKOFF_SECONDS):
                            break
                else:
                    empty_streak = 0
            if not self._stopping():
                with self._condition:
                    if self._thread is threading.current_thread():
                        self._state = RendererState.STOPPED
                        self._current_item_id = None
                        self._current_index = None
                self._safe_blank()
                if on_finished is not None:
                    try:
                        on_finished()
                    except Exception:
                        logger.exception("playback_finished_callback_failed")
        except BaseException as error:
            logger.exception("playback_worker_failed")
            with self._condition:
                if self._thread is threading.current_thread():
                    self._error = error
                    self._state = RendererState.ERROR
            self._safe_blank()
        finally:
            with self._condition:
                if self._thread is threading.current_thread():
                    self._thread = None


class PlaybackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnavailablePlaybackController:
    """Reports a hardware startup failure without taking down the web UI."""

    def __init__(self, message: str = "matrix hardware is unavailable") -> None:
        self._message = message
        self._changed_at = datetime.now(timezone.utc)

    def state(self) -> PlaybackState:
        return PlaybackState(
            status=PlaybackStatus.ERROR,
            changed_at=self._changed_at,
            error=self._message,
        )

    def _unavailable(self) -> PlaybackState:
        raise PlaybackError("matrix_unavailable", self._message)

    def start(self, playlist_id: int, from_start: bool = False) -> PlaybackState:
        return self._unavailable()

    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState:
        return self._unavailable()

    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState:
        return self._unavailable()

    def show_image(self, image: Image.Image, label: str) -> PlaybackState:
        return self._unavailable()

    def release(self) -> PlaybackState:
        return self._unavailable()

    def pause(self) -> PlaybackState:
        return self._unavailable()

    def resume(self) -> PlaybackState:
        return self._unavailable()

    def stop(self) -> PlaybackState:
        return self._unavailable()

    def close(self) -> None:
        pass


TEXT_COLUMNS = (
    "content", "font_family", "font_size", "bold", "italic", "alignment",
    "color_mode", "color_value", "outline", "outline_color", "border_style",
    "border_color", "effect", "accent_color", "background_color", "motion_speed",
)


def build_media_source(
    storage: MediaStorage,
    filename: Optional[str],
    size: tuple,
    duration: float,
    fit_mode: str,
    media_type: Optional[str] = None,
) -> FrameSource:
    if not filename:
        raise PlaybackError("media_not_found", "media record is missing")
    path = storage.media_path(filename)
    if not path.is_file():
        raise PlaybackError("media_file_missing", "media file is missing")
    suffix = path.suffix.lower()
    # A one-frame GIF is stored as a still picture and is held like one.
    if suffix == ".gif" and media_type != "image":
        return GifFrameSource(path, size, fit_mode)
    if suffix in VIDEO_SUFFIXES:
        return VideoFrameSource(storage.playable_path(filename), size, fit_mode=fit_mode)
    return ImageFrameSource(path, size, duration, fit_mode)


def build_text_source(
    storage: MediaStorage,
    values: Mapping[str, object],
    background_filename: Optional[str],
    size: tuple,
    duration: float,
) -> FrameSource:
    if values.get("content") is None:
        raise PlaybackError("text_panel_not_found", "text panel is missing")
    background = None
    if background_filename:
        background = load_background(storage.media_path(background_filename), size)
        if background is None:
            logger.warning("text_background_unavailable")
    return TextFrameSource(
        str(values["content"]), size, duration,
        style=style_from_mapping(values), background=background,
    )


def text_query(where: str) -> str:
    """Selects a text panel's style columns plus its background picture filename."""
    columns = ", ".join(f"tp.{column}" for column in TEXT_COLUMNS)
    return f"""SELECT tp.id, tp.name, {columns}, bg.filename AS background_filename
               FROM text_panels tp
               LEFT JOIN media bg ON bg.id = tp.background_media_id
               WHERE {where}"""


class DatabasePlaybackController:
    """Loads persisted playlists and controls the matrix render worker."""

    POSITION_SAVE_INTERVAL = 15.0

    def __init__(
        self, service: PlaybackService, database: Database, storage: MediaStorage
    ) -> None:
        self.service = service
        self.database = database
        self.storage = storage
        self._lock = threading.RLock()
        self._playlist_id: Optional[int] = None
        self._play_mode: Optional[str] = None
        self._item_count: Optional[int] = None
        self._source_type: Optional[str] = None
        self._source_label: Optional[str] = None
        self._resume_playlist_id: Optional[int] = None
        self._changed_at = datetime.now(timezone.utc)
        self._position: Optional[tuple] = None  # (playlist_id, index) awaiting save
        self._position_saved_at = 0.0

    @staticmethod
    def _transition(value: str) -> Transition:
        return Transition.CUT if value == "none" else Transition(value)

    def _media_builder(
        self, filename: Optional[str], duration: float, fit_mode: str, media_type: Optional[str] = None
    ) -> Callable[[], FrameSource]:
        """Defers file checks to play time so one missing file only skips its item."""
        return lambda: build_media_source(
            self.storage, filename, self.service.matrix.size, duration, fit_mode, media_type
        )

    def _text_builder(self, row: Mapping[str, object], duration: float) -> Callable[[], FrameSource]:
        values = {key: row[key] for key in TEXT_COLUMNS}
        background_filename = row["background_filename"]
        return lambda: build_text_source(
            self.storage, values, background_filename, self.service.matrix.size, duration
        )

    def _items(self, playlist_id: int) -> List[PlaybackItem]:
        columns = ", ".join(f"tp.{column}" for column in TEXT_COLUMNS)
        rows = self.database.fetch_all(
            f"""SELECT pi.*, m.filename, m.media_type, m.display_name AS media_name,
                       tp.name AS panel_name, {columns},
                       bg.filename AS background_filename
               FROM playlist_items pi
               LEFT JOIN media m ON m.id = pi.media_id
               LEFT JOIN text_panels tp ON tp.id = pi.text_panel_id
               LEFT JOIN media bg ON bg.id = tp.background_media_id
               WHERE pi.playlist_id = ? ORDER BY pi.position, pi.id""",
            (playlist_id,),
        )
        items: List[PlaybackItem] = []
        for row in rows:
            duration = row["duration_ms"] / 1000.0
            play_by = row["play_by"] or PlayBy.TIME.value
            if row["item_type"] == ItemType.MEDIA.value:
                source = self._media_builder(row["filename"], duration, row["fit_mode"], row["media_type"])
                label = row["media_name"]
                if row["media_type"] == "image":
                    play_by = PlayBy.TIME.value
            else:
                source = self._text_builder(row, duration)
                label = row["panel_name"]
                play_by = PlayBy.TIME.value
            items.append(
                PlaybackItem(
                    source=source,
                    duration=duration,
                    repeats=row["repeat_count"],
                    transition=self._transition(row["transition_type"]),
                    transition_duration=row["transition_duration_ms"] / 1000.0,
                    item_id=row["id"],
                    play_by=play_by,
                    label=f"“{label}”" if label else None,
                )
            )
        if not items:
            raise PlaybackError("empty_playlist", "playlist has no items")
        return items

    def _save_position(self, force: bool = False) -> None:
        with self._lock:
            pending = self._position
            if pending is None:
                return
            now = time.monotonic()
            if not force and now - self._position_saved_at < self.POSITION_SAVE_INTERVAL:
                return
            self._position = None
            self._position_saved_at = now
        playlist_id, index = pending
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE playlists SET resume_position = ? WHERE id = ?", (index, playlist_id)
                )
        except Exception:
            logger.exception("playlist_position_save_failed", extra={"playlist_id": playlist_id})

    def _item_started(self, playlist_id: int, index: int) -> None:
        with self._lock:
            self._position = (playlist_id, index)
        self._save_position()

    def _playlist_finished(self, playlist_id: int) -> None:
        with self._lock:
            self._position = (playlist_id, 0)
        self._save_position(force=True)

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
            playlist = self._source_type == "playlist" and not stopped
            return PlaybackState(
                status=status,
                playlist_id=self._playlist_id if not stopped else None,
                item_id=self.service.current_item_id,
                source_type=self._source_type if not stopped else None,
                source_label=self._source_label if not stopped else None,
                resume_playlist_id=self._resume_playlist_id if not stopped else None,
                position=self.service.current_index if playlist else None,
                item_count=self._item_count if playlist else None,
                play_mode=self._play_mode if playlist else None,
                changed_at=self._changed_at,
                error=str(error) if error is not None else None,
                warnings=self.service.warnings if not stopped else [],
            )

    def start(self, playlist_id: int, from_start: bool = False) -> PlaybackState:
        row = self.database.fetch_one(
            "SELECT name, play_mode, resume_position FROM playlists WHERE id = ?", (playlist_id,)
        )
        if row is None:
            raise PlaybackError("playlist_not_found", "playlist not found")
        items = self._items(playlist_id)
        self._save_position(force=True)
        start_index = 0 if from_start else int(row["resume_position"] or 0) % len(items)
        mode = row["play_mode"] or PlayMode.LOOP.value
        with self._lock:
            self._playlist_id = playlist_id
            self._play_mode = mode
            self._item_count = len(items)
            self._source_type = "playlist"
            self._source_label = row["name"]
            self._resume_playlist_id = None
            self._changed_at = datetime.now(timezone.utc)
        self.service.start(
            items,
            mode=mode,
            start_index=start_index,
            on_item=lambda index, _item: self._item_started(playlist_id, index),
            on_finished=lambda: self._playlist_finished(playlist_id),
        )
        return self.state()

    def send_media(
        self, media_id: int, fit_mode: str = "fit", duration_ms: Optional[int] = None
    ) -> PlaybackState:
        row = self.database.fetch_one(
            "SELECT filename, display_name, media_type FROM media WHERE id = ?", (media_id,)
        )
        if row is None:
            raise PlaybackError("media_not_found", "media not found")
        duration = (duration_ms or DEFAULT_SEND_DURATION_MS) / 1000.0
        source = self._media_builder(row["filename"], duration, fit_mode, row["media_type"])()
        # Stills hold for the duration; animations play whole loops back to back.
        play_by = PlayBy.TIME.value if row["media_type"] == "image" else PlayBy.REPEATS.value
        self._send(source, duration, "media", row["display_name"], play_by)
        return self.state()

    def send_text_panel(
        self, panel_id: int, duration_ms: Optional[int] = None
    ) -> PlaybackState:
        row = self.database.fetch_one(text_query("tp.id = ?"), (panel_id,))
        if row is None:
            raise PlaybackError("text_panel_not_found", "text panel not found")
        duration = (duration_ms or DEFAULT_SEND_DURATION_MS) / 1000.0
        source = self._text_builder(row, duration)()
        self._send(source, duration, "text_panel", row["name"], PlayBy.TIME.value)
        return self.state()

    def show_image(self, image: Image.Image, label: str) -> PlaybackState:
        """Holds a generated picture, such as the test card, until released."""
        source = ImageFrameSource(image, self.service.matrix.size, DEFAULT_SEND_DURATION_MS / 1000.0, "stretch")
        self._send(source, DEFAULT_SEND_DURATION_MS / 1000.0, "media", label, PlayBy.TIME.value)
        return self.state()

    def _send(
        self, source: Source, duration: float, source_type: str, label: str, play_by: str
    ) -> None:
        self._save_position(force=True)
        with self._lock:
            interrupted = self._playlist_id if self.service.is_running else None
            if interrupted is None and self._source_type in {"media", "text_panel"}:
                interrupted = self._resume_playlist_id
        item = PlaybackItem(
            source=source,
            duration=duration,
            repeats=1,
            transition=Transition.CUT,
            transition_duration=0.0,
            play_by=play_by,
            label=f"“{label}”",
        )
        with self._lock:
            self._playlist_id = None
            self._play_mode = None
            self._item_count = None
            self._source_type = source_type
            self._source_label = label
            self._resume_playlist_id = interrupted
            self._changed_at = datetime.now(timezone.utc)
        self.service.start([item], mode=PlayMode.LOOP.value)

    def release(self) -> PlaybackState:
        """Ends a direct send and resumes the playlist it interrupted where it left off."""
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
        self._save_position(force=True)
        self.service.stop()
        with self._lock:
            self._playlist_id = None
            self._play_mode = None
            self._item_count = None
            self._source_type = None
            self._source_label = None
            self._resume_playlist_id = None
            self._changed_at = datetime.now(timezone.utc)
        return self.state()

    def close(self) -> None:
        self._save_position(force=True)
        self.service.close()

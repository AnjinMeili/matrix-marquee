"""Fitted frames kept on disk: do the Fibonastretch work once, then just read it back.

Fitting an animation or video to the sign is per-frame work. A background worker
plays the live source once into a raw file of sign-size RGB frames, and later
plays read that file. Until a file is ready the live source plays as before, so
nothing waits. Files are derived, so they are never backed up and are pruned
oldest first past a size limit. The SD card takes one sequential write per
(media, sign size, fit) and nothing while a cached item plays.
"""

import hashlib
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

from PIL import Image

from .frames import Frame, FrameSource

logger = logging.getLogger(__name__)

# Bump when fitting changes how frames look, so older files are not reused.
FIT_VERSION = "1"
# One file may take at most this share of the limit; longer clips play live.
MAX_FILE_SHARE = 4


class CachedFrameSource(FrameSource):
    """Frames read straight from a cache file, one at a time."""

    def __init__(self, raw: Path, size: Tuple[int, int], durations: List[float]) -> None:
        self.raw = raw
        self.size = size
        self.durations = durations

    def __iter__(self) -> Iterator[Frame]:
        frame_bytes = self.size[0] * self.size[1] * 3
        with open(self.raw, "rb") as handle:
            for duration in self.durations:
                data = handle.read(frame_bytes)
                if len(data) != frame_bytes:
                    return
                yield Frame(Image.frombytes("RGB", self.size, data), duration)


class FitCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._queue: "queue.Queue[Tuple[str, Callable[[], FrameSource], Tuple[int, int]]]" = queue.Queue()
        self._pending: Set[str] = set()
        self._touched: Set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # Keys and paths -------------------------------------------------------

    @staticmethod
    def key(stem: str, source: Path, size: Tuple[int, int], *settings: str) -> str:
        """Stable per media file; changes with the file, the sign size, or the fit."""
        info = source.stat()
        digest = hashlib.sha1(
            "|".join([FIT_VERSION, source.name, str(info.st_mtime_ns), str(info.st_size),
                      "x".join(map(str, size)), *settings]).encode()
        ).hexdigest()[:16]
        return f"{stem}-{digest}"

    def _raw(self, key: str) -> Path:
        return self.directory / f"{key}.rgb"

    def _meta(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def _skip(self, key: str) -> Path:
        return self.directory / f"{key}.skip"

    # Reading ----------------------------------------------------------------

    def get(self, key: str, size: Tuple[int, int]) -> Optional[CachedFrameSource]:
        """The cached source if a complete file exists; the metadata is written last."""
        try:
            meta = json.loads(self._meta(key).read_text())
            if tuple(meta["size"]) != tuple(size):
                return None
            durations = [float(d) for d in meta["durations"]]
            if self._raw(key).stat().st_size != len(durations) * size[0] * size[1] * 3:
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        self._touch(key)
        return CachedFrameSource(self._raw(key), size, durations)

    def _touch(self, key: str) -> None:
        """Marks a file as recently used, once per run, so pruning drops the unused first."""
        if key in self._touched:
            return
        self._touched.add(key)
        try:
            os.utime(self._meta(key))
        except OSError:
            pass

    def is_skipped(self, key: str) -> bool:
        return self._skip(key).exists()

    # Building ---------------------------------------------------------------

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for leftover in self.directory.glob("*.part"):
            leftover.unlink(missing_ok=True)
        self._thread = threading.Thread(target=self._run, name="fit-cache", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def submit(self, key: str, make_source: Callable[[], FrameSource], size: Tuple[int, int]) -> bool:
        """Queues one build, once; False if it is already queued, built, or given up on."""
        if self._thread is None or self.is_skipped(key) or self.get(key, size) is not None:
            return False
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
        self._queue.put((key, make_source, size))
        return True

    def _run(self) -> None:
        # Linux niceness is per thread and ffmpeg inherits it, so playback keeps priority.
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
        while not self._stop.is_set():
            try:
                key, make_source, size = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            started = time.monotonic()
            try:
                outcome = self._build(key, make_source, size)
                logger.info("fit_cache_" + outcome, extra={"seconds": round(time.monotonic() - started, 1)})
            except Exception:
                logger.exception("fit_cache_failed")
            finally:
                with self._lock:
                    self._pending.discard(key)

    def _build(self, key: str, make_source: Callable[[], FrameSource], size: Tuple[int, int]) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        raw, meta = self._raw(key), self._meta(key)
        raw_part, meta_part = raw.with_name(raw.name + ".part"), meta.with_name(meta.name + ".part")
        limit = self.max_bytes // MAX_FILE_SHARE
        frame_bytes = size[0] * size[1] * 3
        durations: List[float] = []
        source = make_source()
        try:
            with open(raw_part, "wb") as handle:
                for frame in source:
                    if frame.image.size != size:
                        raise ValueError("frame is not sign size")
                    if (len(durations) + 1) * frame_bytes > limit:
                        raw_part.unlink(missing_ok=True)
                        self._skip(key).touch()
                        return "skipped"
                    handle.write(frame.image.convert("RGB").tobytes())
                    durations.append(frame.duration)
                    if self._stop.is_set():
                        raw_part.unlink(missing_ok=True)
                        return "stopped"
        except Exception:
            raw_part.unlink(missing_ok=True)
            raise
        finally:
            source.close()
        if not durations:
            raw_part.unlink(missing_ok=True)
            return "empty"
        meta_part.write_text(json.dumps({"size": list(size), "durations": durations}))
        os.replace(raw_part, raw)
        os.replace(meta_part, meta)
        self.prune(keep=key)
        return "built"

    # Housekeeping ---------------------------------------------------------------

    def prune(self, keep: Optional[str] = None) -> None:
        """Deletes the least recently used files until the cache fits its limit."""
        entries: Dict[str, Tuple[float, int]] = {}
        for meta in self.directory.glob("*.json"):
            key = meta.stem
            try:
                entries[key] = (meta.stat().st_mtime, self._raw(key).stat().st_size + meta.stat().st_size)
            except OSError:
                continue
        total = sum(size for _, size in entries.values())
        for key, (_, size) in sorted(entries.items(), key=lambda item: item[1][0]):
            if total <= self.max_bytes:
                break
            if key == keep:
                continue
            self.remove(key)
            total -= size

    def remove(self, key: str) -> None:
        for path in (self._meta(key), self._raw(key), self._skip(key)):
            path.unlink(missing_ok=True)

    def remove_media(self, stem: str) -> None:
        """Drops every file made from one media file, when that media is deleted."""
        if self.directory.is_dir():
            for path in self.directory.glob(f"{stem}-*"):
                path.unlink(missing_ok=True)

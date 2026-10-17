"""Background conversion of uploaded videos into Pi-friendly playback copies."""

import logging
import queue
import threading
import time
from typing import Iterable, Optional

from .storage import MediaStorage

logger = logging.getLogger(__name__)


class VideoProxyWorker:
    """Converts one video at a time off the request and render paths."""

    def __init__(self, storage: MediaStorage) -> None:
        self.storage = storage
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._pending: set = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="video-proxy", daemon=True)
        self._thread.start()

    def submit(self, filename: str) -> None:
        with self._lock:
            if filename in self._pending:
                return
            self._pending.add(filename)
        self._queue.put(filename)

    def submit_missing(self, filenames: Iterable[str]) -> None:
        for filename in filenames:
            if not self.storage.proxy_path(filename).is_file():
                self.submit(filename)

    def is_pending(self, filename: str) -> bool:
        with self._lock:
            return filename in self._pending

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                filename = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            started = time.monotonic()
            try:
                ready = self.storage.build_proxy(filename)
                logger.info(
                    "video_proxy_built" if ready else "video_proxy_unavailable",
                    extra={"seconds": round(time.monotonic() - started, 1)},
                )
            except Exception:
                logger.exception("video_proxy_failed")
            finally:
                with self._lock:
                    self._pending.discard(filename)

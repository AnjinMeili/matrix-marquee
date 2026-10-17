"""Per-item usage counters: the raw signal for ratings inferred from use.

Counts how each message and picture or video is used (plays, seconds on the
sign, "Show now" sends, edits, quick removals from playlists) as daily
totals. Counts are gathered in memory and written every few minutes so the
SD card is not written on every play. Nothing about viewers is recorded.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

from .database import Database

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 300.0
KEEP_DAYS = 400
QUICK_REMOVAL_SECONDS = 24 * 3600  # dropped within a day of being added
KINDS = ("media", "text_panel")

Key = Tuple[str, str, int]  # (day, kind, item id)


@dataclass
class _Counts:
    plays: int = 0
    seconds: float = 0.0
    sends: int = 0
    edits: int = 0
    quick_removals: int = 0


def _today(clock: Callable[[], datetime]) -> str:
    return clock().strftime("%Y-%m-%d")


class UsageRecorder:
    def __init__(
        self,
        database: Database,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.database = database
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: Dict[Key, _Counts] = defaultdict(_Counts)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="usage-flush", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.flush()

    def _run(self) -> None:
        while not self._stop.wait(FLUSH_SECONDS):
            try:
                self.flush()
            except Exception:
                logger.exception("usage_flush_failed")

    def _bump(self, kind: str, item_id: Optional[int], **amounts: float) -> None:
        if kind not in KINDS or not item_id:
            return
        with self._lock:
            counts = self._pending[(_today(self.clock), kind, int(item_id))]
            for field, amount in amounts.items():
                setattr(counts, field, getattr(counts, field) + amount)

    def record_play(self, kind: str, item_id: Optional[int], seconds: float) -> None:
        """One appearance on the sign; very short ones add time but not a play."""
        self._bump(kind, item_id, plays=1 if seconds >= 1.0 else 0, seconds=max(0.0, seconds))

    def record_send(self, kind: str, item_id: Optional[int]) -> None:
        self._bump(kind, item_id, sends=1)

    def record_edit(self, kind: str, item_id: Optional[int]) -> None:
        self._bump(kind, item_id, edits=1)

    def record_removal(self, kind: str, item_id: Optional[int], added_at: Optional[str]) -> None:
        """Counts removals from a playlist soon after the item was added."""
        if not added_at:
            return
        try:
            added = datetime.fromisoformat(added_at.replace(" ", "T")).replace(tzinfo=timezone.utc)
        except ValueError:
            return
        if (self.clock() - added).total_seconds() <= QUICK_REMOVAL_SECONDS:
            self._bump(kind, item_id, quick_removals=1)

    def flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, defaultdict(_Counts)
        if not pending:
            return
        with self.database.transaction() as connection:
            for (day, kind, item_id), counts in pending.items():
                connection.execute(
                    """INSERT INTO usage_daily (day, kind, item_id, plays, seconds, sends, edits, quick_removals)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(day, kind, item_id) DO UPDATE SET
                         plays = plays + excluded.plays,
                         seconds = seconds + excluded.seconds,
                         sends = sends + excluded.sends,
                         edits = edits + excluded.edits,
                         quick_removals = quick_removals + excluded.quick_removals""",
                    (day, kind, item_id, counts.plays, round(counts.seconds, 1),
                     counts.sends, counts.edits, counts.quick_removals),
                )
            connection.execute(
                "DELETE FROM usage_daily WHERE day < date(?, ?)",
                (_today(self.clock), f"-{KEEP_DAYS} days"),
            )

    def totals(self, days: int = 30) -> List[dict]:
        """Totals per item over the last ``days``, including counts not yet written."""
        self.flush()
        rows = self.database.fetch_all(
            """SELECT kind, item_id, SUM(plays) AS plays, SUM(seconds) AS seconds,
                      SUM(sends) AS sends, SUM(edits) AS edits,
                      SUM(quick_removals) AS quick_removals, MAX(day) AS last_day
               FROM usage_daily WHERE day >= date(?, ?)
               GROUP BY kind, item_id ORDER BY sends DESC, plays DESC""",
            (_today(self.clock), f"-{max(1, days) - 1} days"),
        )
        return [dict(row) for row in rows]

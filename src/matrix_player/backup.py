"""Whole-sign backup packaged as one zip for the requesting device to save.

The archive holds a consistent database snapshot, every original picture and
video, thumbnails, and a manifest. It deliberately leaves out configuration
files and credentials, and the video playback copies, which are rebuilt
automatically from the originals.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
from typing import Dict, Iterator, Tuple
import zipfile

from . import __version__
from .config import Settings
from .storage import PROXY_SUFFIX

ARCHIVE_ROOT = "protest-backup"
HEADROOM_BYTES = 100 * 1024 * 1024

README = """Protest sign backup
===================

  manifest.json             what is in this backup and when it was made
  database/{database}       messages, playlists, tags, and play positions
  media/                    every original picture and video
  thumbnails/               library thumbnails

Not included: configuration and Wi-Fi passwords, and the small video
playback copies, which the sign rebuilds from the originals by itself.

To restore onto a sign, stop the service, copy database/ and media/ (and
optionally thumbnails/) over the matching paths under {data_dir}, then
start the service again.
"""

_lock = threading.Lock()


class BackupBusyError(RuntimeError):
    pass


class BackupSpaceError(RuntimeError):
    pass


@dataclass
class Backup:
    path: Path
    filename: str
    size_bytes: int
    counts: Dict[str, int]


def _media_files(settings: Settings) -> Iterator[Path]:
    for path in sorted(settings.media_dir.iterdir()):
        name = path.name
        if not path.is_file() or name.startswith(".") or name.endswith((PROXY_SUFFIX, ".part")):
            continue
        yield path


def _thumbnails(settings: Settings) -> Iterator[Path]:
    if settings.thumbnail_dir.is_dir():
        yield from (path for path in sorted(settings.thumbnail_dir.iterdir()) if path.is_file())


def estimate_bytes(settings: Settings) -> int:
    return sum(path.stat().st_size for path in _media_files(settings)) + sum(
        path.stat().st_size for path in _thumbnails(settings)
    ) + (settings.database_path.stat().st_size if settings.database_path.exists() else 0)


def _snapshot_database(settings: Settings, target: Path) -> Dict[str, int]:
    """Copies the live database consistently, even while the player writes to it."""
    source = sqlite3.connect(str(settings.database_path))
    copy = sqlite3.connect(str(target))
    try:
        source.backup(copy)
        counts = {
            table: copy.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("media", "text_panels", "playlists", "playlist_items", "tags")
        }
    finally:
        copy.close()
        source.close()
    return counts


def create_backup(settings: Settings, matrix_size: Tuple[int, int]) -> Backup:
    """Builds the zip on the data disk (not RAM-backed /tmp); the caller deletes it."""
    if not _lock.acquire(blocking=False):
        raise BackupBusyError("a backup is already being made")
    try:
        needed = estimate_bytes(settings) + HEADROOM_BYTES
        if shutil.disk_usage(settings.data_dir).free < needed:
            raise BackupSpaceError("not enough free storage on the sign to build the backup")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"protest-backup-{stamp}.zip"
        work = Path(tempfile.mkdtemp(prefix=".backup-", dir=settings.data_dir))
        archive = settings.data_dir / f".{filename}"
        try:
            database_copy = work / settings.database_path.name
            counts = _snapshot_database(settings, database_copy)
            media = list(_media_files(settings))
            thumbnails = list(_thumbnails(settings))
            counts["media_files"] = len(media)
            manifest = {
                "format": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "app_version": __version__,
                "matrix": {"width": matrix_size[0], "height": matrix_size[1]},
                "counts": counts,
                "media_bytes": sum(path.stat().st_size for path in media),
                "excluded": ["configuration and credentials", "video playback copies"],
            }
            with zipfile.ZipFile(archive, "w", allowZip64=True) as bundle:
                deflate = {"compress_type": zipfile.ZIP_DEFLATED}
                bundle.writestr(f"{ARCHIVE_ROOT}/manifest.json", json.dumps(manifest, indent=2), **deflate)
                bundle.writestr(
                    f"{ARCHIVE_ROOT}/README.txt",
                    README.format(database=settings.database_path.name, data_dir=settings.data_dir),
                    **deflate,
                )
                bundle.write(database_copy, f"{ARCHIVE_ROOT}/database/{settings.database_path.name}", **deflate)
                # Pictures and video are already compressed; storing them keeps the Pi fast.
                for path in media:
                    bundle.write(path, f"{ARCHIVE_ROOT}/media/{path.name}", compress_type=zipfile.ZIP_STORED)
                for path in thumbnails:
                    bundle.write(path, f"{ARCHIVE_ROOT}/thumbnails/{path.name}", compress_type=zipfile.ZIP_STORED)
            os.chmod(archive, 0o600)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return Backup(archive, filename, archive.stat().st_size, counts)
    finally:
        _lock.release()


def remove_stale_archives(settings: Settings) -> None:
    """Clears archives left behind if the service stopped mid-download."""
    for path in settings.data_dir.glob(".protest-backup-*.zip"):
        path.unlink(missing_ok=True)
    for path in settings.data_dir.glob(".backup-*"):
        shutil.rmtree(path, ignore_errors=True)

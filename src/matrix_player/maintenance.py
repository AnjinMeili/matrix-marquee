"""One-off library checks that run in the background after startup."""

import logging
import threading
import time

from PIL import Image

from .config import Settings
from .database import Database
from .storage import MediaStorage, gif_animation

logger = logging.getLogger(__name__)

# Bump the version to re-run the check after changing how GIFs are classified.
GIF_CHECK_MARKER = ".gif-animation-check-v1"


def check_gif_animation(database: Database, storage: MediaStorage, settings: Settings) -> int:
    """Reclassifies one-frame GIFs as still pictures and corrects animated GIF loop
    lengths for media uploaded before uploads were checked. Returns how many
    GIFs turned out to be still pictures."""
    marker = settings.data_dir / GIF_CHECK_MARKER
    if marker.exists():
        return 0
    still = 0
    rows = database.fetch_all("SELECT id, filename FROM media WHERE media_type = 'gif' ORDER BY id")
    for row in rows:
        try:
            with Image.open(storage.media_path(row["filename"])) as image:
                animation = gif_animation(image)
        except (OSError, ValueError):
            continue
        with database.transaction() as connection:
            if animation is None:
                connection.execute(
                    """UPDATE media SET media_type = 'image', duration_ms = NULL, frame_rate = NULL,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (row["id"],),
                )
                still += 1
            else:
                connection.execute(
                    "UPDATE media SET duration_ms = ?, frame_rate = ? WHERE id = ?",
                    (animation[0], animation[1], row["id"]),
                )
        time.sleep(0.02)  # leave the render thread room while decoding large GIFs
    marker.write_text("checked\n")
    return still


def start_library_checks(database: Database, storage: MediaStorage, settings: Settings) -> None:
    def run() -> None:
        try:
            still = check_gif_animation(database, storage, settings)
            if still:
                logger.info("still_gifs_reclassified", extra={"count": still})
        except Exception:
            logger.exception("library_check_failed")

    threading.Thread(target=run, name="library-checks", daemon=True).start()

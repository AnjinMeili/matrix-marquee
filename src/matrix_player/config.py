"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from pathlib import Path
import os


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    media_dir: Path
    thumbnail_dir: Path
    max_upload_bytes: int
    matrix_width: int
    matrix_height: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("MATRIX_PLAYER_DATA_DIR", "./data")).expanduser().resolve()
        return cls(
            data_dir=data_dir,
            database_path=Path(
                os.getenv("MATRIX_PLAYER_DATABASE", str(data_dir / "matrix-player.sqlite3"))
            ).expanduser().resolve(),
            media_dir=Path(
                os.getenv("MATRIX_PLAYER_MEDIA_DIR", str(data_dir / "media"))
            ).expanduser().resolve(),
            thumbnail_dir=Path(
                os.getenv("MATRIX_PLAYER_THUMBNAIL_DIR", str(data_dir / "thumbnails"))
            ).expanduser().resolve(),
            max_upload_bytes=_positive_int("MATRIX_PLAYER_MAX_UPLOAD_BYTES", 250 * 1024 * 1024),
            matrix_width=_positive_int("MATRIX_PLAYER_MATRIX_WIDTH", 96),
            matrix_height=_positive_int("MATRIX_PLAYER_MATRIX_HEIGHT", 64),
            log_level=os.getenv("MATRIX_PLAYER_LOG_LEVEL", "INFO").upper(),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.database_path.parent, self.media_dir, self.thumbnail_dir):
            path.mkdir(parents=True, exist_ok=True)

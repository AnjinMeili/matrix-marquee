"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from pathlib import Path
import os


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _matrix_mode() -> str:
    mode = os.getenv("MATRIX_PLAYER_MATRIX_MODE", "mock").strip().lower()
    if mode not in {"mock", "hardware"}:
        raise ValueError("MATRIX_PLAYER_MATRIX_MODE must be 'mock' or 'hardware'")
    return mode


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
    matrix_mode: str = "mock"
    matrix_rows: int = 32
    matrix_cols: int = 64
    matrix_chain_length: int = 1
    matrix_parallel: int = 3
    matrix_multiplexing: int = 1
    matrix_pixel_mapper: str = "Rotate:90"
    matrix_pwm_lsb_nanoseconds: int = 160
    matrix_gpio_slowdown: int = 2

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
            matrix_mode=_matrix_mode(),
            matrix_rows=_positive_int("MATRIX_ROWS", 32),
            matrix_cols=_positive_int("MATRIX_COLS", 64),
            matrix_chain_length=_positive_int("MATRIX_CHAIN_LENGTH", 1),
            matrix_parallel=_positive_int("MATRIX_PARALLEL", 3),
            matrix_multiplexing=_positive_int("MATRIX_MULTIPLEXING", 1),
            matrix_pixel_mapper=os.getenv("MATRIX_PIXEL_MAPPER", "Rotate:90"),
            matrix_pwm_lsb_nanoseconds=_positive_int("MATRIX_PWM_LSB_NANOSECONDS", 160),
            matrix_gpio_slowdown=_positive_int("MATRIX_GPIO_SLOWDOWN", 2),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.database_path.parent, self.media_dir, self.thumbnail_dir):
            path.mkdir(parents=True, exist_ok=True)

"""Panel brightness and gamma, saved across restarts, plus a calibration test card.

Outdoor high-brightness 1/8-scan panels hold each pixel's light longer than
indoor panels, so at full brightness and a linear curve they wash out and
smear moving pictures. Raising gamma (darker mid-tones) or lowering
brightness cures it; the test card shows when the dark steps stay distinct.
"""

from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw

from .database import Database

DEFAULT_BRIGHTNESS = 100
DEFAULT_GAMMA = 1.0
MIN_BRIGHTNESS = 5
MAX_GAMMA = 3.0


@dataclass(frozen=True)
class Correction:
    brightness: int = DEFAULT_BRIGHTNESS  # percent, applied by the driver
    gamma: float = DEFAULT_GAMMA  # 1.0 leaves the picture as rendered

    @property
    def is_identity(self) -> bool:
        return self.brightness == 100 and abs(self.gamma - 1.0) < 1e-6


def gamma_table(gamma: float, scale: float = 1.0) -> List[int]:
    """A Pillow point() table for all three channels: out = 255 * (in/255)^gamma * scale."""
    band = [min(255, round(255 * ((value / 255) ** gamma) * scale)) for value in range(256)]
    return band * 3


def load(database: Database) -> Correction:
    rows = {
        row["key"]: row["value"]
        for row in database.fetch_all(
            "SELECT key, value FROM settings WHERE key IN ('display.brightness', 'display.gamma')"
        )
    }
    try:
        return Correction(
            brightness=int(rows.get("display.brightness", DEFAULT_BRIGHTNESS)),
            gamma=float(rows.get("display.gamma", DEFAULT_GAMMA)),
        )
    except ValueError:
        return Correction()


def save(database: Database, correction: Correction) -> None:
    with database.transaction() as connection:
        for key, value in (("display.brightness", correction.brightness), ("display.gamma", correction.gamma)):
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )


def test_card(size: Tuple[int, int]) -> Image.Image:
    """Grey and colour ramps, dark steps, and hard edges for tuning a panel.

    The darkest steps merging together, or bright edges trailing into black,
    are the signs of a panel that wants more gamma or less brightness."""
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    band = max(4, height // 8)
    steps = 16
    for index in range(steps):
        left = index * width // steps
        right = (index + 1) * width // steps - 1
        level = round(255 * index / (steps - 1))
        draw.rectangle((left, 0, right, band - 1), fill=(level, level, level))
        dark = round(48 * index / (steps - 1))  # the low end, where smear and crush show first
        draw.rectangle((left, band, right, 2 * band - 1), fill=(dark, dark, dark))
    for row, channel in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1))):
        top = (2 + row) * band
        for x in range(width):
            level = round(255 * x / max(1, width - 1))
            draw.line((x, top, x, top + band - 1), fill=tuple(level * c for c in channel))
    top = 5 * band
    half = width // 2
    draw.rectangle((0, top, half - 1, height - 1), fill=(255, 255, 255))
    draw.rectangle((half // 4, top + band // 2, half - 1 - half // 4, height - 1 - band // 2), fill=(0, 0, 0))
    for x in range(half + 2, width - 1, 4):
        draw.line((x, top + 1, x, height - 2), fill=(255, 255, 255))
    return image

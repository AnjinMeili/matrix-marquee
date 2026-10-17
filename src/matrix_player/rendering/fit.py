from enum import Enum
from typing import Tuple, Union

from PIL import Image, ImageColor


class FitMode(str, Enum):
    FIT = "fit"
    CROP = "crop"
    LETTERBOX = "letterbox"
    STRETCH = "stretch"


def fit_image(
    image: Image.Image,
    size: Tuple[int, int],
    mode: Union[FitMode, str] = FitMode.LETTERBOX,
    background: Union[str, Tuple[int, int, int]] = "black",
) -> Image.Image:
    """Return an RGB image normalized to *size* without mutating the input."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("size dimensions must be positive")
    selected = FitMode(mode)
    source = image.convert("RGB")
    if selected is FitMode.STRETCH:
        return source.resize(size, Image.Resampling.LANCZOS)

    scale = (
        max(width / source.width, height / source.height)
        if selected is FitMode.CROP
        else min(width / source.width, height / source.height)
    )
    resized_size = (
        max(1, round(source.width * scale)),
        max(1, round(source.height * scale)),
    )
    resized = source.resize(resized_size, Image.Resampling.LANCZOS)
    if selected is FitMode.CROP:
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        return resized.crop((left, top, left + width, top + height))

    canvas = Image.new("RGB", size, ImageColor.getrgb(background))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas

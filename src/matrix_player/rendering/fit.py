from enum import Enum
from typing import Tuple, Union

import numpy as np
from PIL import Image, ImageColor

from .fibonacci import DEFAULT_BLUR, fibonacci_fit_for


def pixel_unit(size: Tuple[int, int]) -> int:
    """Whole pixels that match one pixel on a 64-pixel-tall sign, for borders and shakes.

    Signs come in any size, so fixed pixel amounts would vanish on large ones.
    """
    return max(1, round(size[1] / 64))


class FitMode(str, Enum):
    FIT = "fit"
    CROP = "crop"
    LETTERBOX = "letterbox"
    STRETCH = "stretch"
    FIBONACCI = "fibonacci"
    MIRROR = "mirror"

    @classmethod
    def _missing_(cls, value: object):
        # "tile" shipped for a few hours before it became "mirror"; saved items still say it.
        return cls.MIRROR if value == "tile" else None


def contained_size(source: Tuple[int, int], size: Tuple[int, int]) -> Tuple[int, int]:
    """The source scaled to sit inside *size* with its shape kept."""
    scale = min(size[0] / source[0], size[1] / source[1])
    return (
        min(size[0], max(1, round(source[0] * scale))),
        min(size[1], max(1, round(source[1] * scale))),
    )


def _mirrored_index(count: int, offset: int, length: int) -> np.ndarray:
    """Source index for each output position: a reflection at both ends of the picture,
    repeated back and forth if the bars are wider than the picture."""
    position = (np.arange(length) - offset) % (2 * count)
    return np.where(position < count, position, 2 * count - 1 - position)


def mirror_fill(picture: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Each bar is its own side of the picture reflected about the edge, like a window
    cut from [mirrored copy | picture | mirrored copy], with the edge pixel doubled."""
    width, height = picture.size
    if (width, height) == tuple(size):
        return picture
    array = np.asarray(picture.convert("RGB"))
    rows = _mirrored_index(height, (size[1] - height) // 2, size[1])
    columns = _mirrored_index(width, (size[0] - width) // 2, size[0])
    return Image.fromarray(np.ascontiguousarray(array[rows][:, columns]))


def fill_bars(
    picture: Image.Image, size: Tuple[int, int], mode: Union[FitMode, str], blur: str = DEFAULT_BLUR
) -> Image.Image:
    """Fills the bars around a picture already fitted inside *size*."""
    if FitMode(mode) is FitMode.MIRROR:
        return mirror_fill(picture, size)
    return fibonacci_fit_for(picture.size, size).apply(picture, blur)


def fit_image(
    image: Image.Image,
    size: Tuple[int, int],
    mode: Union[FitMode, str] = FitMode.LETTERBOX,
    background: Union[str, Tuple[int, int, int]] = "black",
    blur: str = DEFAULT_BLUR,
) -> Image.Image:
    """Return an RGB image normalized to *size* without mutating the input."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("size dimensions must be positive")
    selected = FitMode(mode)
    source = image.convert("RGB")
    if selected is FitMode.STRETCH:
        return source.resize(size, Image.Resampling.LANCZOS)

    if selected in (FitMode.FIBONACCI, FitMode.MIRROR):
        picture = source.resize(contained_size(source.size, size), Image.Resampling.LANCZOS)
        return fill_bars(picture, size, selected, blur)

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

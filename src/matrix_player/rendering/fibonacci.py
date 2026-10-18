"""Fibonastretch: fill the bars around a picture by stretching its edge pixels.

The picture is first fitted inside the sign. On the short axis the remaining gap
is split between the two edges, and each edge gets a strip made from the
picture's outermost pixel lines, each widened by a Fibonacci number: the
outermost line is the widest and the strip meets the picture at width 1. A
strip is built from the fewest terms whose sum covers its gap, so it usually
reaches past the gap and lies over the first lines of the picture; there the
strip and the picture are combined with a blend operator.
"""

from functools import lru_cache
from typing import Callable, Dict, List, Tuple

import numpy as np
from PIL import Image

DEFAULT_BLEND = "screen"


def _screen(b: np.ndarray, s: np.ndarray) -> np.ndarray:
    return 1.0 - (1.0 - b) * (1.0 - s)


def _overlay(b: np.ndarray, s: np.ndarray) -> np.ndarray:
    return np.where(b < 0.5, 2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s))


# Each operator takes the picture beneath and the strip above, as floats in 0..1.
BLENDS: Dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "screen": _screen,
    "additive": lambda b, s: np.minimum(1.0, b + s),
    "multiply": lambda b, s: b * s,
    "subtractive": lambda b, s: np.maximum(0.0, b - s),
    "overlay": _overlay,
    "lighten": np.maximum,
    "darken": np.minimum,
    "difference": lambda b, s: np.abs(b - s),
    "average": lambda b, s: (b + s) / 2.0,
    "normal": lambda b, s: s,
}


def blend_names() -> List[str]:
    return list(BLENDS)


def fibonacci_widths(gap: int, limit: int) -> List[int]:
    """Strip widths from the outermost source line inward; they sum to at least *gap*.

    Uses the fewest terms of 1, 1, 2, 3, 5, ... that cover the gap. If that needs
    more source lines than *limit*, the outermost line takes the shortfall.
    """
    if gap <= 0:
        return []
    terms = [1]
    while sum(terms) < gap and len(terms) < limit:
        terms.append(terms[-1] + (terms[-2] if len(terms) > 1 else 0))
    widths = terms[::-1]
    if sum(widths) < gap:
        widths[0] += gap - sum(widths)
    return widths


class _Strip:
    """One edge's source-line index for every strip pixel, edge first."""

    def __init__(self, gap: int, picture: int) -> None:
        self.gap = gap
        widths = fibonacci_widths(gap, max(1, picture // 2))
        index = np.repeat(np.arange(len(widths)), widths) if widths else np.zeros(0, dtype=int)
        # A strip never runs past the far side of the picture.
        self.index = index[: gap + picture]

    @property
    def length(self) -> int:
        return len(self.index)


class FibonacciFit:
    """The geometry for one (fitted picture size, sign size); build once, apply per frame."""

    def __init__(self, picture: Tuple[int, int], size: Tuple[int, int]) -> None:
        self.picture = picture
        self.size = size
        self.vertical = picture[0] >= size[0] and picture[1] < size[1]
        width, height = (size[1], size[0]) if self.vertical else size
        across = picture[1] if self.vertical else picture[0]
        self.gap = max(0, width - across)
        self.near = self.gap // 2
        self.far = self.gap - self.near
        self._strips = (_Strip(self.near, across), _Strip(self.far, across))

    def apply(self, picture: Image.Image, blend: str = DEFAULT_BLEND) -> Image.Image:
        if picture.size != self.picture:
            raise ValueError("picture does not match the fitted size")
        array = np.asarray(picture.convert("RGB"))
        if self.vertical:
            array = array.transpose(1, 0, 2)
        out = np.zeros((array.shape[0], array.shape[1] + self.gap, 3), dtype=np.uint8)
        out[:, self.near:self.near + array.shape[1]] = array
        operator = BLENDS.get(blend, BLENDS[DEFAULT_BLEND])
        # The far edge is the near edge seen in a mirror.
        self._paint(out, array, self._strips[0], self.near, operator)
        self._paint(out[:, ::-1], array[:, ::-1], self._strips[1], self.far, operator)
        if self.vertical:
            out = out.transpose(1, 0, 2)
        return Image.fromarray(np.ascontiguousarray(out))

    @staticmethod
    def _paint(out: np.ndarray, picture: np.ndarray, strip: _Strip, offset: int, operator) -> None:
        if strip.length == 0:
            return
        pixels = picture[:, strip.index]
        if strip.length <= offset:
            out[:, :strip.length] = pixels
            return
        out[:, :offset] = pixels[:, :offset]
        under = out[:, offset:strip.length].astype(np.float32) / 255.0
        over = pixels[:, offset:].astype(np.float32) / 255.0
        mixed = np.clip(operator(under, over), 0.0, 1.0)
        out[:, offset:strip.length] = np.rint(mixed * 255.0).astype(np.uint8)


@lru_cache(maxsize=16)
def fibonacci_fit_for(picture: Tuple[int, int], size: Tuple[int, int]) -> FibonacciFit:
    return FibonacciFit(picture, size)

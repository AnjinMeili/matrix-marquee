"""Fibonastretch: fill the bars around a picture by stretching its edge pixels.

The picture is first fitted inside the sign. On the short axis the remaining gap
is split between the two edges. Each edge's bar is built from the picture's own
outermost lines. The line at the picture's edge is drawn 1 pixel wide, the next
1, then 2, 3, 5, ... so the bar continues the picture's edge colour and is
widest at the sign's edge. The last step is cut where the gap ends.

A pixel grows in both directions as it steps outward: a step *n* wide turns each
pixel into an *n* by *n* square. Neighbouring squares overlap and are combined
with a blend operator, so the bar blurs more and more toward the sign's edge.
"""

from functools import lru_cache
from typing import List, Tuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

DEFAULT_BLUR = "average"

# Each operator combines the overlapping pixels (last axis) as floats in 0..1.
BLURS = {
    "average": lambda a: a.mean(axis=-1),
    "lighten": lambda a: a.max(axis=-1),
    "screen": lambda a: 1.0 - np.prod(1.0 - a, axis=-1),
    "additive": lambda a: np.minimum(1.0, a.sum(axis=-1)),
    "multiply": lambda a: np.prod(a, axis=-1),
    "darken": lambda a: a.min(axis=-1),
}


def blur_names() -> List[str]:
    return list(BLURS)


def _terms(gap: int, limit: int) -> List[int]:
    """Fibonacci steps (1, 1, 2, 3, ...) that cover *gap*, the last one uncut."""
    if gap <= 0:
        return []
    terms = [1]
    while sum(terms) < gap and len(terms) < limit:
        terms.append(terms[-1] + (terms[-2] if len(terms) > 1 else 0))
    if sum(terms) < gap:
        terms[-1] += gap - sum(terms)
    return terms


def fibonacci_widths(gap: int, limit: int) -> List[int]:
    """Strip widths from the picture's edge outward; they sum to exactly *gap*.

    Source line *i* from the picture's edge is drawn 1, 1, 2, 3, 5, ... pixels
    wide, so the strip starts at the picture's own edge colour and is widest at
    the sign's edge. The last step is cut short where the gap ends. If *limit*
    source lines are not enough, the last one takes the shortfall.
    """
    widths = _terms(gap, limit)
    if widths:
        widths[-1] -= sum(widths) - gap
    return widths


def _blurred(line: np.ndarray, size: int, operator) -> np.ndarray:
    """Each pixel of *line* (rows, 3) becomes a *size*-tall square; overlaps are combined."""
    if size <= 1:
        return line
    before = size // 2
    padded = np.pad(line, ((before, size - 1 - before), (0, 0)), mode="edge").astype(np.float32) / 255.0
    windows = sliding_window_view(padded, size, axis=0)  # (rows, 3, size)
    return np.rint(np.clip(operator(windows), 0.0, 1.0) * 255.0).astype(np.uint8)


class _Strip:
    """One edge's steps: the width drawn for each source line, counted from the picture's edge."""

    def __init__(self, gap: int, picture: int) -> None:
        limit = max(1, picture // 2)
        self.sizes = _terms(gap, limit)
        self.widths = fibonacci_widths(gap, limit)
        self.length = gap

    def render(self, picture: np.ndarray, operator) -> np.ndarray:
        """The strip as (rows, length, 3), outermost pixel first."""
        columns = [
            np.repeat(_blurred(picture[:, line], size, operator)[:, None], width, axis=1)
            for line, (size, width) in enumerate(zip(self.sizes, self.widths))
        ]
        return np.concatenate(columns, axis=1)[:, ::-1]


class FibonacciFit:
    """The geometry for one (fitted picture size, sign size); build once, apply per frame."""

    def __init__(self, picture: Tuple[int, int], size: Tuple[int, int]) -> None:
        self.picture = picture
        self.size = size
        self.vertical = picture[0] >= size[0] and picture[1] < size[1]
        width = size[1] if self.vertical else size[0]
        across = picture[1] if self.vertical else picture[0]
        self.gap = max(0, width - across)
        self.near = self.gap // 2
        self.far = self.gap - self.near
        self._strips = (_Strip(self.near, across), _Strip(self.far, across))

    def apply(self, picture: Image.Image, blur: str = DEFAULT_BLUR) -> Image.Image:
        """Fill the bars; *blur* names how overlapping pixels combine (unknown means average)."""
        if picture.size != self.picture:
            raise ValueError("picture does not match the fitted size")
        array = np.asarray(picture.convert("RGB"))
        if self.vertical:
            array = array.transpose(1, 0, 2)
        out = np.zeros((array.shape[0], array.shape[1] + self.gap, 3), dtype=np.uint8)
        out[:, self.near:self.near + array.shape[1]] = array
        # The far edge is the near edge seen in a mirror.
        operator = BLURS.get(blur, BLURS[DEFAULT_BLUR])
        self._paint(out, array, self._strips[0], operator)
        self._paint(out[:, ::-1], array[:, ::-1], self._strips[1], operator)
        if self.vertical:
            out = out.transpose(1, 0, 2)
        return Image.fromarray(np.ascontiguousarray(out))

    @staticmethod
    def _paint(out: np.ndarray, picture: np.ndarray, strip: _Strip, operator) -> None:
        if strip.length:
            out[:, :strip.length] = strip.render(picture, operator)


@lru_cache(maxsize=16)
def fibonacci_fit_for(picture: Tuple[int, int], size: Tuple[int, int]) -> FibonacciFit:
    return FibonacciFit(picture, size)

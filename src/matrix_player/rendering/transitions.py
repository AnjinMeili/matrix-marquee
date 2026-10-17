import math
import random
from enum import Enum
from typing import Iterator, Union

import numpy as np
from PIL import Image


class Transition(str, Enum):
    CUT = "cut"
    FADE = "fade"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"
    SLIDE_TOP = "slide_top"
    SLIDE_BOTTOM = "slide_bottom"
    ROTATE = "rotate"
    ROTATE_IN = "rotate_in"
    ZOOM = "zoom"
    ZOOM_IN = "zoom_in"
    PIXEL_DISSOLVE = "pixel_dissolve"
    FLASH = "flash"
    IMPACT = "impact"
    MORPH = "morph"


def _normalized(image: Image.Image, size: tuple) -> Image.Image:
    return image.convert("RGB").resize(size, Image.Resampling.LANCZOS)


def _lit_points(pixels: np.ndarray) -> tuple:
    """Bright pixels as (x, y, rgb) arrays, ordered left-to-right then top-to-bottom."""
    luminance = pixels @ np.array([0.299, 0.587, 0.114])
    threshold = max(24.0, float(np.percentile(luminance, 60)))
    ys, xs = np.nonzero(luminance > threshold)
    if xs.size == 0:  # an all-dark frame morphs from or to a single centre point
        height, width = luminance.shape
        return np.array([width // 2]), np.array([height // 2]), np.zeros((1, 3))
    order = np.lexsort((ys, xs))
    xs, ys = xs[order], ys[order]
    return xs.astype(np.float64), ys.astype(np.float64), pixels[ys.astype(int), xs.astype(int)].astype(np.float64)


def _morph_frames(old: Image.Image, new: Image.Image, frames: int) -> Iterator[Image.Image]:
    """Pixel morph: the old picture's bright pixels flow into the new picture's shape.

    Bright pixels are paired by rank in reading order, so features travel a short,
    coherent distance; at 96x64 this is a few thousand points per frame.
    """
    old_pixels = np.asarray(old, dtype=np.float64)
    new_pixels = np.asarray(new, dtype=np.float64)
    height, width = old_pixels.shape[:2]
    ox, oy, ocolor = _lit_points(old_pixels)
    nx, ny, ncolor = _lit_points(new_pixels)
    count = max(ox.size, nx.size)
    pick_old = np.linspace(0, ox.size - 1, count).round().astype(int)
    pick_new = np.linspace(0, nx.size - 1, count).round().astype(int)
    ox, oy, ocolor = ox[pick_old], oy[pick_old], ocolor[pick_old]
    nx, ny, ncolor = nx[pick_new], ny[pick_new], ncolor[pick_new]
    for index in range(1, frames):
        progress = index / frames
        eased = progress * progress * (3 - 2 * progress)
        # Dim the cross-faded backdrop mid-way so the moving pixels read clearly.
        backdrop = old_pixels * (1 - eased) + new_pixels * eased
        backdrop *= 1 - 0.75 * math.sin(math.pi * progress)
        x = np.clip(np.rint(ox + (nx - ox) * eased), 0, width - 1).astype(int)
        y = np.clip(np.rint(oy + (ny - oy) * eased), 0, height - 1).astype(int)
        backdrop[y, x] = ocolor + (ncolor - ocolor) * eased
        yield Image.fromarray(backdrop.clip(0, 255).astype(np.uint8))
    yield new.copy()


def transition_frames(
    previous: Image.Image,
    incoming: Image.Image,
    transition: Union[Transition, str] = Transition.CUT,
    frames: int = 8,
    seed: int = 0,
) -> Iterator[Image.Image]:
    """Yield transition frames, always ending with an exact copy of incoming."""
    kind = Transition(transition)
    if frames <= 0:
        raise ValueError("frames must be positive")
    size = previous.size
    old = previous.convert("RGB")
    new = _normalized(incoming, size)
    if kind is Transition.CUT or frames == 1:
        yield new.copy()
        return

    if kind in {Transition.FADE, Transition.FADE_IN}:
        for index in range(1, frames + 1):
            yield Image.blend(old, new, index / frames)
        return
    if kind is Transition.FADE_OUT:
        black = Image.new("RGB", size)
        for index in range(1, frames):
            phase = index / (frames - 1)
            yield Image.blend(old, black, min(1.0, phase * 2.0))
        yield new.copy()
        return

    if kind in {
        Transition.SLIDE_LEFT, Transition.SLIDE_RIGHT, Transition.SLIDE_UP,
        Transition.SLIDE_DOWN, Transition.SLIDE_TOP, Transition.SLIDE_BOTTOM,
    }:
        aliases = {
            Transition.SLIDE_TOP: Transition.SLIDE_DOWN,
            Transition.SLIDE_BOTTOM: Transition.SLIDE_UP,
        }
        direction = aliases.get(kind, kind)
        width, height = size
        for index in range(1, frames + 1):
            progress = index / frames
            canvas = Image.new("RGB", size)
            if direction is Transition.SLIDE_LEFT:
                offset = round(width * progress)
                canvas.paste(old, (-offset, 0))
                canvas.paste(new, (width - offset, 0))
            elif direction is Transition.SLIDE_RIGHT:
                offset = round(width * progress)
                canvas.paste(old, (offset, 0))
                canvas.paste(new, (-width + offset, 0))
            elif direction is Transition.SLIDE_UP:
                offset = round(height * progress)
                canvas.paste(old, (0, -offset))
                canvas.paste(new, (0, height - offset))
            else:
                offset = round(height * progress)
                canvas.paste(old, (0, offset))
                canvas.paste(new, (0, -height + offset))
            yield canvas
        return

    if kind in {Transition.ROTATE, Transition.ROTATE_IN}:
        for index in range(1, frames + 1):
            progress = index / frames
            angle = 360.0 * (1.0 - progress)
            scale = max(1, round(max(size) * progress))
            layer = new.resize((scale, scale), Image.Resampling.BICUBIC).convert("RGBA")
            layer.putalpha(round(255 * progress))
            layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
            canvas = old.convert("RGBA")
            canvas.alpha_composite(layer, ((size[0] - layer.width) // 2, (size[1] - layer.height) // 2))
            yield canvas.convert("RGB") if index < frames else new.copy()
        return

    if kind in {Transition.ZOOM, Transition.ZOOM_IN}:
        for index in range(1, frames + 1):
            progress = index / frames
            width = max(1, round(size[0] * progress))
            height = max(1, round(size[1] * progress))
            layer = new.resize((width, height), Image.Resampling.BICUBIC)
            canvas = old.copy()
            canvas.paste(layer, ((size[0] - width) // 2, (size[1] - height) // 2))
            yield canvas if index < frames else new.copy()
        return

    if kind is Transition.PIXEL_DISSOLVE:
        coordinates = [(x, y) for y in range(size[1]) for x in range(size[0])]
        random.Random(seed).shuffle(coordinates)
        canvas = old.copy()
        pixels = canvas.load()
        new_pixels = new.load()
        for index in range(1, frames + 1):
            stop = math.ceil(len(coordinates) * index / frames)
            start = math.ceil(len(coordinates) * (index - 1) / frames)
            for x, y in coordinates[start:stop]:
                pixels[x, y] = new_pixels[x, y]
            yield canvas.copy() if index < frames else new.copy()

    if kind is Transition.MORPH:
        yield from _morph_frames(old, new, frames)
        return

    if kind is Transition.FLASH:
        # Camera-flash cut: burn to white, then cool down onto the new image.
        white = Image.new("RGB", size, (255, 255, 255))
        peak = max(1, frames // 3)
        for index in range(1, frames + 1):
            if index <= peak:
                yield Image.blend(old, white, index / peak)
            elif index < frames:
                yield Image.blend(white, new, (index - peak) / (frames - peak))
            else:
                yield new.copy()
        return

    if kind is Transition.IMPACT:
        # Comic-book slam: new image punches in oversized, flashes, then shakes.
        rng = random.Random(seed)
        slam = max(1, frames // 2)
        for index in range(1, frames + 1):
            if index < slam:
                progress = index / slam
                scale = 2.2 - 1.2 * (1 - (1 - progress) ** 3)
                width = max(1, round(size[0] * scale))
                height = max(1, round(size[1] * scale))
                layer = new.resize((width, height), Image.Resampling.BILINEAR)
                canvas = Image.blend(old, Image.new("RGB", size), 0.5)
                canvas.paste(layer, ((size[0] - width) // 2, (size[1] - height) // 2))
                yield canvas
            elif index == slam:
                yield Image.blend(new, Image.new("RGB", size, (255, 255, 255)), 0.6)
            elif index < frames:
                canvas = Image.new("RGB", size)
                canvas.paste(new, (rng.randint(-3, 3), rng.randint(-2, 2)))
                yield canvas
            else:
                yield new.copy()
        return

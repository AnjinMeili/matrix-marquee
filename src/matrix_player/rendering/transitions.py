import math
import random
from enum import Enum
from typing import Iterator, Union

import numpy as np
from PIL import Image

from .easing import ease_in_cubic, ease_out_cubic
from .fit import pixel_unit


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
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    WIPE_UP = "wipe_up"
    WIPE_DOWN = "wipe_down"
    GLITCH = "glitch"
    NEON = "neon"


WIPES = {
    # The side a wipe starts from, as which axis it runs along and whether it runs backwards.
    Transition.WIPE_LEFT: (1, True),
    Transition.WIPE_RIGHT: (1, False),
    Transition.WIPE_UP: (0, True),
    Transition.WIPE_DOWN: (0, False),
}
WIPE_FEATHER = 0.2  # the soft band, as a fraction of the sign


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


def _wipe_frames(old: Image.Image, new: Image.Image, frames: int, axis: int, backwards: bool) -> Iterator[Image.Image]:
    """A soft-edged wipe: a feathered band sweeps across, easing out as it lands."""
    old_pixels = np.asarray(old, dtype=np.float32)
    new_pixels = np.asarray(new, dtype=np.float32)
    length = old_pixels.shape[axis]
    along = (np.arange(length, dtype=np.float32) + 0.5) / length
    if backwards:
        along = 1.0 - along
    for index in range(1, frames):
        edge = ease_out_cubic(index / frames) * (1 + WIPE_FEATHER)
        weight = np.clip((edge - along) / WIPE_FEATHER, 0.0, 1.0)
        weight = weight[:, None, None] if axis == 0 else weight[None, :, None]
        yield Image.fromarray((old_pixels + (new_pixels - old_pixels) * weight).round().astype(np.uint8))
    yield new.copy()


def _glitch(pixels: np.ndarray, strength: float, rng: random.Random, unit: int) -> np.ndarray:
    """Slices of rows jump sideways and the colour channels split, more as strength grows."""
    height = pixels.shape[0]
    out = pixels.copy()
    shift = max(1, round(6 * unit * strength))
    row = 0
    while row < height:
        band = rng.randint(unit, 4 * unit)
        if rng.random() < 0.6 * strength:
            out[row: row + band] = np.roll(pixels[row: row + band], rng.randint(-shift, shift), axis=1)
        row += band
    split = round(2 * unit * strength)
    if split:
        out[..., 0] = np.roll(out[..., 0], split, axis=1)
        out[..., 2] = np.roll(out[..., 2], -split, axis=1)
    return out


def _glitch_frames(old: Image.Image, new: Image.Image, frames: int, seed: int) -> Iterator[Image.Image]:
    """The old picture breaks up, the new one takes over mid-way, and it settles clean.

    Kept to slices and colour splits, with no brightness flashes.
    """
    rng = random.Random(seed)
    unit = pixel_unit(old.size)
    old_pixels = np.asarray(old)
    new_pixels = np.asarray(new)
    for index in range(1, frames):
        progress = index / frames
        strength = math.sin(math.pi * progress)
        source = old_pixels if progress < 0.5 else new_pixels
        yield Image.fromarray(_glitch(source, strength, rng, unit))
    yield new.copy()


# The old picture's brightness through a neon change: it sags once, recovers a
# little, and dies; one flicker, so the panel never strobes.
NEON_CURVE = ((0.0, 1.0), (0.15, 0.55), (0.3, 0.85), (0.6, 0.0), (1.0, 0.0))


def _neon_frames(old: Image.Image, new: Image.Image, frames: int) -> Iterator[Image.Image]:
    """The old message flickers and dims like a failing tube, then the new one snaps on."""
    unit = pixel_unit(old.size)
    black = Image.new("RGB", old.size)
    snap = 0.8
    for index in range(1, frames):
        progress = index / frames
        if progress >= snap:
            yield new.copy()
            continue
        level = float(np.interp(progress, *zip(*NEON_CURVE)))
        frame = Image.blend(black, old, level)
        if 0.1 < progress < 0.35:
            # A small wiggle while the tube struggles.
            shifted = Image.new("RGB", old.size)
            shifted.paste(frame, (unit if index % 2 else -unit, 0))
            frame = shifted
        yield frame
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
        for index in range(1, frames):
            yield Image.blend(old, new, ease_out_cubic(index / frames))
        yield new.copy()
        return
    if kind is Transition.FADE_OUT:
        black = Image.new("RGB", size)
        for index in range(1, frames):
            phase = index / (frames - 1)
            yield Image.blend(old, black, ease_in_cubic(min(1.0, phase * 2.0)))
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
            progress = ease_out_cubic(index / frames)
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

    if kind in WIPES:
        yield from _wipe_frames(old, new, frames, *WIPES[kind])
        return

    if kind is Transition.GLITCH:
        yield from _glitch_frames(old, new, frames, seed)
        return

    if kind is Transition.NEON:
        yield from _neon_frames(old, new, frames)
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
        unit = pixel_unit(size)
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
                canvas.paste(new, (rng.randint(-3, 3) * unit, rng.randint(-2, 2) * unit))
                yield canvas
            else:
                yield new.copy()
        return

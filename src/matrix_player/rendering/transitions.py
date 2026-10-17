import math
import random
from enum import Enum
from typing import Iterator, Union

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


def _normalized(image: Image.Image, size: tuple) -> Image.Image:
    return image.convert("RGB").resize(size, Image.Resampling.LANCZOS)


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

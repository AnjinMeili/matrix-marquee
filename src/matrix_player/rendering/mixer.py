"""The layer mixer: a message over a picture, GIF, or video.

Layer A, the media, sets the clock; layer B, the message, is drawn at A's
time. B is taken from the sign's own text renderer as a layer with alpha, so
everything the renderer draws (the dark box, borders, glow, reveals, the
globe) carries over. The same compositor is meant to mix live streams later
(see docs/live-mixer-plan.md), so it speaks of layers A and B rather than
pictures and words.
"""

from dataclasses import dataclass, fields, replace
from typing import Dict, Iterator, Mapping, Optional, Tuple

import numpy as np
from PIL import Image, ImageColor, ImageFilter

from .fit import pixel_unit
from .frames import Frame, FrameSource
from .text import SCROLL_EFFECTS, TextRenderer, TextStyle

BLENDS = ("normal", "add", "screen", "multiply", "lighten", "darken", "difference", "overlay")
PLACEMENTS = ("full", "top", "bottom")
KEY_MODES = ("none", "black", "color")
KEY_LAYERS = ("a", "b")
BAND_FRACTION = 0.4  # a top or bottom band is this much of the sign's height
FPS = 30.0
LUMA = np.array([0.299, 0.587, 0.114], np.float32)


@dataclass(frozen=True)
class Mix:
    """How layer B sits on layer A, and what keeps B readable."""

    placement: str = "full"
    blend: str = "normal"
    opacity: int = 100  # percent, of whichever layer is on top
    key_mode: str = "none"  # "black": dark areas see-through; "color": a chosen colour is
    key_layer: str = "b"  # which layer gets the see-through holes; that layer goes on top
    key_color: str = "#00ff00"
    key_tolerance: int = 30  # percent
    key_softness: int = 10  # percent
    dim: int = 0  # percent darkening of A where B sits
    blur: bool = False  # soften A where B sits
    halo: bool = False  # a soft ring of the outline colour round the letters
    auto_color: bool = False  # pick the text colour for contrast with A

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "Mix":
        known: Dict[str, object] = {}
        for item in fields(cls):
            if item.name not in values or values[item.name] is None:
                continue
            value = values[item.name]
            if item.type in (bool, "bool"):
                value = bool(value)
            elif item.type in (int, "int"):
                value = int(value)  # type: ignore[arg-type]
            known[item.name] = value
        return replace(cls(), **known)


def band(placement: str, size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    """The part of the sign layer B covers, as (left, top, right, bottom)."""
    width, height = size
    if placement not in ("top", "bottom"):
        return 0, 0, width, height
    rows = max(1, min(height, round(height * BAND_FRACTION)))
    return (0, 0, width, rows) if placement == "top" else (0, height - rows, width, height)


def blend(mode: str, bottom: np.ndarray, top: np.ndarray) -> np.ndarray:
    """The standard separable blend modes, on colours from 0 to 1."""
    if mode == "add":
        return np.minimum(1.0, bottom + top)
    if mode == "screen":
        return 1.0 - (1.0 - bottom) * (1.0 - top)
    if mode == "multiply":
        return bottom * top
    if mode == "lighten":
        return np.maximum(bottom, top)
    if mode == "darken":
        return np.minimum(bottom, top)
    if mode == "difference":
        return np.abs(bottom - top)
    if mode == "overlay":
        return np.where(bottom < 0.5, 2 * bottom * top, 1 - 2 * (1 - bottom) * (1 - top))
    return top


def key_keep(pixels: np.ndarray, mix: Mix) -> np.ndarray:
    """How much of each pixel to keep (0 = see-through) under the mix's key."""
    softness = max(1e-3, mix.key_softness / 100 * 0.3)
    if mix.key_mode == "black":
        luma = pixels @ LUMA
        return np.clip((luma - mix.key_tolerance / 100 * 0.5) / softness, 0.0, 1.0)
    if mix.key_mode == "color":
        # Compare chroma (YCbCr) divided by brightness, so a green screen in shade
        # keys like one in light. Near-black has no reliable colour and is kept.
        key = np.array(ImageColor.getrgb(mix.key_color)[:3], np.float32) / 255
        to_chroma = np.array([[-0.168736, 0.5], [-0.331264, -0.418688], [0.5, -0.081312]], np.float32)
        floor = 0.08
        hue = (pixels @ to_chroma) / np.maximum(pixels.max(axis=-1), floor)[..., None]
        key_hue = (key @ to_chroma) / max(float(key.max()), floor)
        distance = np.linalg.norm(hue - key_hue, axis=-1)
        return np.clip((distance - mix.key_tolerance / 100 * 0.4) / softness, 0.0, 1.0)
    return np.ones(pixels.shape[:2], np.float32)


def composite(bottom: np.ndarray, top: np.ndarray, alpha: np.ndarray, mode: str, opacity: float) -> np.ndarray:
    """``top`` (straight colour) over ``bottom`` with per-pixel alpha, a blend mode, and opacity."""
    weight = (alpha * opacity)[..., None]
    return bottom * (1 - weight) + blend(mode, bottom, top) * weight


def _luma(color: str) -> float:
    return float(np.array(ImageColor.getrgb(color)[:3], np.float32) @ LUMA / 255)


def readable_style(style: TextStyle, under: np.ndarray) -> TextStyle:
    """Text colour chosen once for contrast with what is under it, so it never flickers."""
    if style.color_mode != "solid":
        return style
    light = float((under @ LUMA).mean())
    if light > 0.55:
        return replace(style, color_value="#000000", outline_color="#ffffff")
    if abs(_luma(style.color_value) - light) >= 0.45:
        return style
    return replace(style, color_value="#ffffff", outline_color="#000000")


class TextLayer:
    """A message drawn as a layer with alpha.

    Each frame is drawn over black and over white: where the two agree the
    message is opaque, and where they differ by the full range it is clear.
    That recovers exact colour and alpha for everything the renderer draws,
    without the renderer knowing about layers.
    """

    def __init__(self, renderer: TextRenderer) -> None:
        self.renderer = renderer
        self._black = Image.new("RGB", renderer.size)
        self._white = Image.new("RGB", renderer.size, (255, 255, 255))
        self._still: Optional[Tuple[np.ndarray, np.ndarray]] = None

    def at(self, t: float) -> Tuple[np.ndarray, np.ndarray]:
        """(colour, alpha) at time t: colour is straight (not premultiplied), 0..1."""
        still = not self.renderer.animated and t >= self.renderer.lead_seconds
        if still and self._still is not None:
            return self._still
        on_black = np.asarray(self.renderer.frame(t, self._black), np.float32) / 255
        on_white = np.asarray(self.renderer.frame(t, self._white), np.float32) / 255
        alpha = np.clip(1.0 - (on_white - on_black).mean(axis=-1), 0.0, 1.0)
        colour = np.where(alpha[..., None] > 1e-3, on_black / np.maximum(alpha, 1e-3)[..., None], 0.0)
        layer = (np.clip(colour, 0.0, 1.0), alpha)
        if still:
            self._still = layer
        return layer


class MixFrameSource(FrameSource):
    """A message (B) over a media source (A), mixed frame by frame."""

    def __init__(
        self, base: FrameSource, text: str, style: TextStyle, mix: Mix, size: Tuple[int, int]
    ) -> None:
        self.base = base
        self.text = text
        self.style = style
        self.mix = mix
        self.size = size
        self.region = band(mix.placement, size)
        self.unit = pixel_unit(size)
        self._layer: Optional[TextLayer] = None
        self._elapsed = 0.0  # message time runs on across the media's loops

    @property
    def renderer(self) -> TextRenderer:
        if self._layer is None:
            left, top, right, bottom = self.region
            self._layer = TextLayer(TextRenderer(self.text, (right - left, bottom - top), self.style))
        return self._layer.renderer

    @property
    def lead_seconds(self) -> float:
        return self.renderer.lead_seconds

    @property
    def min_seconds(self) -> float:
        """A scrolling message needs one full pass to be read."""
        return self.renderer.cycle_seconds if self.style.effect in SCROLL_EFFECTS else 0.0

    def _prepare(self, image: Image.Image) -> np.ndarray:
        """Layer A with the readability treatment where B sits; done once per media frame."""
        image = image.convert("RGB")
        if image.size != self.size:
            image = image.resize(self.size, Image.Resampling.LANCZOS)
        left, top, right, bottom = self.region
        if self.mix.blur:
            area = image.crop(self.region).filter(ImageFilter.GaussianBlur(1.5 * self.unit))
            image = image.copy()
            image.paste(area, (left, top))
        pixels = np.asarray(image, np.float32) / 255
        if self.mix.dim:
            pixels = pixels.copy()
            pixels[top:bottom, left:right] *= 1 - self.mix.dim / 100
        return pixels

    def _choose_colour(self, under: np.ndarray) -> None:
        left, top, right, bottom = self.region
        style = readable_style(self.style, under[top:bottom, left:right])
        if style != self.style:
            self.style = style
            self._layer = TextLayer(TextRenderer(self.text, (right - left, bottom - top), style))

    def _compose(self, under: np.ndarray, t: float) -> Image.Image:
        assert self._layer is not None
        left, top, right, bottom = self.region
        colour = np.zeros_like(under)
        alpha = np.zeros(under.shape[:2], np.float32)
        colour[top:bottom, left:right], alpha[top:bottom, left:right] = self._layer.at(t)
        if self.mix.halo:
            ring = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2 * self.unit))
            halo = np.minimum(1.0, np.asarray(ring, np.float32) / 255 * 1.6)
            tint = np.array(ImageColor.getrgb(self.style.outline_color)[:3], np.float32) / 255
            under = under * (1 - halo[..., None]) + tint * halo[..., None]
        opacity = self.mix.opacity / 100
        if self.mix.key_mode != "none" and self.mix.key_layer == "a":
            # A has the holes, so A goes on top; B shows through on black.
            mixed = composite(colour * alpha[..., None], under, key_keep(under, self.mix), self.mix.blend, opacity)
        else:
            if self.mix.key_mode != "none":
                alpha = alpha * key_keep(colour, self.mix)
            mixed = composite(under, colour, alpha, self.mix.blend, opacity)
        return Image.fromarray((np.clip(mixed, 0.0, 1.0) * 255).round().astype(np.uint8))

    def __iter__(self) -> Iterator[Frame]:
        renderer = self.renderer
        for frame in self.base:
            under = self._prepare(frame.image)
            if self.mix.auto_color and self._elapsed == 0.0:
                self._choose_colour(under)
                renderer = self.renderer
            moving = renderer.animated or self._elapsed < renderer.lead_seconds
            steps = max(1, round(frame.duration * FPS)) if moving else 1
            step = frame.duration / steps
            for _ in range(steps):
                yield Frame(self._compose(under, self._elapsed), step)
                self._elapsed += step

    def close(self) -> None:
        self.base.close()

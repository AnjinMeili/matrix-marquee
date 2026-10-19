"""Sign-style text rendering: auto-fitting display fonts, colour fields, and motion.

Text is laid out once into glyph and outline masks, then composited per frame so
animated colour modes, borders, and effects stay cheap enough for the Pi.
"""

import colorsys
from dataclasses import dataclass, replace
from functools import lru_cache
import math
from pathlib import Path
import random
import zlib
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from .easing import ease_out_back
from .fit import pixel_unit
from .reveals import REVEALS, SCROLL_REVEALS, LetterState, Reveal, neon_state

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
SYSTEM_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


@dataclass(frozen=True)
class FontFace:
    label: str
    regular: str
    bold: Optional[str] = None
    italic: Optional[str] = None
    bold_italic: Optional[str] = None


FONTS: Dict[str, FontFace] = {
    "impact": FontFace("Impact (tall and heavy)", "Anton-Regular.ttf"),
    "comic": FontFace("Comic book", "Bangers-Regular.ttf"),
    "block": FontFace("Wide block", "ArchivoBlack-Regular.ttf"),
    "headline": FontFace("Headline (narrow capitals)", "BebasNeue-Regular.ttf"),
    "marker": FontFace("Hand-painted marker", "PermanentMarker-Regular.ttf"),
    "pixel": FontFace("Retro pixel", "PressStart2P-Regular.ttf"),
    "sans": FontFace(
        "Plain",
        str(SYSTEM_FONT_DIR / "DejaVuSans.ttf"),
        bold=str(SYSTEM_FONT_DIR / "DejaVuSans-Bold.ttf"),
        italic=str(SYSTEM_FONT_DIR / "DejaVuSans-Oblique.ttf"),
        bold_italic=str(SYSTEM_FONT_DIR / "DejaVuSans-BoldOblique.ttf"),
    ),
}
FONT_ALIASES = {"default": "sans"}
FALLBACK_FONT = "Anton-Regular.ttf"

COLOR_MODES = ("solid", "rainbow", "plasma", "cycle", "fire")
EFFECTS = ("none", "scroll", "scroll_right", "globe", "pulse", "shake", "flash", "pow", "zoom")
# "scroll" moves right to left like a news ticker; "scroll_right" moves left to right;
# "globe" moves right to left round a cylinder.
SCROLL_EFFECTS = frozenset({"scroll", "scroll_right", "globe"})
# pow and zoom are entrances of their own, so they take no reveal.
ENTRANCE_EFFECTS = frozenset({"pow", "zoom"})
BORDER_STYLES = ("none", "solid", "dashed", "double", "marquee", "rainbow")
ALIGNMENTS = ("left", "center", "right")

SCROLL_SPEED = 32.0  # pixels per second at normal speed on a 64-pixel-tall sign; scaled to the sign
# One speed setting paces every animation: scrolling, pulsing, flashing, colour.
MOTION_SPEEDS = {"slow": 0.55, "normal": 1.0, "fast": 1.8}
ITALIC_SLANT = 0.2


@dataclass(frozen=True)
class TextStyle:
    font_family: str = "impact"
    font_size: int = 0  # 0 sizes the text to fill the panel
    bold: bool = False
    italic: bool = False
    alignment: str = "center"
    color_mode: str = "solid"
    color_value: str = "#ffffff"
    outline: bool = True
    outline_color: str = "#000000"
    border_style: str = "none"
    border_color: str = "#ffffff"
    effect: str = "none"
    accent_color: str = "#ffd400"
    background_color: str = "#000000"
    motion_speed: str = "normal"
    reveal: str = "none"
    backing: bool = False  # a dark rounded plate behind the text


def _resolve(path: str) -> Optional[Path]:
    candidate = Path(path) if Path(path).is_absolute() else FONT_DIR / path
    return candidate if candidate.is_file() else None


@lru_cache(maxsize=256)
def load_font(family: str, size: int, bold: bool, italic: bool) -> Tuple[ImageFont.FreeTypeFont, bool, bool]:
    """Returns the font plus whether bold and italic must be synthesized."""
    face = FONTS[FONT_ALIASES.get(family, family)] if FONT_ALIASES.get(family, family) in FONTS else FONTS["impact"]
    variants = [
        (bold and italic, face.bold_italic, False, False),
        (bold, face.bold, False, italic),
        (italic, face.italic, bold, False),
    ]
    for wanted, path, synth_bold, synth_italic in variants:
        if wanted and path and _resolve(path):
            return ImageFont.truetype(str(_resolve(path)), size), synth_bold, synth_italic
    regular = _resolve(face.regular) or _resolve(FALLBACK_FONT)
    return ImageFont.truetype(str(regular), size), bold, italic


def font_choices() -> List[Dict[str, str]]:
    return [{"id": key, "label": face.label} for key, face in FONTS.items()]


def _rgb(value: str) -> Tuple[int, int, int]:
    return ImageColor.getrgb(value)[:3]


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    h = np.mod(h, 1.0) * 6.0
    s = np.broadcast_to(s, h.shape)
    v = np.broadcast_to(v, h.shape)
    sector = np.floor(h).astype(np.int64) % 6
    f = h - np.floor(h)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    r = np.choose(sector, [v, q, p, p, t, v])
    g = np.choose(sector, [t, v, v, q, p, p])
    b = np.choose(sector, [p, p, t, v, v, q])
    return (np.stack([r, g, b], axis=-1) * 255).clip(0, 255).astype(np.uint8)


@lru_cache(maxsize=8)
def _grid(width: int, height: int) -> Tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    return xs, ys


def colour_field(mode: str, size: Tuple[int, int], t: float, base: str) -> Image.Image:
    """Full-panel fill for the given colour mode at time t (seconds)."""
    width, height = size
    if mode == "solid":
        return Image.new("RGB", size, _rgb(base))
    if mode == "cycle":
        r, g, b = (channel / 255 for channel in _rgb(base))
        hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
        if saturation < 0.2:
            saturation, value = 1.0, 1.0
        rgb = colorsys.hsv_to_rgb((hue + t * 0.25) % 1.0, saturation, max(value, 0.6))
        return Image.new("RGB", size, tuple(round(channel * 255) for channel in rgb))
    xs, ys = _grid(width, height)
    if mode == "rainbow":
        hue = xs / width * 0.8 + ys / height * 0.2 - t * 0.35
        pixels = _hsv_to_rgb(hue, np.float64(1.0), np.float64(1.0))
    elif mode == "plasma":
        cx, cy = width / 2, height / 2
        wave = (
            np.sin(xs / 7.0 + t * 2.0)
            + np.sin(ys / 5.0 - t * 1.6)
            + np.sin((xs + ys) / 9.0 + t * 1.3)
            + np.sin(np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / 6.0 - t * 2.2)
        )
        pixels = _hsv_to_rgb(wave / 8.0 + t * 0.08, np.float64(1.0), np.float64(1.0))
    else:  # fire: white-hot yellow at the base rising to orange-red tips
        rise = ys / max(1, height - 1)
        flicker = np.sin(xs * 0.7 + t * 9.0) * np.sin(ys * 0.45 - t * 6.0)
        hue = 0.02 + 0.13 * rise + 0.02 * flicker
        saturation = 1.0 - 0.35 * rise ** 3
        value = 0.92 + 0.08 * np.sin(xs * 0.3 + ys * 0.2 + t * 11.0)
        pixels = _hsv_to_rgb(hue, saturation, value)
    return Image.fromarray(pixels)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float, extra: int) -> List[str]:
    lines: List[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if font.getlength(candidate) + extra <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


@dataclass(frozen=True)
class Placement:
    """Where one character sits, in the cropped block's coordinates."""

    char: str
    x: float  # pen position
    y: float  # line top
    advance: float
    line: int


@dataclass
class TextBlock:
    glyph: Image.Image  # "L" mask of the letter faces
    outline: Optional[Image.Image]  # "L" mask of faces plus stroke
    font_size: int
    # What per-letter drawing needs to redraw each character where the block put it.
    lines: Tuple[Tuple[str, float, float], ...] = ()  # (text, x, top) of each line
    origin: Tuple[int, int] = (0, 0)  # the crop's top-left in the uncropped block
    canvas: Tuple[int, int] = (1, 1)  # the uncropped block's size
    bold_px: int = 0
    stroke: int = 0
    synth_italic: bool = False
    cap: Tuple[float, float] = (0.0, 0.0)  # capital top and bottom below a line's top

    @property
    def size(self) -> Tuple[int, int]:
        return (self.outline or self.glyph).size


def _render_block(lines: List[str], style: TextStyle, size: int) -> TextBlock:
    font, synth_bold, synth_italic = load_font(style.font_family, size, style.bold, style.italic)
    bold_px = max(1, round(size / 28)) if synth_bold else 0
    stroke = max(1, round(size / 16)) if style.outline else 0
    ascent, descent = font.getmetrics()
    # Display fonts reserve tall accent space; a tighter advance keeps lines big.
    line_height = round((ascent + descent) * 0.88)
    gap = max(1, round(size * 0.04))
    widths = [font.getlength(line) for line in lines]
    pad = stroke + bold_px + 2
    height = line_height * len(lines) + gap * (len(lines) - 1) + pad * 2
    slant_pad = math.ceil(height * ITALIC_SLANT) if synth_italic else 0
    width = math.ceil(max(widths, default=0)) + pad * 2 + slant_pad
    glyph = Image.new("L", (max(1, width), max(1, height)))
    outline = Image.new("L", glyph.size) if stroke else None
    glyph_draw = ImageDraw.Draw(glyph)
    outline_draw = ImageDraw.Draw(outline) if outline else None
    longest = max(widths, default=0)
    positions: List[Tuple[str, float, float]] = []
    for index, (line, line_width) in enumerate(zip(lines, widths)):
        if style.alignment == "left":
            x = pad
        elif style.alignment == "right":
            x = pad + longest - line_width
        else:
            x = pad + (longest - line_width) / 2
        y = pad + index * (line_height + gap)
        positions.append((line, x, y))
        glyph_draw.text((x, y), line, font=font, fill=255, stroke_width=bold_px, stroke_fill=255)
        if outline_draw:
            outline_draw.text(
                (x, y), line, font=font, fill=255, stroke_width=bold_px + stroke, stroke_fill=255
            )
    if synth_italic:
        shear = (1, ITALIC_SLANT, -ITALIC_SLANT * glyph.height, 0, 1, 0)
        glyph = glyph.transform(glyph.size, Image.Transform.AFFINE, shear, Image.Resampling.BILINEAR)
        if outline:
            outline = outline.transform(outline.size, Image.Transform.AFFINE, shear, Image.Resampling.BILINEAR)
    bbox = (outline or glyph).getbbox()
    if bbox is None:
        empty = Image.new("L", (1, 1))
        return TextBlock(empty, empty.copy() if outline else None, size)
    cap_box = font.getbbox("H")
    return TextBlock(
        glyph.crop(bbox), outline.crop(bbox) if outline else None, size,
        lines=tuple(positions), origin=(bbox[0], bbox[1]), canvas=glyph.size,
        bold_px=bold_px, stroke=stroke, synth_italic=synth_italic,
        cap=(float(cap_box[1]), float(cap_box[3])),
    )


def layout_text(text: str, style: TextStyle, box: Tuple[int, int], single_line: bool = False) -> TextBlock:
    """Lays out text at the fixed size, or at the largest size that fits the box."""
    box_width, box_height = max(1, box[0]), max(1, box[1])
    if single_line:
        text = " ".join(text.split())

    def build(size: int) -> TextBlock:
        if single_line:
            lines = [text]
        else:
            font, synth_bold, _ = load_font(style.font_family, size, style.bold, style.italic)
            extra = 2 * (max(1, round(size / 16)) if style.outline else 0)
            extra += 2 * (max(1, round(size / 28)) if synth_bold else 0)
            lines = _wrap(text, font, box_width, extra)
        return _render_block(lines, style, size)

    def fits(block: TextBlock) -> bool:
        width, height = block.size
        return height <= box_height and (single_line or width <= box_width)

    if style.font_size > 0:
        return build(style.font_size)
    low, high = 6, max(7, box_height * 2)
    best = build(low)
    while low <= high:
        middle = (low + high) // 2
        block = build(middle)
        if fits(block):
            best, low = block, middle + 1
        else:
            high = middle - 1
    return best


@dataclass
class Letter:
    """One character's masks, placed in the cropped block's coordinates."""

    placement: Placement
    glyph: Optional[np.ndarray] = None  # float32 0..255; None for spaces
    glyph_at: Tuple[int, int] = (0, 0)
    outline: Optional[np.ndarray] = None
    outline_at: Tuple[int, int] = (0, 0)


def _letter_mask(
    font: ImageFont.FreeTypeFont, char: str, x: float, y: float, stroke: int, block: TextBlock
) -> Optional[Tuple[np.ndarray, int, int]]:
    """Draws one character as the whole block drew it: its mask and its top-left in the uncropped block."""
    left, top, right, bottom = font.getbbox(char, stroke_width=stroke)
    ix, iy = math.floor(x + left) - 1, math.floor(y + top) - 1
    width, height = math.ceil(right - left) + 3, math.ceil(bottom - top) + 3
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).text((x - ix, y - iy), char, font=font, fill=255, stroke_width=stroke, stroke_fill=255)
    if block.synth_italic:
        # The block was sheared about its own bottom edge; shear this piece to match.
        full = block.canvas[1]
        low = math.floor(ITALIC_SLANT * (full - iy - height))
        high = math.ceil(ITALIC_SLANT * (full - iy))
        shear = (1, ITALIC_SLANT, low + ITALIC_SLANT * (iy - full), 0, 1, 0)
        mask = mask.transform((width + high - low + 1, height), Image.Transform.AFFINE, shear, Image.Resampling.BILINEAR)
        ix += low
    box = mask.getbbox()
    if box is None:
        return None
    return np.asarray(mask.crop(box), dtype=np.float32), ix + box[0], iy + box[1]


def letter_layout(block: TextBlock, style: TextStyle) -> List[Letter]:
    """Every character of a laid-out block, spaces included, each drawn on its own."""
    font, _, _ = load_font(style.font_family, block.font_size, style.bold, style.italic)
    origin_x, origin_y = block.origin
    letters: List[Letter] = []
    for line_index, (line, x, y) in enumerate(block.lines):
        pen = 0.0
        for position, char in enumerate(line):
            advance = font.getlength(char)
            if position:
                # Pairs carry the kerning between neighbours, and this stays linear in length.
                pen += font.getlength(line[position - 1: position + 1]) - advance
            letter = Letter(Placement(char, x + pen - origin_x, y - origin_y, advance, line_index))
            if char.strip():
                face = _letter_mask(font, char, x + pen, y, block.bold_px, block)
                if face is not None:
                    letter.glyph, letter.glyph_at = face[0], (face[1] - origin_x, face[2] - origin_y)
                    if block.outline is not None:
                        ring = _letter_mask(font, char, x + pen, y, block.bold_px + block.stroke, block)
                        if ring is not None:
                            letter.outline, letter.outline_at = ring[0], (ring[1] - origin_x, ring[2] - origin_y)
            letters.append(letter)
    return letters


def _stamp(target: np.ndarray, mask: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    """Lays a mask into target at (x, y), keeping the brighter value, clipped to the target."""
    height, width = mask.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(target.shape[1], x + width), min(target.shape[0], y + height)
    if x0 >= x1 or y0 >= y1:
        return
    piece = mask[y0 - y: y1 - y, x0 - x: x1 - x]
    if alpha < 1.0:
        piece = piece * alpha
    np.maximum(target[y0:y1, x0:x1], piece, out=target[y0:y1, x0:x1])


def _mask_image(mask: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(mask, 0, 255).astype(np.uint8))


def _scaled(mask: Optional[Image.Image], scale: float) -> Optional[Image.Image]:
    if mask is None or abs(scale - 1.0) < 1e-3:
        return mask
    width = max(1, round(mask.width * scale))
    height = max(1, round(mask.height * scale))
    return mask.resize((width, height), Image.Resampling.BILINEAR)


def _perimeter(size: Tuple[int, int], inset: int = 0) -> List[Tuple[int, int]]:
    width, height = size
    left, top, right, bottom = inset, inset, width - 1 - inset, height - 1 - inset
    if right <= left or bottom <= top:
        return []
    points = [(x, top) for x in range(left, right)]
    points += [(right, y) for y in range(top, bottom)]
    points += [(x, bottom) for x in range(right, left, -1)]
    points += [(left, y) for y in range(bottom, top, -1)]
    return points


HEADROOM = {"pulse": 1.1, "pow": 1.12, "shake": 1.06}
PLATE_OPACITY = 0.72


class TextRenderer:
    """Renders one text panel at any point in its reveal and animation.

    Time ``t`` counts from when the item starts. A reveal, if any, plays first
    for ``lead_seconds``; the message's own motion then starts from its
    beginning, so the reveal adds time rather than taking it from reading.
    """

    FPS = 30.0

    def __init__(
        self,
        text: str,
        size: Tuple[int, int],
        style: Optional[TextStyle] = None,
        background: Optional[Image.Image] = None,
    ) -> None:
        self.text = text
        self.size = size
        self.style = style or TextStyle()
        self.background = (
            background.convert("RGB").resize(size)
            if background is not None
            else Image.new("RGB", size, _rgb(self.style.background_color))
        )
        # Borders, shakes, and scrolling scale with the sign so they look the same on any size.
        self.unit = pixel_unit(size)
        self.scroll_speed = SCROLL_SPEED * size[1] / 64
        border = self.style.border_style
        self.border_width = self.unit * (0 if border == "none" else 3 if border == "double" else 1)
        inset = self.border_width + self.unit
        self.box = (inset, inset, size[0] - inset, size[1] - inset)
        box_size = (self.box[2] - self.box[0], self.box[3] - self.box[1])
        headroom = HEADROOM.get(self.style.effect, 1.0)
        fit_box = (int(box_size[0] / headroom), int(box_size[1] / headroom))
        self.block = layout_text(text, self.style, fit_box, single_line=self.style.effect in SCROLL_EFFECTS)
        self._outline_color = Image.new("RGB", size, _rgb(self.style.outline_color))
        self._rings = [_perimeter(size, inset) for inset in range(self.unit)]

        reveal = self.style.reveal if self.style.reveal in REVEALS else "none"
        if self.style.effect in ENTRANCE_EFFECTS or not text.strip():
            reveal = "none"
        elif self.style.effect in SCROLL_EFFECTS and reveal not in SCROLL_REVEALS:
            reveal = "none"
        self.reveal = reveal
        self._seed = zlib.crc32(text.encode("utf-8"))
        self._letters: Optional[List[Letter]] = None
        self._schedule: Optional[Reveal] = None
        self._scroll_neon_on: Optional[List[float]] = None
        self._plate = self._backing_plate()
        self._black = Image.new("RGB", size)
        # Globe: the flat strip is as long as half the cylinder's circumference.
        self._globe_width = math.ceil(box_size[0] * math.pi / 2) if self.style.effect == "globe" else 0
        self._globe_map: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None

    @property
    def animated(self) -> bool:
        """Whether the message keeps moving after any reveal."""
        return (
            self.style.effect != "none"
            or self.style.color_mode != "solid"
            or self.style.border_style in {"marquee", "rainbow"}
        )

    @property
    def speed(self) -> float:
        return MOTION_SPEEDS.get(self.style.motion_speed, 1.0)

    @property
    def lead_seconds(self) -> float:
        """Seconds of reveal before the motion starts. A scrolling message reveals as it scrolls, so it has none."""
        if self.reveal == "none" or self.style.effect in SCROLL_EFFECTS:
            return 0.0
        return self.schedule.duration

    @property
    def letters(self) -> List[Letter]:
        if self._letters is None:
            self._letters = letter_layout(self.block, self.style)
        return self._letters

    @property
    def schedule(self) -> Reveal:
        if self._schedule is None:
            chars = [char for line, _, _ in self.block.lines for char in line]
            _, top = self._anchor(*self.block.size)
            height = self.block.size[1]
            self._schedule = Reveal(
                self.reveal, chars, self.speed,
                rise=self.size[1] * 0.3,
                drop=top + height + self.unit,
                matte_depth=height + 2 * self.unit,
                seed=self._seed,
            )
        return self._schedule

    def _travel(self) -> float:
        span = self._globe_width or (self.box[2] - self.box[0])
        return self.block.size[0] + span

    @property
    def cycle_seconds(self) -> float:
        """Length of one natural pass of the animation, e.g. one full scroll."""
        if self.style.effect in SCROLL_EFFECTS:
            return self._travel() / (self.scroll_speed * self.speed)
        if self.style.effect in ENTRANCE_EFFECTS:
            return 1.0 / self.speed
        return 2.0 / self.speed if self.animated else 0.0

    def _anchor(self, width: int, height: int) -> Tuple[float, float]:
        left, top, right, bottom = self.box
        if self.style.alignment == "left":
            x = left
        elif self.style.alignment == "right":
            x = right - width
        else:
            x = left + (right - left - width) / 2
        return x, top + (bottom - top - height) / 2

    def _motion(self, t: float) -> Tuple[float, float, float, bool]:
        """Returns (scale, dx, dy, visible) for the text at time t."""
        effect = self.style.effect
        if effect == "pulse":
            return 1.0 + 0.1 * math.sin(2 * math.pi * t / 0.8), 0.0, 0.0, True
        if effect == "shake":
            rng = random.Random(int(t * 20))
            return 1.0, rng.randint(-2, 2) * self.unit, rng.randint(-2, 2) * self.unit, True
        if effect == "flash":
            return 1.0, 0.0, 0.0, (t % 0.6) < 0.4
        if effect == "pow":
            if t < 0.35:
                return 0.2 + 0.8 * ease_out_back(t / 0.35), 0.0, 0.0, True
            if t < 0.6:
                rng = random.Random(int(t * 30))
                return 1.0, rng.randint(-2, 2) * self.unit, rng.randint(-1, 1) * self.unit, True
            return 1.0 + 0.03 * math.sin(2 * math.pi * t / 1.2), 0.0, 0.0, True
        if effect == "zoom":
            if t < 0.3:
                return 3.0 - 2.0 * (1 - (1 - t / 0.3) ** 3), 0.0, 0.0, True
            if t < 0.45:
                rng = random.Random(int(t * 30))
                return 1.0, rng.randint(-2, 2) * self.unit, rng.randint(-2, 2) * self.unit, True
        return 1.0, 0.0, 0.0, True

    def _backing_plate(self) -> Optional[Image.Image]:
        """A dark rounded plate behind the text, for messages over busy pictures."""
        if not self.style.backing or not self.text.strip():
            return None
        width, height = self.block.size
        pad = 2 * self.unit
        left, top, right, bottom = self.box
        if self.style.effect in SCROLL_EFFECTS:
            # A band the text scrolls along.
            y = top + (bottom - top - height) / 2
            rect = (left, y - pad, right - 1, y + height + pad)
        else:
            x, y = self._anchor(width, height)
            centre_x, centre_y = x + width / 2, y + height / 2
            headroom = HEADROOM.get(self.style.effect, 1.0)
            shake = 2 * self.unit if self.style.effect == "shake" else 0
            half_w = width * headroom / 2 + pad + shake
            half_h = height * headroom / 2 + pad + shake
            rect = (centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h)
        plate = Image.new("L", self.size)
        ImageDraw.Draw(plate).rounded_rectangle(rect, radius=2 * self.unit, fill=round(255 * PLATE_OPACITY))
        return plate

    def _draw_plate(self, canvas: Image.Image) -> None:
        if self._plate is not None:
            canvas.paste(self._black, (0, 0), self._plate)

    def _burst(self, canvas: Image.Image, t: float) -> None:
        width, height = self.size
        grow = min(1.0, 0.3 + t / 0.35 * 0.7)
        outer = max(width, height) * 0.62 * grow
        inner = min(width, height) * 0.4 * grow
        spikes = 14
        angle = t * 0.5
        points = []
        for index in range(spikes * 2):
            radius = outer if index % 2 == 0 else inner
            theta = angle + math.pi * index / spikes
            points.append((width / 2 + radius * math.cos(theta), height / 2 + radius * math.sin(theta) * 0.8))
        ImageDraw.Draw(canvas).polygon(
            points, fill=_rgb(self.style.accent_color), outline=_rgb(self.style.outline_color)
        )

    def _border(self, canvas: Image.Image, t: float) -> None:
        style = self.style.border_style
        if style == "none":
            return
        color = _rgb(self.style.border_color)
        pixels = canvas.load()
        unit = self.unit
        if style in {"solid", "double"}:
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, self.size[0] - 1, self.size[1] - 1), outline=color, width=unit)
            if style == "double":
                inset = 2 * unit
                draw.rectangle(
                    (inset, inset, self.size[0] - 1 - inset, self.size[1] - 1 - inset), outline=color, width=unit
                )
            return
        # Dashes and the marquee's step grow with the sign, ring by ring.
        offset = int(t * 14 * unit)
        for ring in self._rings:
            count = max(1, len(ring))
            for index, (x, y) in enumerate(ring):
                if style == "dashed":
                    if (index // unit) % 5 < 3:
                        pixels[x, y] = color
                elif style == "marquee":
                    if ((index + offset) // unit) % 6 < 3:
                        pixels[x, y] = color
                elif style == "rainbow":
                    r, g, b = colorsys.hsv_to_rgb((index / count - t * 0.4) % 1.0, 1.0, 1.0)
                    pixels[x, y] = (round(r * 255), round(g * 255), round(b * 255))

    # Per-letter drawing, for reveals.

    def _letter_masks(
        self, position: Tuple[int, int], states: List[LetterState], size: Optional[Tuple[int, int]] = None
    ) -> Tuple[Optional[np.ndarray], np.ndarray]:
        width, height = size or self.size
        glyph = np.zeros((height, width), np.float32)
        outline = np.zeros_like(glyph) if self.block.outline is not None else None
        for letter, state in zip(self.letters, states):
            if letter.glyph is None or state.alpha <= 0:
                continue
            x, y = position[0] + round(state.dx), position[1] + round(state.dy)
            _stamp(glyph, letter.glyph, x + letter.glyph_at[0], y + letter.glyph_at[1], state.alpha * state.light)
            if outline is not None and letter.outline is not None:
                _stamp(outline, letter.outline, x + letter.outline_at[0], y + letter.outline_at[1], state.alpha)
        return outline, glyph

    def _block_masks(
        self, position: Tuple[int, int], size: Tuple[int, int]
    ) -> Tuple[Optional[np.ndarray], np.ndarray]:
        width, height = size
        glyph = np.zeros((height, width), np.float32)
        _stamp(glyph, np.asarray(self.block.glyph, dtype=np.float32), *position)
        outline = None
        if self.block.outline is not None:
            outline = np.zeros_like(glyph)
            _stamp(outline, np.asarray(self.block.outline, dtype=np.float32), *position)
        return outline, glyph

    def _matte(
        self, outline: Optional[np.ndarray], glyph: np.ndarray, position: Tuple[int, int], line: Tuple[float, float]
    ) -> None:
        """Hides the text below a thin line and draws the line, which grows and then fades."""
        width_fraction, alpha = line
        line_y = position[1] + self.block.size[1] + self.unit
        for mask in (outline, glyph):
            if mask is not None:
                mask[max(0, line_y):, :] = 0
        span = (self.block.size[0] + 2 * self.unit) * width_fraction
        centre = position[0] + self.block.size[0] / 2
        x0, x1 = max(0, round(centre - span / 2)), min(self.size[0], round(centre + span / 2))
        if alpha > 0 and x1 > x0 and 0 <= line_y < self.size[1]:
            glyph[line_y: line_y + self.unit, x0:x1] = 255 * alpha

    def _cursor(self, outline: Optional[np.ndarray], glyph: np.ndarray, position: Tuple[int, int], index: int) -> None:
        letters = self.letters
        if not letters:
            return
        if index < len(letters):
            place = letters[index].placement
            x = place.x
        else:
            place = letters[-1].placement
            x = place.x + place.advance
        top, bottom = place.y + self.block.cap[0], place.y + self.block.cap[1]
        bar_width = max(2 * self.unit, round(self.block.font_size * 0.12))
        left, upper = position[0] + round(x), position[1] + round(top)
        bar = np.full((max(1, round(bottom - top)), bar_width), 255.0, np.float32)
        _stamp(glyph, bar, left, upper)
        if outline is not None:
            ring = self.block.stroke + self.block.bold_px
            _stamp(outline, np.pad(bar, ring, constant_values=255.0), left - ring, upper - ring)

    def _paint(
        self, canvas: Image.Image, outline: Optional[np.ndarray], glyph: np.ndarray, fill: Image.Image
    ) -> Image.Image:
        if outline is not None:
            canvas = Image.composite(self._outline_color, canvas, _mask_image(outline))
        return Image.composite(fill, canvas, _mask_image(glyph))

    def _reveal_frame(self, t: float, base: Image.Image) -> Image.Image:
        moment = self.schedule.at(t)
        canvas = base.copy()
        self._draw_plate(canvas)
        x, y = self._anchor(*self.block.size)
        settled = (round(x), round(y))
        outline, glyph = self._letter_masks((settled[0], round(y + moment.block_dy)), moment.states)
        if moment.matte is not None:
            self._matte(outline, glyph, settled, moment.matte)
        if moment.cursor is not None:
            self._cursor(outline, glyph, settled, moment.cursor)
        # Colours and borders hold still during the reveal and start moving after it.
        fill = colour_field(self.style.color_mode, self.size, 0.0, self.style.color_value)
        if moment.glow > 0:
            halo = _mask_image(outline if outline is not None else glyph).filter(
                ImageFilter.GaussianBlur(2 * self.unit)
            )
            strength = 1.6 * moment.glow
            canvas = Image.composite(fill, canvas, halo.point(lambda value: min(255, round(value * strength))))
        canvas = self._paint(canvas, outline, glyph, fill)
        self._border(canvas, 0.0)
        return canvas

    def _scroll_neon(self, t: float) -> Optional[List[LetterState]]:
        """On a scrolling message, each neon letter lights up soon after it comes onto the sign."""
        if self.reveal != "neon" or self.style.effect not in SCROLL_EFFECTS:
            return None
        if self._scroll_neon_on is None:
            rng = random.Random(self._seed)
            block_width = self.block.size[0]
            on = []
            for letter in self.letters:
                place = letter.placement
                if self.style.effect == "scroll_right":
                    arrives = (block_width - place.x - place.advance) / self.scroll_speed
                else:
                    arrives = place.x / self.scroll_speed
                # It arrives cold; its flicker window (0.3) starts after it is on the sign.
                on.append(arrives + rng.uniform(0.35, 0.7))
            self._scroll_neon_on = on
        if t >= max(self._scroll_neon_on, default=0.0):
            return None
        return [neon_state(t, on, 0.3, self._seed, index) for index, on in enumerate(self._scroll_neon_on)]

    def _bend(self, mask: np.ndarray) -> np.ndarray:
        """Wraps the flat strip round a cylinder: columns bunch up toward the box's edges."""
        if self._globe_map is None:
            left, _, right, _ = self.box
            radius = (right - left) / 2
            angle = np.arcsin(np.clip((np.arange(right - left) + 0.5 - radius) / radius, -1.0, 1.0))
            source = np.clip(self._globe_width / 2 + radius * angle - 0.5, 0.0, self._globe_width - 1.001)
            columns = np.floor(source).astype(np.int64)
            # Letters darken as they turn away, which reads as depth.
            shade = np.zeros(self.size[0], np.float32)
            shade[left:right] = 0.3 + 0.7 * np.cos(angle)
            self._globe_map = (columns, (source - columns).astype(np.float32), shade)
        columns, weights, _ = self._globe_map
        bent = np.zeros((self.size[1], self.size[0]), np.float32)
        bent[:, self.box[0]: self.box[2]] = mask[:, columns] * (1 - weights) + mask[:, columns + 1] * weights
        return bent

    def _globe(
        self, canvas: Image.Image, position: Tuple[int, int], states: Optional[List[LetterState]], t: float
    ) -> Image.Image:
        strip = (self._globe_width, self.size[1])
        if states is None:
            outline, glyph = self._block_masks(position, strip)
        else:
            outline, glyph = self._letter_masks(position, states, strip)
        glyph = self._bend(glyph)
        fill = colour_field(self.style.color_mode, self.size, t, self.style.color_value)
        if outline is not None:
            outline = self._bend(outline)
            text = Image.composite(fill, self._outline_color, _mask_image(glyph))
            coverage = outline
        else:
            text, coverage = fill, glyph
        shade = self._globe_map[2]
        alpha = (coverage / 255.0)[..., None]
        pixels = np.asarray(canvas, np.float32) * (1 - alpha) + np.asarray(text, np.float32) * shade[None, :, None] * alpha
        return Image.fromarray(pixels.clip(0, 255).astype(np.uint8))

    def _motion_frame(self, t: float, base: Image.Image) -> Image.Image:
        """The message's own animation at effect time t (already scaled by speed)."""
        canvas = base.copy()
        self._draw_plate(canvas)
        effect = self.style.effect
        if effect == "pow":
            self._burst(canvas, t)
        scale, dx, dy, visible = self._motion(t)
        if visible and self.text.strip():
            glyph = _scaled(self.block.glyph, scale)
            outline = _scaled(self.block.outline, scale)
            width, height = (outline or glyph).size
            if effect in SCROLL_EFFECTS:
                offset = (t * self.scroll_speed) % self._travel()
                if effect == "scroll_right":
                    x = self.box[0] - self.block.size[0] + offset
                elif effect == "globe":
                    x = self._globe_width - offset
                else:
                    x = self.box[2] - offset
                y = self.box[1] + (self.box[3] - self.box[1] - height) / 2
            else:
                x, y = self._anchor(width, height)
            position = (round(x + dx), round(y + dy))
            states = self._scroll_neon(t)
            if effect == "globe":
                canvas = self._globe(canvas, position, states, t)
            elif states is not None:
                masks = self._letter_masks(position, states)
                canvas = self._paint(canvas, *masks, colour_field(self.style.color_mode, self.size, t, self.style.color_value))
            else:
                if outline is not None:
                    mask = Image.new("L", self.size)
                    mask.paste(outline, position)
                    canvas = Image.composite(self._outline_color, canvas, mask)
                mask = Image.new("L", self.size)
                mask.paste(glyph, position)
                fill = colour_field(self.style.color_mode, self.size, t, self.style.color_value)
                canvas = Image.composite(fill, canvas, mask)
        self._border(canvas, t)
        return canvas

    def frame(self, t: float = 0.0, base: Optional[Image.Image] = None) -> Image.Image:
        """The frame at time t, drawn over the message's background or over ``base``."""
        base = self.background if base is None else base
        lead = self.lead_seconds
        if t < lead:
            return self._reveal_frame(t, base)
        return self._motion_frame((t - lead) * self.speed, base)

    def frames(self, seconds: Optional[float] = None) -> Iterator[Tuple[Image.Image, float]]:
        """Yields (image, duration): the reveal, then the message for ``seconds``.

        Infinite for animations unless seconds is given.
        """
        step = 1.0 / self.FPS
        lead = self.lead_seconds
        index = 0
        while index * step < lead:
            yield self.frame(index * step), step
            index += 1
        if not self.animated:
            yield self.frame(lead), seconds if seconds is not None else 1.0
            return
        index = 0
        while seconds is None or index * step < seconds:
            yield self.frame(lead + index * step), step
            index += 1


def load_background(path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
    """First frame of an image or GIF, cropped to fill the panel; None if unreadable."""
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            source = opened.convert("RGB")
    except (OSError, ValueError):
        return None
    scale = max(size[0] / source.width, size[1] / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def preview_frames(renderer: "TextRenderer", fps: float = 12.0, max_seconds: float = 8.0) -> List[Image.Image]:
    """Frames for a browser preview: one still, or a looping sample of the reveal and animation."""
    lead = renderer.lead_seconds
    if not renderer.animated and not lead:
        return [renderer.frame(0.0)]
    hold = max(2.4, renderer.cycle_seconds) if renderer.animated else 1.6
    seconds = min(max_seconds, lead + hold)
    count = max(1, round(seconds * fps))
    return [renderer.frame(index / fps) for index in range(count)]


def style_from_mapping(values: Dict[str, object]) -> TextStyle:
    """Builds a style from a DB row or API payload, ignoring unknown keys."""
    defaults = TextStyle()
    known = {key: values[key] for key in defaults.__dataclass_fields__ if key in values and values[key] is not None}
    for flag in ("bold", "italic", "outline", "backing"):
        if flag in known:
            known[flag] = bool(known[flag])
    if "font_size" in known:
        known["font_size"] = int(known["font_size"])
    return replace(defaults, **known)

"""Text reveals: one-time entrances that play before a message's ongoing motion.

This module only keeps time. Given a message's characters it works out, for any
moment of the reveal, where each letter is and how lit it is; ``TextRenderer``
draws the result. Every reveal ends with each letter settled, and the renderer
then hands over to the message's normal frames, so a reveal always ends on
exactly the still frame the message has without one.
"""

from dataclasses import dataclass, field
import random
from typing import List, Optional, Sequence, Tuple

from .easing import clamp01, ease_out_back, ease_out_cubic

REVEALS = ("none", "typewriter", "letters_rise", "drop_in", "matte_rise", "neon")
# A scrolling message is never still, so only a reveal that leaves letters in place fits it.
SCROLL_REVEALS = frozenset({"none", "neon"})
# However long the message, it is fully shown within this many seconds.
CAP_SECONDS = 1.5
NEON_DIM = 0.15  # an unlit neon letter still shows faintly, like a cold tube


@dataclass(frozen=True)
class LetterState:
    dx: float = 0.0
    dy: float = 0.0
    alpha: float = 1.0  # the whole letter, outline included
    light: float = 1.0  # the letter's face only, for neon tubes not yet lit


HIDDEN = LetterState(alpha=0.0)
SETTLED = LetterState()


@dataclass
class RevealFrame:
    states: List[LetterState]
    block_dy: float = 0.0  # the whole block's offset, for the matte rise
    matte: Optional[Tuple[float, float]] = None  # (line width 0..1, line alpha); clips below the line
    cursor: Optional[int] = None  # the typewriter cursor sits before this character
    glow: float = 0.0  # 0..1 strength of the neon halo


def _fit(count: int, each: float, gap: float) -> Tuple[float, float]:
    """Shrinks the stagger, then each letter's own time, so a reveal fits the cap."""
    each = min(each, CAP_SECONDS)
    if count > 1 and each + (count - 1) * gap > CAP_SECONDS:
        gap = (CAP_SECONDS - each) / (count - 1)
    return each, gap


def _flicker(seed: int, index: int, slot: int) -> float:
    return random.Random(seed * 1_000_003 + index * 7919 + slot).random()


def neon_state(t: float, on: float, window: float, seed: int, index: int) -> LetterState:
    """A letter that flickers in the window before ``on``, then stays lit."""
    if t >= on:
        return SETTLED
    start = on - window
    if t < start:
        return LetterState(light=NEON_DIM)
    progress = (t - start) / window
    slot = int((t - start) / (window / 5))  # about five flickers per letter, never a fast strobe
    return SETTLED if _flicker(seed, index, slot) < progress else LetterState(light=NEON_DIM)


@dataclass
class Reveal:
    """The schedule of one message's reveal.

    ``chars`` is every character in reading order, spaces included. ``speed`` is
    the message's motion speed multiplier. Distances are in pixels and come from
    the renderer, which scales them with the sign.
    """

    kind: str
    chars: Sequence[str]
    speed: float = 1.0
    rise: float = 0.0  # letters_rise: how far below its place a letter starts
    drop: float = 0.0  # drop_in: how far above; enough to start off the sign
    matte_depth: float = 0.0  # matte_rise: the block's height plus the gap to its line
    seed: int = 0
    duration: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        if self.kind not in REVEALS:
            self.kind = "none"
        self.visible = [index for index, char in enumerate(self.chars) if char.strip()]
        if not self.visible:
            self.kind = "none"
        slow = 1.0 / max(self.speed, 1e-3)
        count = len(self.visible)
        if self.kind == "typewriter":
            # A beat after the last letter, with the cursor blinking, then the still.
            self.tail = min(0.3 * slow, CAP_SECONDS / 3)
            self.gap = min(0.07 * slow, (CAP_SECONDS - self.tail) / max(1, len(self.chars)))
            self.duration = len(self.chars) * self.gap + self.tail
        elif self.kind == "letters_rise":
            self.each, self.gap = _fit(count, 0.35 * slow, 0.045 * slow)
            self.duration = self.each + (count - 1) * self.gap
        elif self.kind == "drop_in":
            self.each, self.gap = _fit(count, 0.5 * slow, 0.06 * slow)
            self.duration = self.each + (count - 1) * self.gap
        elif self.kind == "matte_rise":
            self.duration = min(0.9 * slow, CAP_SECONDS)
        elif self.kind == "neon":
            scale = min(slow, CAP_SECONDS / 1.15)
            rng = random.Random(self.seed)
            self.window = 0.3 * scale
            self.on = [rng.uniform(0.1, 0.65) * scale + self.window * 0.5 for _ in self.visible]
            self.lit = max(self.on)
            self.duration = self.lit + 0.35 * scale
        else:
            self.duration = 0.0

    def at(self, t: float) -> RevealFrame:
        states = [HIDDEN] * len(self.chars)
        if self.kind == "typewriter":
            shown = min(len(self.chars), int(t / self.gap)) if self.gap > 0 else len(self.chars)
            for index in range(shown):
                states[index] = SETTLED
            typing = shown < len(self.chars)
            # Solid while typing, then blinking twice a second: a small mark, never a strobe.
            cursor = shown if typing or int((t - len(self.chars) * self.gap) / 0.25) % 2 == 0 else None
            return RevealFrame(states, cursor=cursor)
        if self.kind in {"letters_rise", "drop_in"}:
            for order, index in enumerate(self.visible):
                local = (t - order * self.gap) / self.each if self.each > 0 else 1.0
                if local < 0:
                    continue
                if self.kind == "letters_rise":
                    states[index] = LetterState(dy=(1 - ease_out_back(local)) * self.rise, alpha=clamp01(local / 0.4))
                else:
                    states[index] = LetterState(dy=-(1 - ease_out_back(local, 2.2)) * self.drop)
            return RevealFrame(states)
        if self.kind == "matte_rise":
            progress = t / self.duration if self.duration > 0 else 1.0
            line_width = ease_out_cubic(progress / 0.25)
            line_alpha = 1.0 - clamp01((progress - 0.75) / 0.25)
            lift = ease_out_cubic((progress - 0.15) / 0.65)
            for index in self.visible:
                states[index] = SETTLED
            return RevealFrame(states, block_dy=(1 - lift) * self.matte_depth, matte=(line_width, line_alpha))
        if self.kind == "neon":
            for order, index in enumerate(self.visible):
                states[index] = neon_state(t, self.on[order], self.window, self.seed, index)
            glow = 0.0
            if t >= self.lit:
                phase = (t - self.lit) / max(1e-6, self.duration - self.lit)
                glow = phase / 0.3 if phase < 0.3 else max(0.0, (1 - phase) / 0.7)
            return RevealFrame(states, glow=glow)
        return RevealFrame([SETTLED] * len(self.chars))

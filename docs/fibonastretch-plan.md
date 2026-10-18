# Plan: Fibonastretch fit (fill the bars with a Fibonacci stretch)

**Revised 2026-10-18 after testing on the sign:** the strip runs the other way from the original build. Source line 0 (the picture's edge) is 1 px wide, then 1, 2, 3, 5, …, growing to the widest step at the sign's edge, with the last step cut where the gap ends (128 px: 1, 1, 2, 3, 5, 8, 13, 21, 34, 40). This removes the seam and the "picture added to itself" banding that the widest-at-the-edge layout produced; a second revision followed the owner's intent that each pixel scales in **both** dimensions as it walks the pattern: a step *n* wide turns each pixel into an *n*×*n* square, overlapping neighbours are combined with an operator, and the bars blur more toward the sign's edge (about 0.13 ms per frame at 96×64 and 0.66 ms at 384×128 on a Mac). The setting is `fit_blur` (`average` default, `none`, `lighten`, `screen`, `additive`, `multiply`; UI names Blur, Crisp, Glow, Bright, Dense, Dark; `none` is the one-dimensional stretch, which the owner found kept each pixel clear). `screen` and `additive` wash the bars out to white, which draws the most power. It replaced the first `fit_blend` setting and a short-lived `fit_bars`; a database that ran those builds keeps their unused columns, since schema changes are additive. **Mirror.** A sibling fit, `mirror`: each bar is its own side of the picture reflected about the edge, exact 1:1 pixels, like a window cut from [mirrored copy | picture | mirrored copy] (reflecting back and forth if the bars are wider than the picture). The owner confirmed exact copies from the same side, mirrored left and right from the centre. It first shipped for a few hours as `tile`, which took pixels from the opposite side (a mistake in the request); `tile` is kept as an alias so saved items still play. No stretch and no blur, so the blur select is hidden for it, and it is cheap enough per frame that it is not cached. Video uses the same ffmpeg-scales, Python-fills path as Fibonastretch.

**Cache.** Frames are fitted once and read back from disk (`rendering/fitcache.py`): GIFs and videos with this fit play live the first time while a low-priority worker writes raw sign-size frames to a cache directory; later plays read the file. The key covers the media file (name, mtime, size, so a finished video proxy supersedes the original), sign size, fit and blur, and a version constant to bump when the look changes. Limits: 1 GiB total (oldest pruned first), at most a quarter of it per file (bigger clips stay live, with a marker so they are not retried), files never backed up, previews never trigger builds. Stills are not cached: one fit costs less than reading a file. The sections below describe the first design, including the overlap blend and the `fit_blend` field, which no longer exist.

**Status:** built, deployed to the sign, and pushed on 2026-10-18, with every step below done including the video path. On the Pi, the stretch costs about 2 ms per frame at 96×64 and 10 ms at 384×128 live, and reading from the cache costs 0.08 and 0.36 ms. Real video through the running service has not been timed. The source note is `specs/Fibonastretch.txt`, which lives only on the owner's machine (`specs/` is not tracked).

## The request

When media does not match the sign's aspect ratio, fill the empty bars by
stretching the media's own edge pixels outward in a Fibonacci progression, and
blend where the stretch overlaps the picture. The spec's worked example is a
640×480 clip on a 1024×1024 sign.

## How I read the spec

1. **Contain-fit first.** Scale the media (aspect preserved) until one side
   equals the sign and the other is short: 640×480 → 1024×768.
2. **The gap** on the short axis is split between the two edges: 256 px → 128
   per side (an odd gap gives the extra pixel to the far side).
3. **Per edge, build a strip** from the media's outermost source pixel rows or
   columns. Source pixel *i* from the edge is widened to F(i) output pixels.
   The outermost source pixel gets the *largest* width and the width shrinks
   toward the picture, so the strip meets the picture at width 1 and joins
   it without a visible step.
4. **Choose the fewest terms whose sum covers the gap.** The spec quotes sums
   of 88 and 143, which only match the series **1, 1, 2, 3, 5, 8, 13, 21, 34,
   55** (the spec's own list drops the second 1). Sums: up to 34 is 88, which
   is short of 128; up to 55 is 143, so the strip is 15 px longer than the
   gap.
5. **The overlap is those 15 px.** The strip reaches 15 px past the gap and
   lies over the first 15 px of the picture. There, the strip pixel and the
   picture pixel are combined with the chosen blend operator. This is the
   only place blending happens.

The strip is replicated pixels, not interpolated: a smear, which is the look
the spec describes, and the diffuser softens the steps anyway.

If the media is wider than the sign, the same thing happens on the vertical
axis. If the aspect ratios match, there is no gap and nothing changes.

## What the rendering code does today

| Piece | Where | Relevance |
|---|---|---|
| `fit_image` | [rendering/fit.py](../src/matrix_player/rendering/fit.py) | One function for stills and GIF frames. `FIT` and `LETTERBOX` already do the same thing (contain-fit on black); the UI maps old `letterbox` to `fit`. |
| `ImageFrameSource` | [rendering/frames.py](../src/matrix_player/rendering/frames.py) | Calls `fit_image` once per still. |
| `GifFrameSource` | same | Calls `fit_image` per frame, caches up to 600 fitted frames. Anything per-size must be built once, not per frame. |
| `VideoFrameSource` | same | Fits **inside ffmpeg** (`scale` + `pad`); Python receives finished sign-size frames and never sees the media at its own size. |
| `FitMode` | [models.py](../src/matrix_player/models.py) and rendering/fit.py | Declared twice, with `schemas.py` importing the models one. A new mode must be added to both, or the two should be unified. |
| Per-item `fit_mode` | `playlist_items` column, `SendCommand`, [playback.py](../src/matrix_player/playback.py) `build_media_source`, [preview.py](../src/matrix_player/preview.py) | Reaches the source builders as a bare string. Previews go through `build_media_source`, so they match the panel automatically. |
| `load_background` | [rendering/text.py](../src/matrix_player/rendering/text.py) | Crop-to-fill for text backgrounds; leave alone. |

Two things follow from this. First, stills and GIFs are easy: the new fit is
one more branch in `fit_image`. Second, video is the hard case, because the
fit happens in ffmpeg. See "Video" below.

## Design

### A reusable geometry object

New module `rendering/fibonacci.py` with a `FibonacciFit` built once per
(source size, sign size). It does the arithmetic once:

- contained size and axis, gap per side;
- the term list (fewest Fibonacci terms with sum ≥ gap) and the per-source-pixel
  widths for each strip;
- a `numpy` gather index that maps each strip pixel to its source row or column
  (`np.repeat(np.arange(k), widths)`), and the overlap range.

Then `apply(contained: ndarray) -> ndarray` places the picture, writes both
strips with one `take` each, and blends the overlap. Per frame this is a copy,
two gathers, and a blend over at most ~38% of one strip, so it should cost far
less than the existing per-frame work. Measure on the Pi at the largest sign
size in use before trusting that.

### Edge cases to decide in code and test

- **No gap:** return the contained image unchanged.
- **Too few source pixels:** if the series needs more than half the picture's
  pixels on that axis (a very thin or tiny source on a big sign), cap at half
  and widen the outermost term to make up the difference, so the strips never
  read the same source pixels. 
- **Gap of 1–2 px:** a single term of width 1 or 2; the overlap is zero or
  tiny. Fine.
- **Odd gap:** near side gets `gap // 2`, far side the rest.
- **Alpha:** `fit_image` already flattens to RGB first, so strips are opaque.

### Blend operators

Applied per channel on 0–1 values, with `b` the picture beneath and `s` the
strip:

| Operator | Result | Note |
|---|---|---|
| `screen` (default) | `1 − (1−b)(1−s)` | Brightens, never clips: soft seam |
| `additive` | `min(1, b + s)` | Brighter, can hit white |
| `multiply` | `b·s` | Darkens: seam recedes |
| `subtractive` | `max(0, b − s)` | Strong darkening |
| `overlay` | `2bs` if `b < ½`, else `1 − 2(1−b)(1−s)` | Contrast |

The spec says "not exclusive to", so the registry is a plain dict that tests
iterate over, and these are cheap to add later: `lighten`, `darken`,
`difference`, `average`, and `normal` (strip replaces the picture, no
blending). Unknown names fall back to `screen`, never raise on the render path.

### Where the choice lives

- `FitMode.FIBONACCI = "fibonacci"` in both enum declarations (or, better,
  make `models.FitMode` import the rendering one, so there is one list).
- A new **`fit_blend`** setting, kept separate from `fit_mode` so every
  operator works with the mode. Stored as `playlist_items.fit_blend TEXT NOT
  NULL DEFAULT 'screen'` through `COLUMN_MIGRATIONS` (additive, so older releases
  still run), on `PlaylistItemBase`, `SendCommand`, the playlist routes' column
  list, and the `build_media_source(..., fit_blend)` argument.
- Environment default `MATRIX_PLAYER_FIT_BLEND` is *not* needed: the column
  default carries it. (If one is added later, it must go in `.env.example`.)

### Video

Today ffmpeg returns sign-size, padded frames. For this mode, change only the
ffmpeg filter: scale to the exact contained size (computed in Python from the
probed source size, passed as `scale=W:H`, no `pad`) and read frames of that
size; then `FibonacciFit.apply` fills the sign-size frame. The video proxy
worker and its stored copies are unaffected.

This needs the source's size before the first frame, so `VideoFrameSource` gets
an `ffprobe` size probe next to `_probe_fps` (same timeout and fallback; if the
probe fails, fall back to the ordinary `fit` for that item, never fail
playback). The frame size handed to `Image.frombytes` becomes the contained
size in this mode. The ffmpeg tests run on the owner's Mac now that ffmpeg is
installed there, but speed must still be checked on the Pi.

### UI (plain words)

Add to the "Picture fit" select in [index.html](../src/matrix_player/static/index.html)
(the `FIT_LABEL` map in app.js too): **"Stretch the edges (Fibonacci)"**. When
chosen, show a second select, "Where the edges meet the picture", with plain
names: *Glow* (screen, default), *Brighter* (additive), *Darker* (multiply),
*Cut away* (subtractive), *Contrast* (overlay). Hide it for the other fits.
The dialog's "Show now" path sends both fields. The UI check skill covers
this: run `tools/ui_check.py` after the change.

## Power

Letterbox bars are black and free. This mode fills them with the picture's own
edge colours, so a clip with a white or yellow edge lights the **whole** panel
and draws toward the heaviest load in AGENTS.md (about 80% of full white,
every frame). `additive` and `screen` add a little more in the overlap. Two
consequences:

- The planned per-item power estimate must be computed from the *fitted*
  frames, not the raw media, or it will understate this mode.
- Worth showing in the picker's help text: "Fills the empty bars, which uses
  more battery on bright pictures."

## Build order

1. `rendering/fibonacci.py` with the geometry, the blend registry, and unit
   tests; no wiring yet. This is where the 640×480 → 1024×1024 example
   becomes a test: expect gap 128 per side, widths summing to 143, overlap 15.
2. Wire into `fit_image` for stills and GIFs (`fit_blend` argument through
   `ImageFrameSource`, `GifFrameSource`, `build_media_source`).
3. Schema, migration, routes, preview, and `SendCommand`; unify `FitMode`.
4. UI select and labels; run the `ui-check` skill.
5. Video path (size probe, scale-only filter, `apply` per frame); verify on
   the Pi, including ms per frame at the largest size.
6. Optional extra operators.

Each step can be committed separately and is safe on its own; steps 1–2 give a
working feature for stills and GIFs before video is touched.

## Tests

- **Arithmetic:** the spec example; odd gap; zero gap; thin source capped at
  half the axis; vertical as well as horizontal.
- **Sign sizes:** the same media at 96×64, 128×64, 192×128, and 384×128, as
  `test_text_looks_the_same_on_signs_of_any_size` does for text: output is
  exactly the sign size, the picture region is unchanged outside the overlap,
  and the outermost strip pixel equals the picture's edge pixel.
- **Blend operators:** each returns values in range and matches a hand-worked
  pixel; an unknown name behaves as `screen`.
- **No gap or mismatch:** same aspect ratio returns the contained image.
- **Migration:** an older database without `fit_blend` opens and defaults to
  `screen`.
- **Video:** ffmpeg test (runs locally and on the Pi) that a 4:3 clip on a wide sign yields
  frames of the sign's size with filled bars; the size-probe failure falls
  back to `fit`.
- **Performance:** a timing test or script at the largest size, run on the Pi.

## Open questions for the owner

1. **Series reading.** I used 1, 1, 2, 3, 5, … because that matches the
   spec's sums of 88 and 143. If you meant 1, 2, 3, 5, … (sums 87 and 142),
   the 128-px example would overlap by 14 instead of 15; one constant.
2. **Strip direction.** I put the widest step at the outer edge, joining the
   picture at width 1. The reverse (a sharp step at the picture) is easy but
   looks like a visible seam.
3. **Feathering.** The spec blends the overlap at full strength. A ramp from
   0 at the inner end to full at the outer end would hide the seam better; I
   would add it as an option only if the plain version looks harsh on the
   panel.
4. **Interpolation.** Replicated pixels (blocky smear) versus smoothly
   interpolated strips. I started with replication per the spec.

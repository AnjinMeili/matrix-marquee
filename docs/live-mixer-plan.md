# Plan: the layer mixer, chromakey, and known senders

**Status:** planned 2026-10-19, and the owner settled the open questions the
same day (see "Decided"). **Phase 1, words on top of pictures, GIFs, and
videos, is built** (see "As built"); not yet deployed or timed on the Pi.
Phases 2–4 wait on the receiver work. The source note is
`specs/DDS Receiver.txt`, which lives only on the owner's machine (`specs/` is
not tracked). It extends [protocol-receiver-plan.md](protocol-receiver-plan.md)
and reuses the backing plate from
[text-transitions-plan.md](text-transitions-plan.md).

## The request

The note, in short:

1. The sign plays a **DDS stream from a known sender.** If the sender is
   known, its saved permissions apply; if not, the sign's administrator is
   notified.
2. **Streams mix with the sign's own content** using the standard blend
   modes.
3. **Chromakey,** with the operator choosing whether layer A or B is keyed.
4. **The same mixer puts text panes over media,** with options that keep the
   text clear and readable.

"DDS" is **DDP**, the Distributed Display Protocol that MatrixPrism streams
and the receiver plan already covers (confirmed by the owner). The receiver
(matrix-jack, `matrix-dmx`) also takes Art-Net and sACN, so everything below
applies to all three.

## What changes in the receiver plan

The receiver plan decided that **no stream ever takes over by itself**: every
stream raises a display request that a connected person approves. "Always
allow this sender" was an optional extra, off by default. This note makes
remembering senders the normal path:

| | Receiver plan (2026-10-17) | With this note |
|---|---|---|
| Unknown sender | Display request to everyone with the page open | The same: a notification, answered by someone with the operator PIN |
| Known sender | Optional "always allow", off by default | **Its saved profile applies at once:** it shows without a request only if the profile grants that right; otherwise it is asked about or ignored as the profile says |
| What is saved | An address | A **sender profile** (below) |

This stays inside the earlier rule in spirit, because a saved profile is a
person's decision made earlier, behind the PIN, rather than no decision.
These earlier decisions still hold unchanged:

- **Receive and Discoverable are off after every power-up.** A known sender
  can only show while an admin has turned receiving on. Profiles persist;
  the switches never do.
- The 5-minute idle timeout, the live banner with **Stop**, the audit log,
  and approvals needing the PIN.

### Sender profiles

A profile is saved the first time someone answers an unknown sender with
"Allow and remember", and edited or deleted on the Live input card in
Settings, behind the PIN.

**Rights are granted, never assumed.** Being known does not by itself let a
sender show: a profile carries a right, and only "Show without asking" skips
the request. A remembered sender without that right is still asked about
each time, with its saved mix settings pre-filled.

| Field | Meaning | Default |
|---|---|---|
| Name | Shown in banners and logs ("MatrixPrism laptop") | the address |
| Who | Address, plus the MAC address the sign saw for it on its own Wi-Fi | as first seen |
| Right | **Show without asking**, **Ask each time**, or **Ignore** | Ask each time; "Show without asking" is an explicit choice in the form |
| How it shows | Full sign, or as a layer in the mixer (below) with its blend, key, and opacity | full sign |
| Session limit | Each stream shows for at most this long, then the playlist resumes | 10 minutes |
| Expires | The profile itself lapses, so a forgotten laptop loses its pass | 7 days; set per profile, with a sign-wide default on the Live input card (1 hour to 1 year, or never) |

**How sure "known" is.** DDP, Art-Net, and sACN carry no sender identity or
signature: anyone on the network can send a packet that claims any source
address. On the sign's own Wi-Fi the real boundary is the Wi-Fi passphrase;
the address and MAC match keeps out mistakes (a second laptop, a phone
running LedFx), not a determined attacker who already has the passphrase.
The Settings card should say so plainly. A profile is matched on address
**and** MAC, and a mismatch is treated as unknown.

### Notifying the administrator

"Administrator" means whoever holds the operator PIN, so notifications go out
over the `/api/events` channel from the receiver plan, as there: to every
open page, with the answer buttons working only on a phone that has entered
the PIN. The sign is offline, so there is no push to a closed phone; a
request waits (default 5 minutes) for the next person to open the page. The
answers gain one choice: **Allow and remember…**, which opens the profile
form pre-filled with the request's details.

## The mixer

One compositor that combines two layers per frame. It serves three uses, and
the first needs no receiver at all:

| Use | Layer A (bottom) | Layer B (top) |
|---|---|---|
| **Text over media** | a picture, GIF, or video | a message |
| **Stream over the sign** | the playlist as it plays | the live stream |
| **Sign over a stream** | the live stream | a message (a caption over MatrixPrism's visuals) |

Today a message's background picture shows its first frame only, so a message
over a video or animated GIF is new.

### Blend modes

The standard set, worked out per pixel with numpy at the sign's size:

| Mode | UI name | What it does on the panel |
|---|---|---|
| normal | Over | B covers A, at B's opacity |
| add | Add light | Sums the light; bright areas clip to white |
| screen | Brighten | Like add but gentler; never darker than either layer |
| multiply | Darken through | Black in either layer stays black; white passes the other through |
| lighten | Brightest wins | Per channel, the brighter of the two |
| darken | Darkest wins | Per channel, the darker |
| difference | Difference | Strong, psychedelic; useful for music visuals, hard to read text through |
| overlay | Overlay | Contrast boost: multiply in darks, screen in lights |

Each layer also has **opacity** (0–100%). The cost is a few array operations
per frame: well under a millisecond at 96×64, a few at 384×128.

**Power.** Add, screen, and lighten put more light on the panel than either
layer alone; a bright stream added over a white message can approach full
white, the heaviest load the sign has (about 14–15 A at near-white). The
library's planned power estimate should treat a mix as its brighter result,
and the planned limiter applies after mixing.

### Chromakey

Keying makes part of one layer see-through so the other shows. The operator
picks **which layer is keyed, A or B**, and how:

- **Black is see-through** (a luma key), the common case on LED panels:
  MatrixPrism and most pixel senders draw on black, and black pixels are
  unlit anyway. Settings: threshold and softness.
- **A chosen colour is see-through** (a chroma key): a colour picker, with
  tolerance and softness, matched in a hue and saturation space so lighting
  gradients key cleanly.

Keying A means A's keyed areas show B underneath, which flips the layer
order for those areas; the mixer handles it by swapping the layers' roles
before blending, so the result is always "the keyed layer has holes, the
other shows through". Softness gives a feathered edge, which the diffuser
softens further.

### Readability for text over media

Options on the message's layer when it sits over media or a stream. The
first two exist already, and the third was built with the reveals:

| Option | Effect |
|---|---|
| Outline | The existing stroke, in a chosen colour |
| Heavy fonts | The existing bundled display fonts |
| **Dark box behind** | The backing plate built on 2026-10-19 |
| **Dim the picture** | Darken layer A by a set amount, everywhere or only in a band behind the text |
| **Blur behind the text** | Soften layer A inside the text's box, so busy detail does not fight the letters |
| **Glow** | A soft halo of the outline colour round the letters (shares code with the neon reveal's glow) |
| **Auto colour** | Pick the text colour (white, yellow, or black) for the most contrast with what is under it, measured once per item, not per frame, so it does not flicker |
| **Where** | Full sign, top band, or bottom band ("lower third"), so text can sit over a video without covering all of it |

The diffuser blurs 1-pixel detail, so the defaults lean on the heavy options:
outline on, dark box on, dim 40% when the text covers the whole sign.

### Where it lives

- **Rendering:** `rendering/mixer.py`, a `MixFrameSource` taking two frame
  sources and a mix description. Layer A sets the clock (a video's frame
  rate, or the playlist's); layer B is sampled at A's times. A live stream is
  sampled as "latest frame", never waited for, so a stalled sender cannot
  stall the sign.
- **Data:** a playlist item gains an optional overlay: a message, its
  position, its blend, key, and opacity, and its readability options. One
  additive table, `item_overlays`, keyed by the playlist item, keeps the
  item table unchanged for older releases. A message can also be shown over
  media with "Show now" without a playlist.
- **Live layer:** a sender profile's "How it shows" is the same mix
  description, applied over or under whatever the player is showing.
- **Previews** render the mix with the sign's renderer, as now. A stream
  that is not connected previews as its request thumbnail, or a test
  pattern.
- **UI:** in the playlist item dialog, "Add words on top" opens a short
  form: which message, where, how it blends (Over by default, with the
  others under "More"), "black is see-through" or a colour key, and the
  readability options. The copy stays plain: "Words on top", "Make black
  see-through", not "luma key on layer B".

## Phases

1. **The mixer and text over media** (about a day): `mixer.py` with blend
   modes, opacity, both keys, and the readability options; the item overlay;
   the editor and previews; tests at several sizes, including that "Over" at
   100% with no key equals the plain layers, and that each key is exact on
   pure colours. Useful at once, with no receiver.
2. **The receiver plan's phases 1 and 2:** the `/api/events` channel and the
   operator PIN. Unchanged from that plan.
3. **Sender profiles** with "Allow and remember", the Settings card, and
   expiry. Can be built and tested before streams exist by faking requests.
4. **The receiver plan's phases 3 and 4** (the bridge and live input), now
   showing streams through the mixer according to the profile.

## Decided (2026-10-19)

- **"DDS" is DDP.**
- **A known sender shows without asking only if it has been granted that
  right** in its profile ("Show without asking"). Otherwise a remembered
  sender is still asked about, or ignored, as its profile says. Receive
  stays off after every power-up, so even a granted sender shows only while
  an admin has receiving turned on.
- **Text panes are both:** a message can cover the whole sign or sit in a
  top or bottom band, with full sign the default.

- **Profile expiry is configurable, with a secure default:** 7 days, long
  enough to cover a multi-day event and short enough that a forgotten laptop
  loses its pass within a week. Set per profile and as a sign-wide default
  (1 hour to 1 year); "never" is allowed but never the default.
- **Text over media is built first** (phase 1).

## As built: phase 1 (2026-10-19)

- **Rendering:** `rendering/mixer.py`. `MixFrameSource` wraps any media
  source; the message is drawn by the sign's text renderer twice, over black
  and over white, which recovers its exact colour and alpha, so the dark
  box, borders, reveals, and globe all work as words on top. A still message
  is drawn once and reused.
- **Clock:** the media sets it; moving words are drawn at 30 frames a second
  even over a still picture, and the message's time runs on across the
  media's loops, so a reveal plays once and is not restarted by a short GIF.
  The reveal's time is added to the item's, as for messages.
- **Data:** table `item_overlays`, one row per playlist item, cascading when
  the item is deleted and blocking deletion of a message in use. The API
  carries it as `overlay` on playlist items (media items only). Duplicating
  a playlist copies overlays.
- **Band:** a top or bottom band is 40% of the sign's height; dimming and
  blur apply to the band (or the whole sign for "Whole sign").
- **Keys:** the colour key compares chroma divided by brightness, so a green
  screen keys evenly in light and shade; near-black is kept. Keying the
  picture puts it on top, with the words showing through its holes on black.
- **UI:** "Words on top" in the playlist item dialog for pictures and videos:
  message, where, darken (default Some, 40%), blur, soft halo, pick the
  clearest colour; blend, strength, and see-through under "More ways to
  mix". The item's summary line names the message.
- **Cost on a Mac**, with a real ffmpeg video, a pulsing neon-revealed
  message, band, dim, and halo: 1.2 ms a frame at 96×64 and 2.4 ms at
  384×128 including decode (3.3 ms with the Fibonastretch fit). Still to
  time on the Pi.
- **Not in phase 1:** words on top for "Show now" without a playlist, and
  softness in the UI (the API takes `key_softness`, default 10%).

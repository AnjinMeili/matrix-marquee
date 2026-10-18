# Plan: a companion app that designs signs with an LLM (not built)

**Status:** planned 2026-10-17. Nothing is built.

## The request

A companion app that uses an LLM to design and create new signs: slogans,
message designs, and playlists, ready to load onto the sign.

## Constraints that shape it

- **The sign is offline at a protest.** An LLM needs the internet, so the
  design work happens where the internet is: a laptop or phone on its own
  connection, before or during the event. The sign only receives finished
  designs.
- **A phone on the sign's Wi-Fi can't easily reach the internet.** The AP's
  wildcard DNS answers every name with the sign's own address, so a page
  served by the sign cannot call an LLM API from the phone's browser.
- **No API key ships with the sign.** The project may be open source; each
  user brings their own key, kept on their own device.
- **Every sign is already plain data.** A message is text plus a style
  (font, colours, effect, border, background), and a playlist is items
  plus timing and entrances. That is exactly what an LLM can write and
  what the sign's API already accepts, so nothing on the sign needs
  rewriting.

## Recommended shape

```
 brief ─► Sign Studio (laptop/phone, has internet) ─► design pack (JSON) ─► sign at protest.local
           │  Claude + tools                              │                  POST /api/import
           │  renders with the sign's own renderer        └─► or a file, imported from Settings
```

1. **Sign Studio**, a small Python app (command line plus a local,
   phone-friendly web page). It imports this project's renderer
   (`matrix_player.rendering`), so its previews and readability checks are
   exactly what the panel will show, with no sign needed.
2. **Design packs**: a versioned JSON file of messages and playlists. Sign
   Studio sends a pack to `http://protest.local` when the sign is
   reachable, or saves it to import later. Packs are also easy to share
   between people.
3. **On the sign**, one addition: `POST /api/import` (validate everything
   first, then create in one transaction; on name clashes, rename rather
   than overwrite) and an "Import design pack" button.

Later, optionally: an "Ideas" tab on the sign itself, enabled only when the
Pi has its own internet connection (wired or tethered), with the key in
`/etc/protest/protest.env`.

## The design pack

```json
{
  "format": 1,
  "created_by": "Sign Studio",
  "brief": "Housing rally, hopeful, short chants",
  "messages": [
    { "ref": "m1", "name": "Homes", "content": "HOMES NOT HOTELS",
      "font_family": "impact", "color_mode": "solid", "color_value": "#ffdd00",
      "effect": "pow", "border_style": "marquee", "motion_speed": "normal" }
  ],
  "playlists": [
    { "name": "Rally loop", "play_mode": "loop",
      "items": [ { "message": "m1", "duration_ms": 6000,
                   "transition_type": "impact", "transition_duration_ms": 600 } ] }
  ]
}
```

Message fields are the sign's existing text-panel schema; playlist items are
its existing item schema with a message reference in place of an id. Sign
Studio validates packs with the same Pydantic models the sign uses.

## The LLM loop

A workflow with tools, using the Claude API (Python `anthropic` SDK) on
`claude-opus-5-5`:

1. **Brief.** The user gives a cause, a tone (angry, funny, hopeful,
   solemn), length, languages, and how many ideas they want.
2. **Propose.** Claude drafts slogans and complete styles.
3. **Render and look.** Through tools, every draft is rendered by the
   sign's renderer. Claude gets back the frame as an image (enlarged from
   96×64; a few frames for animated designs) plus measurements, and looks
   at it.
4. **Revise** whatever fails the readability rules, then render again.
5. **Return the pack** as structured output (`output_config.format` /
   `client.messages.parse()` with the pack's Pydantic model), so it always
   parses.

Tools, all `strict: true`:

| Tool | Returns |
|---|---|
| `render_message(style)` | PNG frame(s) and metrics: chosen font size, lines, characters per line, text/background contrast, whether it animates, seconds per scroll pass |
| `render_sequence(playlist)` | sampled frames across entrances, to judge pacing |
| `list_capabilities()` | fonts, effects, colour modes, borders, transitions; generated from the code so they never drift |

**Readability rules**, measured rather than guessed. The renderer already
auto-fits text, so the font size it picks is the signal:

- reject below about a quarter of the sign's height in glyph height (16 px
  at 96×64), meaning too many words;
- at most three lines;
- text against background (or against the outline) at a contrast ratio of
  at least 4.5;
- a scrolling message must finish a pass within its time on screen.

API specifics that matter here:

- **Model:** `claude-opus-5-5` with adaptive thinking; effort `medium` (its
  default) for quick slogans, `high` for a full campaign pack.
- **Prompt caching:** the system prompt (the sign's capabilities, the rules,
  the pack schema, tone guidance) and the tool list are stable, so cache
  them; only the brief and the loop change.
- **Tool choice:** forced tool choice is not available on this model; use
  `auto` with instructions, and structured outputs for the final pack.
- **Refusals:** check `stop_reason == "refusal"` before reading content,
  and enable the server-side fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`). A protest slogan that is heated but
  legitimate should be retried there rather than dropped.
- **Cost:** a session of a few drafts with small images is likely to cost
  tens of cents. Measure it on real briefs before quoting a number.

## Learning from ratings

Users rate what is already on the sign, and higher ratings carry more
weight in new designs, so the designer learns the sign's own taste.

**On the sign, ratings are tags.**

- **Reserved tags `★1` to `★5`, one per item.** Setting `★4` replaces any
  other star tag on that item, and the tag API enforces this, so
  batch-tagging a selection with `★5` cleanly re-rates all of them.
  Ratings are optional; unrated items count as neutral.
- **Everything tags already do works for ratings:** tap the `★5` chip to
  filter, select a batch and give it `★4`, search "★5", and "Add these to
  a playlist" from a rating. One addition: a "★4 or better" filter option,
  since ordinary tags can't express "at least".
- **Messages get tags too**, the same way pictures and videos have them.
  That is useful by itself for grouping (a "housing" set, a "chants" set),
  and it makes star tags work the same across both.
- **The designer reads the star tags as numbers 1–5** for the weighting
  below.
- Ratings belong to the sign, not to a person, since there are no accounts.

**Inferred ratings from use.** Most items will never be rated by hand, so
the sign infers a rating from how each item is used. Deliberate choices
count more than time on screen:

| Signal | Weight | Why |
|---|---|---|
| Sent with "Show now" | strong | someone picked it on purpose |
| In several playlists | medium, per extra playlist | chosen again and again |
| Picked as a message background | medium | chosen for a look |
| Longer time or more repeats than the playlist's usual | medium | given more air time on purpose |
| Edited and kept | weak | worth refining |
| Times played and seconds shown | weak, log-scaled, as a share of the time it could have had | passive. An item in the always-on default playlist plays a lot without being anyone's favourite |
| Removed from a playlist soon after being added | negative | tried and dropped |
| Unused for a long time | fades towards neutral | taste moves on |

The signals add up to a score, which is mapped to an inferred 1–5 by
ranking it within the library.

- **Inferred ratings are never written as tags.** They change constantly as
  the sign is used, and as tags they would churn the user's tags and blur
  what was chosen with what was guessed.
- **A star tag always overrides the inferred rating.** Inferred ratings
  count at half the weight of star tags.
- The UI shows an inferred rating faintly ("≈★4") beside the tags; one tap
  turns it into a real `★4` tag.

Recording use needs per-item counters on the sign: plays, seconds shown,
sends, and when an item was added to or removed from a playlist. They are
kept as daily totals per item, gathered in memory and written every few
minutes, so they are light on the SD card, like the heat record. They
never record anything about viewers or anything outside the sign.

**The taste profile:** `GET /api/designer/profile`. It is also included in
backups and design packs, so Sign Studio can use it offline.

- **Exemplars:** the top-rated messages (text plus full style), highest
  first. Each item's influence is weighted by `rating − 3` (−2 to +2), so a
  5★ counts twice as much as a 4★, and 1–2★ items become examples to
  avoid.
- **Weighted preferences:** for each style attribute (font, colour mode,
  effect, border, motion speed, entrance), the sum of those weights, for
  example "Comic font +9, Fire −3, POW +6".
- **From pictures and videos:** tags, type, and a small palette of
  dominant colours from the thumbnails, weighted the same way. Highly
  rated pictures are offered as candidate text backgrounds.

**In the LLM loop:** the profile becomes a "house style" section of the
cached system prompt:

- the weighted preferences as plain statements ("strongly favoured:
  Impact, yellow on black, POW; avoid: fire colours, slow scroll");
- the top few exemplars rendered as images, so Claude sees what worked;
- one or two low-rated examples labelled as what not to do.

The brief still wins over the profile, so a request for something new is
not pulled back towards old favourites. Weights decay gently with age, so
the profile follows changing taste.

**Closing the loop:**

- Each card in Sign Studio can be approved with a rating. Imported designs
  keep their rating, and rating them later on the sign feeds the next
  session.
- **A side benefit:** the "Random" play mode could favour higher-rated
  items. That is a separate decision.

**Privacy:** the profile holds the user's own messages and small
thumbnails. It goes to the model only when the user runs a design session.
A setting can send tags and colours instead of thumbnails.

## Content guidance

Built into the system prompt and visible in the app: sharp, funny, angry is
fine; no threats, no incitement to violence, no slurs, no claims about
private individuals. Slogans should be about the cause. Claude's own
safeguards apply on top.

## Privacy and openness

- The key stays on the user's device; nothing is sent anywhere but the
  model API and the user's own sign.
- No telemetry. Packs are plain JSON the user can read before importing.
- The brief and the generated text are the only things sent to the model,
  never the sign's media or logs.

## Phases

1. **Ratings on the sign**: usage counters (built first, 2026-10-17, so
   the signal builds up over weeks of use), tags on messages, reserved
   `★1`–`★5` tags with the one-per-item rule and a "★4 or better" filter,
   and inferred ratings shown faintly. Useful on its own.
2. **Design pack format and `POST /api/import`** on the sign, with tests,
   plus the taste profile endpoint. Useful on its own for sharing designs.
3. **A measuring API in the renderer:** one function returning the metrics
   above, used by both the sign's editor (as warnings) and Sign Studio.
4. **Sign Studio, command-line version:** brief in, pack out, the full tool
   loop with the taste profile.
5. **Sign Studio web page:** a brief form, cards with live animated
   previews, approve, edit, or regenerate each card, and "Send to sign".
6. **Optional:** an "Ideas" tab on the sign when it has internet.

## Not in scope

**Generating images.** Claude does not generate images. Picture ideas could
be described for a person to make, or simple pixel art drawn with code;
that is a separate decision.

## Decisions for the owner

- Laptop first, or must it work from a phone alone?
- One key per user (recommended for open source), or a shared key?
- Languages to support, and translation of slogans?
- Should it design playlists and entrances, or messages only?
- How strict should the content guidance be beyond the model's own
  safeguards?
- Show inferred ratings in the UI, or keep them behind the scenes for the
  designer only?
- Should ratings also steer the "Random" play mode?
- May thumbnails of rated pictures go to the model, or only tags and
  colours?

# Agent guide

Instructions for an AI coding agent working in this repository: the software
for a protest sign, a Raspberry Pi 4 driving three outdoor 64×32 HUB75 panels
as one 96×64 display, controlled from a phone over the sign's own Wi-Fi.
`CLAUDE.md` imports this file, so Claude Code loads it at the start of every
session.

## Read first

1. [README.md](README.md): what the sign does, install, configuration, media
   handling, backups, display tuning.
2. [PROJECT_PLAN.md](PROJECT_PLAN.md) §12–14: decisions on record, the related applications, what is built
   and what is open.
3. The `docs/*-plan.md` for a feature before building it. Plans exist for the
   LLM sign designer, street-sound counting, and WLED output.
4. [needs.txt](needs.txt): the owner's own goals, as written. Do not edit it.

## Which skill to load

| When the task is… | Load | Why it matters here |
|---|---|---|
| Deploying to the sign, rolling back, or checking it after a change or power cycle | `deploy-sign` (project skill) | Approval rule, database backup, `scripts/verify-deploy.sh`, checking on the hardware |
| Any change to `src/matrix_player/static/` or to API responses the page renders | `ui-check` (project skill) | The captive-portal window and phone rendering faults that unit tests can't see |
| Building the LLM sign designer (`docs/companion-designer-plan.md`) | `claude-api` | Current model IDs, structured outputs, vision, caching, refusal handling |
| Any chart (the Heat & power chart, future usage or sound charts) | `dataviz` | Readable single-series charts in the dark UI |
| Reviewing a diff for bugs | `code-review` | |
| Changes to routes, uploads, backups, or anything reachable from the Wi-Fi | `security-review` | There is no login: anyone on the Wi-Fi can call every API |

## Working with the owner

- **Separate go-aheads.** "deploy", "commit", and "push" are each given
  explicitly. End a change by saying which one is next rather than doing it.
- **Commit straight to `main`**, and push only when asked.
- **"Plan" means a document, not code.** Write `docs/<topic>-plan.md` and add a
  line to `PROJECT_PLAN.md` §14 ("Status and open items").
- **The owner builds the hardware and knows it well,** so technical hardware
  detail is welcome. The sign's UI is for non-technical protesters, so its copy
  stays plain.

## Environment

- **Local machine:** macOS with Python 3.9 and no ffmpeg. The ffmpeg tests are
  skipped there, so check video work on the Pi.
- **The sign:** DietPi, Python 3.13, Pillow 12.3, ffmpeg, and the
  rpi-rgb-led-matrix wheel. Reach it as `protest.local`; never write an IP
  address into the repo.
- **Running the app without hardware:**
  `MATRIX_PLAYER_MATRIX_MODE=mock MATRIX_PLAYER_DATA_DIR=<dir> uvicorn matrix_player.app:app`
  with `PYTHONPATH=src`.
- **Tests:** `pip install -e '.[test]'` then `python -m pytest`. This includes
  the headless-Chrome UI check when Chrome is installed.

## Rules for changes

- **Edits by script must check their match.** A `str.replace` that matches
  nothing changes nothing, and once that shipped a 500 error to the phone.
  Assert the count, or use the Edit tool.
- **Never block the sign.** Slow work (transcoding, previews, backups, sensor
  reads) runs off the render thread and request path. One failing item is
  skipped, never fatal.
- **Spare the SD card.** Aggregate in memory and write every few minutes, as
  the health monitor and usage counters do.
- **Logs** are JSON events with IDs. Never log credentials, request bodies,
  message text, or original filenames. Routine polling stays out of the access
  log.
- **Database changes are additive** (`CREATE TABLE IF NOT EXISTS`, entries in
  `COLUMN_MIGRATIONS`), so older releases still run against a newer database.
- **Previews must match the panel:** render with `matrix_player.rendering`.
- **Every new setting read from the environment** goes in `.env.example`. A
  test enforces this.

## Panel hardware facts (not obvious from the code)

- **Pins:** three parallel chains on the "regular" mapping use GPIO 2–27. Only
  GPIO 0/1 are free, as I2C bus `i2c0`, the planned path for sensors.
- **Brightness and the CIE curve are applied per canvas when a frame is
  drawn.** Frames swap on vsync between two canvases, so a held still image
  must be redrawn for a brightness change to show.
- **Outdoor 1/8-scan panels smear at full brightness.** Lowering brightness or
  raising gamma cures it.
- **Diagnosing artifacts:**
  - sparkles or tearing at panel edges: power (capacitors, lead length);
  - ghost copies at panel joins: `MATRIX_PWM_LSB_NANOSECONDS`;
  - errors at row ends: `MATRIX_GPIO_SLOWDOWN`.
- **Power:** about 14–15 A on near-white on a 20 A supply. The panels are rated
  35 W each, 21 A for all three.
- **Diffuser:** the sign sits behind one, so 1 px detail blurs. Heavy fonts with
  an outline read best.

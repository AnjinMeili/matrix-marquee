---
name: ui-check
description: Check the sign's web UI in headless Chrome at iPhone size before calling UI work done. Use for any change to src/matrix_player/static/ (index.html, app.js, app.css), to API responses the page renders, or when the owner reports something odd on the phone. Runs tools/ui_check.py (every view and the main dialogs, failing on stray "null"/"undefined"/"NaN", horizontal overflow, script errors, browser dialogs), reads its screenshots, and explains how to script new interactive flows.
---

# Checking the web UI

The owner uses the sign from an iPhone, often inside the captive-portal window
that opens when joining the sign's Wi-Fi. Two faults reached the phone before
this check existed:

- **A stray "null" in two screens.** `replaceChildren(null)` prints the word
  "null".
- **Deletes that silently did nothing.** The portal window refuses
  `window.confirm()`.

Unit tests can't see either; a rendered page can.

## Run it

```sh
python tools/ui_check.py --out <scratch dir>/ui-check
```

It starts the app in mock mode on a free port with throwaway sample data
(pictures with tags, an animated GIF, messages, a playlist), opens every view
plus the playlist editor, message detail, message editor, and media dialog at
390×844, and prints `ok` or `FAIL` per screen. Exit codes: 0 pass, 1 problems,
2 cannot run (no Chrome or no `websocket-client`). `python -m pytest` runs it
too, as `test_every_view_renders_cleanly_at_phone_size`.

Signs come in many sizes. To check another shape, set the size first, e.g.
`MATRIX_PLAYER_MATRIX_WIDTH=192 MATRIX_PLAYER_MATRIX_HEIGHT=64 python
tools/ui_check.py --out <dir>`. Previews should take that shape and dot grid.

Then **look at the screenshots** with the Read tool. The check catches
text and overflow faults, not visual ones such as overlapping controls,
unreadable contrast, or a chart drawn too small. Known capture artifacts to
ignore:

- the "Skip to content" link, which shows when headless Chrome gives it focus;
- sticky bars (the selection bar) that land mid-page in a full-page capture.

## When adding UI

- **New screen or dialog:** add a step to `steps` in `tools/ui_check.py` so it
  is checked from then on.
- **One-off flows** (tap through a sequence, read a value): use its `Browser`
  class from a scratch script: `browser.evaluate(js)`,
  `browser.settle(seconds)`, `browser.screenshot(path)`. Script errors and
  dialogs are collected in `browser.errors`.
- **Prove a check works** by breaking the code on purpose, running it, and
  restoring the file with `git checkout -- <file>`.

## UI rules this project keeps

- No `window.confirm`, `alert`, or `prompt`; use `confirmAction()`. A test
  enforces this.
- Use `fill(node, ...)` rather than `replaceChildren(...)` whenever a child
  can be `null`.
- Write copy for non-technical operators ("Show now", "Turn off sign"), not
  developers.
- Downloads don't work in the portal window; tell users to open Safari.
- Previews should match the panel: draw with the sign's own renderer
  (`matrix_player.rendering`), never a browser approximation.

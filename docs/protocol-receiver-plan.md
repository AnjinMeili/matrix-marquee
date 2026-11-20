# Plan: receive Art-Net, sACN, and DDP with MatrixPrism's receiver (not built)

**Status:** investigated 2026-10-17; the owner chose option B (the bridge), and
that live input must never take over the sign on its own. A request to
display is approved by a person connected to the sign's page, through a
real-time messaging channel that also carries other notifications. Nothing
is built. The sign is a receiver only; sending to other panels was dropped,
see [wled-output-plan.md](wled-output-plan.md).

**Update 2026-10-19 (decided by the owner):** saved sender profiles become
the normal path. A known sender shows without a new request only if its
profile grants it that right, and only while receiving is on; any other
sender, known or not, still raises a display request. Streams show through
a layer mixer with blend modes and chromakey. This refines "no automatic
takeover" below: the takeover is a right a PIN holder granted earlier. See
[live-mixer-plan.md](live-mixer-plan.md).

## The request

Use MatrixPrism's Pi app, `receiver/matrix-dmx`, as a git submodule to give
the sign a ready-made receiver for the common pixel protocols. Then any
sender (MatrixPrism's composer, LedFx, xLights, Pixel Animator, lighting
desks) can drive the sign.

## What the receiver is

`matrix-dmx` is C++, GPL-3.0-or-later, about 3,900 lines with its own tests:

- **Protocols:** Art-Net (UDP 6454, with ArtPoll replies), sACN/E1.31 (UDP
  5568, multicast and unicast), and DDP (UDP 4048). For DDP it accepts
  standard data types `0x0b` and `0x01`, plus Pixel Animator 3's custom
  layout. Partial frames are flushed after 40 ms.
- **WLED compatibility:** it pretends to be a WLED node, so WLED apps such as
  LedFx can drive it. It answers WLED's JSON API on HTTP port 80 and runs its
  own mDNS responder on 5353 (`wled-<mac>.local`). `--no-wled` turns this
  off.
- **Recording and playback:** `.mrgb` recordings and `.scene` playlists, with
  `scene-tool` to edit them.
- **Panel output:** it drives rpi-rgb-led-matrix itself, taking the standard
  `--led-*` flags. That library is pinned as a nested submodule
  (`receiver/matrix`).

## Constraints found

| Constraint | Effect |
|---|---|
| **The panel's GPIO can be driven by only one process** | `matrix-dmx` and this project's player cannot both show on the panel |
| **Port 80 is the sign's web UI and captive portal** (uvicorn) | `matrix-dmx` cannot serve the WLED API. Without it, it logs a warning and runs Art-Net, sACN, and DDP only |
| **mDNS on 5353**: Avahi already announces the configured `<hostname>.local` | Both set `SO_REUSEADDR`, so they could share the port, but two responders on one host is fragile. Better to let Avahi do it all |
| **Source release** | If MatrixMarquee is published, the receiver source and licence must also be available |
| **Repository size:** MatrixPrism is small (a 2.4 MB pack), but the submodule brings the composer as well | The receiver "stands alone" per its README; a repository of its own would be the cleaner submodule |
| **The nested rpi-rgb-led-matrix submodule** | Not needed: build against `/opt/rpi-rgb-led-matrix`, which the sign already has, with `make RGB_INCDIR=… RGB_LIBDIR=…` |
| **The UDP ports are free on the sign, and `net.core.rmem_max` is already 4 MB** | No conflicts; the sign meets the receiver's buffer advice |
| **Licence:** GPL-3.0-or-later | Running it as a separate program is fine whatever this project's licence. Shipping builds means offering the source, which a submodule does |

## Two ways to integrate

### A. Switch modes: works now, with no change to MatrixPrism

- `matrix-dmx` runs as its own service, `matrix-marquee-receiver.service`, with the
  sign's matrix flags from `protest.env` plus `--no-wled`.
- A "Live input" switch in Settings stops this project's player, releases the
  panel, and starts the receiver. Switching back reverses it. Releasing the
  GPIO inside a running Python process is untested, so in practice this means
  restarting the service in a "receiver mode" that skips matrix setup: about
  3 s, with the UI staying usable for editing.
- **What you lose while live:**
  - brightness and gamma only as start-up flags, not the live sliders;
  - no live mirror and no usage counts;
  - no automatic return to the playlist when the stream stops;
  - WLED apps can't discover the sign.

### B. Bridge into the player (chosen)

A small change to the receiver adds a headless output: no GPIO, with each
assembled frame written to a local socket or FIFO, framed like its own
`.mrgb` records (timestamp plus RGB). This project then:

- **adds a "Live input" frame source.** The player stays the only owner of
  the panel, and the receiver does all the protocol parsing (universes, Art-Net
  polls, Pixel Animator quirks).
- **keeps every existing feature for live content:** brightness and gamma,
  the live mirror, heat and power, usage counts, and the brightness limiter
  if it's built.
- **never lets a stream take over by itself.** A new stream becomes a
  display request that a connected person approves or denies (see below).
  After an approved stream stops, the playlist resumes where it left off.
- **serves the WLED JSON API on its own port 80,** by porting the receiver's
  small JSON builders, and adds `_wled._tcp` to the Avahi service file. The
  receiver runs with `--no-wled`. WLED apps then find the sign as
  the configured `<hostname>.local`, and there's one mDNS responder.

**Changes in MatrixPrism (maintained separately):**

- an output interface in `matrix-dmx.cc`, which creates the matrix
  unconditionally today;
- `--output=<socket path>`;
- a build target that doesn't link rgbmatrix;
- **the source of each frame:** protocol, sender address, and port, sent as
  a small header whenever the source changes, so this project can tell
  senders apart and gate each one;
- **`--no-artpoll`,** so Art-Net poll replies (Art-Net's own discovery) can
  be switched off separately from receiving, for the "Discoverable" switch;
- tests for the frame output.

**Effort:** about half a day in MatrixPrism, and half to one day here.

## Off and invisible by default

**The sign is invisible as a receiver unless an admin turns it on, and it is
always off again after power-up.** There are two switches, both held only in
memory, never saved. Any restart (power-up, reboot, deploy, or service
restart) turns both off:

| Switch | Off (always, after start-up) | On (turned on by an admin) |
|---|---|---|
| **Receive live input** | The receiver is not running: no UDP ports open, nothing to find or send to | `matrix-dmx` starts and listens. Streams still need an approved display request |
| **Discoverable** (needs Receive) | No `_wled._tcp` mDNS announcement, no WLED JSON API, no Art-Net poll replies; only a sender that already knows the address can reach it | The sign announces itself to apps (LedFx, Art-Net controllers) |

- Both switches live on a Settings card ("Live input"). Turning either on,
  like approving a request, needs the operator PIN.
- Who turned each switch on or off, and when, is logged.
- While Receive is on, the Sign page shows it plainly.
- **Idle timeout:** Receive turns itself off after **5 minutes** with no
  approved stream showing. It takes Discoverable off with it, so a sign left
  receiving drops back to invisible. Each approved stream restarts the
  5 minutes when it ends.
- The receiver runs as its own service, `matrix-marquee-receiver.service`, started
  and stopped by the app and never enabled at boot. A forgotten switch can't
  survive a power cycle.

## Display requests and notifications

No stream reaches the panel without a person's say-so.

1. **A new stream arrives.** The bridge reports frames from a sender that is
   not approved. The player keeps showing the playlist and drops those
   frames.
2. **The sign raises a display request:**
   - the sender's address and protocol, its size and frame rate;
   - a **thumbnail of its first frame**, so people see what it wants to show.
3. **Everyone with the sign's page open is notified at once,** on the Sign
   page and as a banner on any other page:

   > Live input from 192.168.1.50 (DDP, 96×64, 30 fps) wants the sign.
   > [Allow for… 10 min ▾] [Allow until done] [Deny and ignore for… 10 min ▾]

   The approver chooses one of three answers:

   - **Allow for a time window** they pick from the declared list (by
     default 5, 10, or 30 minutes, with 10 preselected). When the window ends, the sign goes back to work: the
     playlist resumes where it left off. If the sender is still streaming,
     that counts as a new request.
   - **Allow until done:** the stream shows until it stops, meaning no frames
     for a few seconds. Then the playlist resumes, and a later stream is a
     new request. **Stop** on the live banner ends it sooner.
   - **Deny and ignore for a window** they pick from the declared list (by
     default 10 minutes, 1 hour, or until the next restart, with 10 minutes
     preselected). Requests from that sender
     are dropped silently until then.

4. **The first answer decides,** and everyone else sees who decided. Each
   approval, denial, and expiry is logged as an audit event.
5. **While live:** a banner on every page reads "Live input from … — Stop".
   Brightness, gamma, the live mirror, and heat and power all still apply.
   When the stream stops for a few seconds, or the approval runs out, the
   playlist resumes where it left off.
6. **If nobody is connected, nothing is shown.** The request waits
   (default 5 minutes) and shows on the Sign page for the next person who
   opens it, then expires.
7. **Spam control:** one pending request per sender. A denied sender is
   ignored for the window the approver chose.
8. **Trusted senders (optional):** "Always allow this sender" remembers an
   address, revocable in Settings. Off by default.

### Declared windows

The windows aren't built into the code. They are declared settings on the
Live input card in Settings, changed behind the operator PIN and stored in
the sign's `settings` table. Unlike the Receive and Discoverable switches,
they **persist across restarts**: they are preferences, not permissions, so
keeping them doesn't weaken the off-at-start-up rule.

| Setting | Default |
|---|---|
| Allow windows offered | 5, 10, 30 minutes |
| Preselected allow window | 10 minutes |
| Offer "Allow until done" | yes (turn off to make every approval time-limited) |
| Ignore windows offered | 10 minutes, 1 hour, until restart |
| Preselected ignore window | 10 minutes |
| Request wait when nobody is connected | 5 minutes |
| Receiver idle timeout | 5 minutes |

The rules for the values:

- each window is 1 minute to 24 hours;
- at least one allow window is offered;
- a preselected value must be one of those offered.

The request banner is built from whatever is declared.

### The messaging channel

- **A WebSocket at `/api/events`.** It is two-way and works in Safari and
  the captive-portal window. The page reconnects automatically and catches
  up from the current state on reconnect.
- **Messages are small JSON events:**
  - from the sign: `display_request`, `display_decided`, `live_started`,
    `live_ended`, `health`, `playback_warning`, `activity` (for example,
    "POW! was sent to the sign"), and `presence` (how many people are
    connected);
  - from a person: `allow`, `deny`, `stop_live`.
- **Presence:** each open page is a session with a short device label ("an
  iPhone") that the user can rename, so a decision can be credited.
- **No push notifications.** Web Push needs the internet and HTTPS, which
  an offline sign doesn't have, so only open pages are notified. The
  "nobody connected" rule above covers the rest.
- **Built first,** because nothing else needs it: health warnings,
  skipped-item warnings, and "someone else changed the playlist" can use it
  before live input exists. The Sign page's 3-second polling could then be
  dropped.

### Who may approve

Today anyone on the Wi-Fi can open the page, so anyone could approve. Live
input should therefore come with the **PIN** already on the list: approving
a display request, like any other change, requires a phone that has entered
the operator PIN. Viewing and being notified stay open.

## The receiver's own repository: matrix-jack

Decided 2026-10-17: the receiver moves out of MatrixPrism into a repository
of its own, **matrix-jack**. Its programs keep
their names, `matrix-dmx` and `scene-tool`, unless renamed separately. It is one of three applications for managing matrix displays
with varied information:

| Application | Role |
|---|---|
| **MatrixMarquee** (this repository) | the sign's player and manager: messages, playlists, media, the phone UI, health |
| **MatrixPrism** | music-driven visuals, composed on a laptop and streamed out |
| **matrix-jack**, the receiver (`matrix-dmx` and `scene-tool`) | Art-Net, sACN, and DDP in, frames out to a panel or (with the bridge) to an application |

Both MatrixMarquee and MatrixPrism then use the receiver as a submodule.

**Splitting it out of MatrixPrism:**

1. `git subtree split --prefix=receiver` keeps the receiver's history on a
   branch of its own.
2. Push that branch as the new repository's `main`, taking its nested
   rpi-rgb-led-matrix submodule and its GPL-3.0-or-later licence with it.
3. Replace `receiver/` in MatrixPrism with a submodule of the new repository,
   and update MatrixPrism's README, `AGENTS.md`, and `docs/STATUS.md`.
4. The bridge output and `--no-artpoll` changes are then made in the new
   repository, with its own tests (`make test` runs without hardware).

**Visibility:** the new repository can be private now and made public when
MatrixMarquee is released, independently of MatrixPrism.

### Build and promote, following MatrixMarquee

The new repository gets the same release process as MatrixMarquee, so all three
applications deploy, verify, and roll back the same way. MatrixMarquee's pieces and
their receiver counterparts:

| Stage | MatrixMarquee today | Receiver repository |
|---|---|---|
| **Test** | `python -m pytest` (with the UI check) | `make test`: protocol, mapping, recording, scene tests, no hardware |
| **Ship** | `scripts/deploy.sh --host …`: rsync over SSH (batch mode, strict host keys), never secrets or data | `scripts/deploy.sh`, the same options and safeguards, `MATRIX_JACK_DEPLOY_HOST` |
| **Build** | `scripts/install.sh` builds a venv in a timestamped release; the rgbmatrix wheel is cached | `scripts/install.sh` runs `make` in `/opt/matrix-jack/releases/<UTC stamp>-<commit>`, against the matrix library already on the Pi, so a rebuild only happens when the commit changes |
| **Promote** | atomically repoint `/opt/protest/current`, restart, keep the newest 5 releases | the same: repoint `/opt/matrix-jack/current`, restart only if the service is running, keep 5 |
| **Verify** | `scripts/verify-deploy.sh` (read-only, non-zero on failure) | `scripts/verify-deploy.sh`: the binary reports the expected commit (`matrix-dmx --version`, to be added). When running standalone, send a test DDP frame and confirm it was accepted (`--debug` counters) |
| **Roll back** | repoint `current` to an earlier release, restart | the same |
| **Run** | `protest.service`, a sandboxed systemd unit, settings in `/etc/protest/protest.env` | `matrix-jack.service`, with panel flags and universes in `/etc/matrix-jack/matrix-jack.env`, never overwritten by a deploy |
| **Operate** | health timer, persistent journald, log bundle, configuration backup | the same pieces, scaled down: journald retention and a log bundle |
| **Agents** | `AGENTS.md` and `CLAUDE.md`; skills `deploy-sign` and `ui-check` | `AGENTS.md` and `CLAUDE.md`; a `deploy-matrix-jack` skill with the same explicit-"deploy" rule |
| **Release** | commits on `main`, pushed on request | the same, plus **tags** (`v0.x.y`). MatrixMarquee and MatrixPrism pin a tagged release as their submodule, and move to a newer tag deliberately |

**Two ways it is installed:**

1. **Standalone,** on a dedicated receiver display:
   `matrix-jack.service` is enabled at boot and drives the panel.
2. **Inside MatrixMarquee,** as the bridge:
   - MatrixMarquee's `install.sh` calls the receiver's own build script on the
     submodule commit and caches the binary under `/opt/protest/bin/`.
   - MatrixMarquee starts the on-demand `matrix-marquee-receiver.service` only when an
     admin turns receiving on, never at boot, as the rules above require.
   - One build script serves both, so the two installs can't diverge.

**Shared conventions across the three applications:**

- releases under `/opt/<app>/releases/`, with a `current` link;
- the same `deploy.sh` options (`--host`, `--user`, `--identity`,
  `--skip-os-packages`);
- a read-only `verify-deploy.sh` that exits non-zero on failure;
- `<app>.local` names over mDNS, where an app has a UI;
- the same approval rules for agents.

If the three keep drifting apart, these scripts could later become one
shared deploy kit; until then, copying MatrixMarquee's scripts and adapting them is
simpler.

## Submodule mechanics (either way)

- **Location:** `vendor/matrix-jack`, pinned to a commit and
  updated deliberately with `git submodule update --remote`.
- **Deploy:** `scripts/deploy.sh` copies the working tree, so it must refuse
  to run if the submodule isn't checked out (`git submodule status` showing
  `-`).
- **Build:** `install.sh` builds the receiver on the Pi only when the
  submodule commit changes, and caches the binary as
  `/opt/protest/bin/matrix-dmx-<commit>`, much as the rgbmatrix wheel is
  cached. The build tools are already on the sign.
- **Tests:** `make -C vendor/…/receiver test` runs without hardware and can
  join this project's checks.

## Security

- **Approval:** live input never shows without a person's approval (above).
- **Off and invisible after every start-up:** the receiver isn't running, so
  no ports are open. Turning it on, and making it discoverable, are
  deliberate admin choices that are never saved across a restart.
- **The PIN** for operators who may approve.
- **Optional:** "from these addresses only".
- **The receiver's own WLED interface is off** (`--no-wled`); WLED discovery,
  if wanted, is served by this project instead.

## Decided (2026-10-17)

- **Option B, the bridge.**
- **No automatic takeover.** Every stream is a display request that a
  connected person approves, over a messaging channel that notifies
  everyone with the page open.
- **Opt-in discoverability, off at every power-up.** The sign is invisible as
  a receiver by default. An admin may turn on receiving, and then
  discoverability, but both reset to off on any restart.
- **A 5-minute idle timeout:** receiving, and with it discoverability, turns
  off after 5 minutes without an approved stream.
- **Approval answers:** allow for a chosen window and then back to the
  playlist; allow until done; or deny and ignore the sender for a chosen
  window.
- **The windows are declared settings**, not fixed in code: persisted, PIN
  protected, and with defaults as in "Declared windows" above.
- **The receiver gets its own repository, matrix-jack,** shared by MatrixMarquee
  and MatrixPrism as one of three applications for matrix displays.
- **Granted senders (2026-10-19):** a sender whose saved profile grants
  "Show without asking" shows without a new request while receiving is on;
  every other sender still needs an approved request. See
  [live-mixer-plan.md](live-mixer-plan.md).

## Phases

0. **Split the receiver into its own repository** (in MatrixPrism; see
   above), with MatrixMarquee's build and promote process: tests, deploy,
   build, promote, verify, rollback, a systemd unit, `AGENTS.md`, a
   `deploy-matrix-jack` skill, and tagged releases. Not needed for phases 1–2.
1. **The messaging channel** (`/api/events`) with presence and
   notifications for things that exist today: health, skipped items, and
   activity. Useful on its own; no MatrixPrism changes. **Built 2026-10-19**
   (see "As built: the messaging channel").
2. **The operator PIN,** so approvals and changes need a trusted phone.
3. **The MatrixPrism bridge output,** with frame source metadata and
   `--no-artpoll`, and the submodule with its cached build.
4. **The live input source:** the two runtime switches (off at start-up),
   the 5-minute idle timeout, `matrix-marquee-receiver.service` started on demand,
   and display requests with approval, expiry, the live banner, and resuming
   the playlist.
5. **Discoverable:** the WLED JSON API and `_wled._tcp`, served by this
   project only while the switch is on.
6. **Optional:** trusted senders and an address allow-list.

## As built: the messaging channel (2026-10-19)

- **Where:** `events.py` (`EventHub`, the `ChangeAnnouncer` middleware, and a
  log filter) and the WebSocket route `/api/events`.
- **Events from the sign:** `welcome` (the page's session id and starting
  name), `presence` (everyone connected, by name), `activity` (an area
  changed: playlists, messages, library, sign, or display, and which session
  did it), `health` (a heat or power problem starting or clearing), and
  `playback_warning` (an item skipped). Each has a sequence number and time.
- **Activity needs no per-route code:** a pure ASGI middleware announces any
  successful POST, PUT, PATCH, or DELETE under those areas, leaving out
  previews. Pages send their session id as `X-Session`, so other phones can
  say who made a change and a page skips its own. The header is a courtesy,
  not proof; the PIN is what will make approvals trustworthy.
- **From a page:** `hello` with a saved name. A name is printable, one line,
  and at most 30 characters; the starting name comes from the browser ("an
  iPhone", "a Mac"). The name is kept on the phone (`localStorage`) and set
  in Settings under "This phone".
- **On the page:** a "Also connected: …" line on the Sign page, notices for
  other phones' changes, health problems, and skipped items, and a reload
  shortly after someone else's change (never under an open dialog). The page
  reconnects with back-off up to 30 s and reloads once to catch up.
- **Limits:** 32 open pages (more are turned away), 100 queued events per
  page (oldest dropped), messages from a page up to 1,024 characters.
  Publishing never blocks: the render thread and health monitor hand events
  to the web server's loop.
- **Logs:** connects and disconnects are debug-level, and uvicorn's
  WebSocket handshake and "connection open/closed" lines for `/api/events`
  are filtered out. Names are never logged.
- **Still polling:** the Sign page keeps its 3-second status poll, for the
  progress line and as a fallback; dropping it can follow once the channel
  has run on the sign for a while.
- **Not yet:** `display_request`, `display_decided`, `live_started`,
  `live_ended`, and the `allow`, `deny`, and `stop_live` answers, which come
  with live input (phase 4).

## Still open

- Nothing blocking. The defaults above can be changed on the sign once built.

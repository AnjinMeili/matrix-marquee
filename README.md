# Protest RGB Matrix Appliance

DietPi appliance software for a Raspberry Pi 4 driving an RGB LED matrix of
any size as a protest sign (the first build is 96x64). Everything is controlled from a phone over the sign's own
Wi-Fi; no internet connection is needed.

## What it does

- **Messages** — big, bold text that sizes itself to fill the sign. Bundled
  display fonts, outline, bold and italic, colour styles (solid, rainbow,
  plasma, changing colours, fire), motion (scroll either way, pulse, shake,
  flash, comic POW, punch-in) with a speed setting, animated borders, and text
  over a background picture. One-tap looks (Protest, Comic POW, Alarm, …).
- **Pictures and videos** — JPEG, PNG, WebP, GIF, MP4, WebM, and iPhone MOV.
  Items can be renamed and tagged; tags filter and search the library. Select
  a batch to tag it or add it to a playlist in one step. Videos and animated
  GIFs play, with controls, in their edit pane.
- **Playlists** — per-item time or "play the animation N times", entrance
  effects (comic slam, morph, camera flash, push, fade, zoom, …), and play
  modes (in order, shuffle, random, stop at the end). Playlists pick up where
  they left off after a reboot, a stop, or showing something on its own.
- **Previews** — every editor previews with the sign's own renderer before
  saving, and the Sign page mirrors the panel live.
- **Brightness and gamma** — live controls, kept across restarts, and a
  calibration test card for outdoor panels that wash out or smear.
- **Heat and power** — the Pi's temperature, CPU speed, and under-voltage and
  slow-down flags, with warnings on the Sign page and a 24-hour record.
- **Backups** — one tap saves every message, playlist, picture, and video to
  the phone or laptop as a zip.
- **Keeps running** — an item that cannot play is skipped and reported on the
  Sign page; the rest of the playlist carries on.

## Using it from a phone

Join the sign's Wi-Fi; the sign-in window opens the control page. From any
network the sign is on, the page is at `http://protest.local`.

The iPhone sign-in window is limited: it cannot save downloads. Open the page
in Safari to download a backup or log bundle. After a software update, reload
the page.

## Install and deploy

The appliance runs as root because the matrix driver needs direct hardware
access. The systemd unit still limits filesystem writes to `/var/lib/protest`
and `/var/log/protest`.

From an operator machine (non-interactive, auditable):

```sh
scripts/deploy.sh --host protest.local --user root
```

The host defaults to `protest.local` and is configurable by `--host` or
`PROTEST_DEPLOY_HOST`. SSH uses batch mode and strict host-key checking, so
connect once with `ssh root@protest.local` to accept the host key. Deployment never transfers `.env` files,
runtime data, media, or credentials. Each deploy creates a timestamped release
under `/opt/protest/releases`, atomically repoints `/opt/protest/current`, and
keeps the newest five releases (`KEEP_RELEASES`).

The first install needs internet access on the Pi: it installs `ffmpeg` and
`fonts-dejavu-core` with apt and the Python dependencies (including NumPy)
with pip. Later deploys can add `--skip-os-packages`.

The matrix bindings are built once on the Pi and cached as a wheel that every
release reuses:

```sh
sudo scripts/build-rgbmatrix.sh
```

Rebuild it after a Pillow upgrade: the bindings compile against Pillow's
headers. Without the wheel the service runs with the matrix reported as
unavailable while the web UI stays usable.

Common operations:

```sh
systemctl status protest
journalctl -u protest -f
scripts/health-check.sh
systemctl restart protest
```

### The protest.local name

The installer sets up `avahi-daemon`, which announces the sign as
`<hostname>.local` on every network it joins, along with its web UI. With the
hostname `protest` that is `protest.local`; on the sign's own Wi-Fi the AP's
DNS answers the same name. If two signs share a network, the second becomes
`protest-2.local`; give each its own hostname to tell them apart.

## Configuration

Copy `.env.example` to `/etc/protest/protest.env` (mode `0600`); deploys never
overwrite it. Notable settings:

- `MATRIX_PLAYER_MATRIX_MODE` — `hardware` drives the panel, `mock` runs
  without GPIO for UI and API development.
- `MATRIX_PLAYER_AUTOPLAY_DEFAULT=true` — play the enabled default playlist at
  boot. Leave it off while provisioning if the panel should stay blank.
- `MATRIX_PLAYER_MAX_UPLOAD_BYTES` — upload size limit (default 250 MiB).
  Uploads are also refused while free storage is below 200 MiB, checked
  before and during each upload.
- `MATRIX_PLAYER_AP_SSID`, `MATRIX_PLAYER_AP_ACTIVE`,
  `MATRIX_PLAYER_UPSTREAM_INTERFACE` — shown in the UI only.

The sign can be any size: set `MATRIX_PLAYER_MATRIX_WIDTH` and
`MATRIX_PLAYER_MATRIX_HEIGHT` to the viewing area and the `MATRIX_*` driver
settings to the panels. Text, borders, effects, transitions, and the web
previews all scale to it. The first build's viewing area is 96x64. Its driver
uses the supplied initialization string, whose raw framebuffer is 64x96 and is
rotated by the configured mapper:

```text
--led-rows=32 --led-cols=64 --led-chain=1 --led-parallel=3
--led-multiplexing=1 --led-pixel-mapper=Rotate:90
--led-pwm-lsb-nanoseconds=160 --led-slowdown-gpio=2
```

### Display tuning

Reserve CPU core 3 for the panel refresh thread to reduce flicker: append
`isolcpus=3` to the single line in `/boot/firmware/cmdline.txt` and reboot.
The matrix library logs a reminder at startup until this is set. Frames are
drawn off-screen and swapped on vsync, so motion does not tear.

Outdoor high-brightness 1/8-scan panels hold each pixel's light longer, so at
full brightness they can wash out and smear moving pictures. Use **Settings →
Brightness & gamma**: show the test card, then lower brightness or raise gamma
until its dark steps stay separate and its edges stop trailing. Gamma 1.0 and
brightness 100% are the panel's untouched look. Static-like sparkles or tearing
at panel edges are a power problem instead: fit low-ESR capacitors at each
panel's power input, as the matrix library's wiring guide recommends.

## Media handling

- Pictures are checked with Pillow; a GIF with a single frame is stored and
  played as a still picture. A one-off startup check applies this to media
  uploaded before the check existed (marker:
  `/var/lib/protest/.gif-animation-check-v1`).
- Videos are checked with ffprobe and get a thumbnail. Phone video is too
  heavy to decode live on a Pi, so each video gets a small upright H.264
  playback copy (`<name>.play.mp4`, up to 320 px and 30 fps), built in the
  background at low priority. Until it exists the original plays, as fast as
  the Pi can decode it.
- Animated GIFs play frame by frame from the original on the sign. For the
  phone's player, a GIF gets an MP4 copy the first time it is opened.
- **Picture fit** is chosen per playlist item: fill the sign (trim edges), the
  whole picture, stretch to fill, or "Stretch the edges to fill the bars"
  (Fibonastretch), or "Repeat the picture to fill the bars" (tile: the bars
  show exact copies of the picture's other side, like a window cut from the
  middle of three copies in a row; no stretching or blur). Fibonastretch fills the empty bars by stretching the picture's
  edge pixels outward in Fibonacci steps (1, 1, 2, 3, 5, ...), starting at the
  picture's own edge and widest at the sign's edge, so there is no seam. Each pixel grows in both directions as it steps outward, and the overlapping neighbours are combined, so the bars blur more toward the sign's edge. The blur can be Crisp (none: pixels are only stretched, so each keeps its clarity), Blur (average, the default), Glow (lightest wins), Bright (screen), Dense (additive), or Dark (multiply); Bright and Dense wash the bars out to white. It lights
  the bars, so bright pictures draw more power than with black bars. Video
  uses ffmpeg only to scale and draws the strips itself; if the video's size
  cannot be read it plays as "whole picture". See `docs/fibonastretch-plan.md`.
- **Fitted frames are kept on disk.** The first time an animation or video plays
  with the Fibonastretch fit, it plays live while a background worker writes the
  fitted frames, at low priority, to `/var/lib/protest/fitted` as raw sign-size
  frames. Every later play reads that file back with no fitting work. A clip
  whose file would take more than a quarter of the limit
  (`MATRIX_PLAYER_FIT_CACHE_MAX_BYTES`, 1 GiB) always plays live. The files are
  derived, not backed up, and can be deleted at any time; the oldest are pruned
  past the limit, and deleting the media deletes its files. A 96×64 frame is
  18 KB, so 10 s at 30 fps is about 5 MB. Each combination of media, sign size,
  fit, and blur is written once, so trying many blurs on one clip writes one
  file each.
- Deleting media that a playlist or message background uses is refused.

### Usage counters

The sign keeps daily totals of how each message and picture or video is
used: plays, seconds on the sign, "Show now" sends, edits, and removals from
a playlist soon after adding. They are the basis for ratings inferred from
use (see `docs/companion-designer-plan.md`), are readable at `/api/usage`,
and are included in full backups. They are gathered in memory and written
every few minutes, and record nothing about viewers.

## Wi-Fi AP and captive portal

AP setup is intentionally **not** part of installation. Changing the wrong
interface can cut off SSH. First install the required DietPi packages:

```sh
apt-get install hostapd dnsmasq
install -m 0600 deploy/ap/ap.env.example /etc/protest/ap.env
editor /etc/protest/ap.env
```

Use a dedicated Wi-Fi interface that does not carry the default route. The
default AP network is `10.42.0.0/24`; verify it does not overlap another local
network. Only after local/console recovery is available, apply explicitly:

```sh
scripts/setup-ap.sh --apply --acknowledge-network-change
```

The script validates inputs and configuration, refuses an interface with an
IPv4 address/default route or an existing AP-subnet route, backs up replaced
files, and only then installs a narrowly scoped interface unit and enables
`hostapd` and `dnsmasq`. It does not replace DietPi's network manager.
Wildcard DNS points clients to the local portal, and the application answers
the captive-portal checks of iOS, Android, and Windows. NAT/upstream sharing
is deliberately not enabled; add it only after an operator has identified and
secured an upstream interface. Note that a phone joined to the sign's Wi-Fi
cannot also share its own connection over that link; upstream would need USB
tethering or a second Wi-Fi adapter joining the phone's hotspot.

There is no login: anyone who knows the Wi-Fi passphrase, or can reach the
wired address, can control the sign. Choose the passphrase accordingly.

To recover, connect locally; disable `hostapd`, `dnsmasq`, and
`protest-ap-interface`; remove their three generated configuration/unit files;
restore files from the reported `/var/backups/protest/network-*` directory;
then reload systemd and restart the prior network manager.

## Backups, logs, and incident data

There are three different archives:

| Archive | Contains | Made by |
|---|---|---|
| Full backup | database, every original picture and video, thumbnails, manifest | **Settings → Back up everything** (saved on the requesting device) |
| Configuration backup | systemd, journald, logrotate, and deploy files only | `sudo scripts/backup.sh` |
| Log bundle | redacted service logs | **Settings → Download log bundle**, or `scripts/log-bundle.sh` |

The full backup is built on the data disk and deleted once sent. It never
includes configuration, Wi-Fi passwords, or the rebuildable playback copies.
To restore it, stop the service, copy `database/` and `media/` (and optionally
`thumbnails/`) from the zip over the matching paths in `/var/lib/protest`, and
start the service; playback copies are rebuilt automatically.

```sh
sudo scripts/backup.sh
sudo LOG_SINCE='2 hours ago' scripts/log-bundle.sh
```

Script outputs are mode `0600` under `/var/backups/protest`. Review a log
bundle before sharing it.

Journald is persistent, compressed, capped at 256 MiB, and retained up to 30
days. Application logs are single-line JSON with event names and IDs; they
must never include credentials, request bodies, message text, or original
filenames. The phone UI's routine polling (live mirror, status, health) is
left out of the access log so it cannot flood the journal; failed polls are
still logged. The Settings log download leaves out DNS query logs because
they show what visitors browse; `scripts/log-bundle.sh` includes them for
network troubleshooting, which is one more reason to review it before sharing.

## Updating and rollback

Re-run `deploy.sh` for an atomic release update. Database changes are
additive (new tables and columns), so it is worth copying
`/var/lib/protest/matrix-player.sqlite3` to `/var/backups/protest/` before an
update, but an older release still runs against a newer database. To roll
back:

```sh
ln -sfn /opt/protest/releases/<previous-timestamp> /opt/protest/current
systemctl restart protest
```

After an update, check `systemctl status protest`, run
`scripts/health-check.sh`, and confirm the matrix blanks safely when playback
stops.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
MATRIX_PLAYER_MATRIX_MODE=mock MATRIX_PLAYER_DATA_DIR=./data \
    .venv/bin/uvicorn matrix_player.app:app --port 8000
.venv/bin/python -m pytest
```

Mock mode needs no GPIO. Tests that need `ffmpeg` are skipped when it is not
installed. The suite includes a headless-Chrome check of every view at phone
size (`tools/ui_check.py`, skipped without Chrome). After a deploy,
`scripts/verify-deploy.sh` checks the sign itself, read-only. Agent
instructions are in `AGENTS.md`, with project skills in `.claude/skills/`. The web UI is plain HTML, CSS, and JavaScript with no build step
and no external assets, so it works offline.

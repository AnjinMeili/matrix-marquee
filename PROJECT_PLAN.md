# Raspberry Pi RGB Matrix Media Player

## 1. Product definition

Build a Raspberry Pi 4 appliance that:

- Drives an RGB LED matrix through `rpi-rgb-led-matrix`.
- Creates its own Wi-Fi access point without requiring internet access.
- Serves a captive-portal web UI when a phone joins the access point.
- Plays images, animated GIFs, and video from playlists or a media folder.
- Supports animated transitions, repeats, display durations, and text panels.
- Allows media upload, playlist editing, text-panel editing, preview, and library management.
- Can use an iPhone-provided internet connection when one is available, but remains fully functional offline.

The primary operator is expected to use a phone on the local Wi-Fi network. The system should also work from a laptop on the same network.

### Confirmed deployment constraints

- Development/deployment target: Raspberry Pi at `protest.local` over SSH.
- Operating system: DietPi OS.
- Runtime privilege: the appliance currently assumes a root-only runtime; service permissions should be narrowed where practical without breaking matrix access or panel administration.
- The panel requires extended access, so the final design must document which privileged operations are needed and isolate them behind a small, auditable service boundary.
- The attached 96x64 viewing matrix has been validated with the supplied driver
  initialization string. The driver framebuffer is 64x96 because the
  configured `Rotate:90` mapper produces the 96x64 viewing orientation:

  ```text
  --led-rows=32 --led-cols=64 --led-chain=1 --led-parallel=3
  --led-multiplexing=1 --led-pixel-mapper="Rotate:90"
  --led-pwm-lsb-nanoseconds=160 --led-slowdown-gpio=2
  ```

- All Pi activity must be auditable. The application should use structured logs, systemd journal integration, upload/playback audit events, and network/service logs with rotation and retention settings. Logs must not include credentials or uploaded content.

## 2. Proposed MVP scope

### Included in MVP

1. **Appliance networking**
   - Raspberry Pi access point with a stable local hostname and IP.
   - Captive portal detection endpoints for common phone operating systems.
   - Local web server available without upstream internet.
   - Optional upstream routing/NAT when an iPhone connection is detected.
   - Status page showing Wi-Fi, storage, temperature, matrix state, and playback state.
   - Log AP association/disassociation, DHCP leases, captive-portal requests, upstream changes, and administrative actions.

2. **Media library**
   - Upload images, GIFs, and supported video formats.
   - List, search, filter, preview, rename, and delete media.
   - Store generated thumbnails and media metadata.
   - Validate file type, dimensions, duration, and available disk space.

3. **Playlists**
   - Create, rename, duplicate, reorder, and delete playlists.
   - Add media and text panels as playlist items.
   - Configure per-item duration or repeat count.
   - Configure transition type and transition duration.
   - Start, pause, resume, stop, and preview a playlist.
   - Run a selected playlist automatically on boot.

4. **Text panels**
   - Text content with line wrapping and alignment.
   - Font selection from an installed, curated font set.
   - Solid color, rainbow, and plasma color modes.
   - Bold and italic styles where supported by the selected font.
   - Border decoration selection and color.
   - Preview at the actual matrix aspect ratio.

5. **Matrix renderer**
   - Render static images, animated GIF frames, video frames, and text panels.
   - Normalize content to matrix dimensions using crop, fit, or letterbox modes.
   - Maintain a render loop with bounded memory and frame timing.
   - Provide a safe blank/black output when playback stops or an error occurs.
   - Use the confirmed matrix flags as the initial hardware profile, configurable without changing application code.

6. **Operational observability**
   - Emit JSON logs for startup, shutdown, playback transitions, upload lifecycle, validation failures, configuration changes, and hardware errors.
   - Send service output to `journald` and configure persistent storage, rotation, and retention on DietPi.
   - Provide a diagnostics view with recent event summaries and a downloadable log bundle that excludes secrets.
   - Record network state changes and administrative requests while avoiding request bodies and personal content.

### Deferred after MVP

- Multi-matrix chaining and virtual matrix layouts.
- Remote access outside the local network.
- User accounts and permissions.
- Audio playback.
- Timeline-based editing.
- Large transition marketplace or user-authored shader system.
- Cloud synchronization.

## 3. Recommended architecture

```text
                           Optional iPhone upstream
                                      |
                                      v
 [Phone/Laptop] <-- Wi-Fi --> [Raspberry Pi AP + DNS/DHCP]
                                      |
                                      v
                           [Captive portal web server]
                              |              |
                              v              v
                         [SQLite DB]   [Media library]
                              |              |
                              +------> [Playback service]
                                             |
                                             v
                                  [Frame/transition engine]
                                             |
                                             v
                                   [RGB LED matrix driver]
```

### Service boundaries

| Service | Responsibility | Suggested implementation |
|---|---|---|
| `web` | REST API, static UI, captive portal routes | Python FastAPI or Flask |
| `storage` | Media files, thumbnails, metadata, cleanup | Filesystem + SQLite |
| `playback` | Playlist state machine and scheduling | Separate Python process/module |
| `renderer` | Decode frames, scale, compose transitions | Pillow, imageio/ffmpeg, NumPy |
| `matrix` | Hardware output and safe shutdown | `rpi-rgb-led-matrix` adapter |
| `network` | AP, DHCP/DNS, captive portal integration | NetworkManager or hostapd/dnsmasq |

Keep the matrix adapter behind an interface so development can run with a mock renderer on a laptop or without hardware attached.

## 4. Data model

### `media`

- `id`
- `filename`
- `display_name`
- `media_type` (`image`, `gif`, `video`)
- `mime_type`
- `width`
- `height`
- `duration_ms`
- `frame_rate`
- `file_size_bytes`
- `thumbnail_path`
- `created_at`
- `updated_at`

### `playlists`

- `id`
- `name`
- `description`
- `is_default`
- `is_enabled`
- `created_at`
- `updated_at`

### `playlist_items`

- `id`
- `playlist_id`
- `position`
- `item_type` (`media`, `text_panel`)
- `media_id` or `text_panel_id`
- `duration_ms`
- `repeat_count`
- `transition_type`
- `transition_duration_ms`
- `fit_mode`

### `text_panels`

- `id`
- `name`
- `content`
- `font_family`
- `font_size`
- `bold`
- `italic`
- `alignment`
- `color_mode`
- `color_value`
- `border_style`
- `border_color`
- `updated_at`

## 5. API outline

This is the original plan. The running service documents the current API at `/api/docs`; it has grown to include sending items, live frames, previews, tags, backups, and log downloads.

### System

- `GET /api/status`
- `GET /api/network`
- `POST /api/playback/start`
- `POST /api/playback/pause`
- `POST /api/playback/resume`
- `POST /api/playback/stop`

### Media

- `GET /api/media?type=&query=`
- `POST /api/media` (multipart upload)
- `GET /api/media/{id}`
- `GET /api/media/{id}/thumbnail`
- `DELETE /api/media/{id}`

### Playlists

- `GET /api/playlists`
- `POST /api/playlists`
- `GET /api/playlists/{id}`
- `PUT /api/playlists/{id}`
- `DELETE /api/playlists/{id}`
- `POST /api/playlists/{id}/items`
- `PUT /api/playlists/{id}/items/{item_id}`
- `DELETE /api/playlists/{id}/items/{item_id}`
- `POST /api/playlists/{id}/preview`

### Text panels

- `GET /api/text-panels`
- `POST /api/text-panels`
- `GET /api/text-panels/{id}/preview`
- `PUT /api/text-panels/{id}`
- `DELETE /api/text-panels/{id}`

All mutating endpoints should return validation errors explicitly and should never silently discard a file or playlist change.

## 6. Playback and transition design

### Playback state machine

```text
STOPPED -> STARTING -> PLAYING -> PAUSED
                         |           |
                         +-> ERROR <-+
PLAYING -> TRANSITIONING -> PLAYING
```

The scheduler should resolve each playlist item into a frame source, render it at the matrix's configured dimensions, then apply the configured transition before showing the next item.

### Initial transition set

1. Cut
2. Fade in
3. Fade out
4. Slide from left
5. Slide from right
6. Slide from top
7. Slide from bottom
8. Rotate in
9. Zoom in
10. Pixel dissolve

Transitions should degrade to a cut if the source cannot provide frames quickly enough. The UI should show that fallback as a warning rather than failing the whole playlist.

**As built:** all ten are available, plus comic slam (`impact`), camera flash, and a pixel morph. "Fade in" is the cross-fade (`fade`) and "fade out" fades through black (`fade_out`). Late transition frames are dropped to keep time rather than falling back to a cut, and an item that fails is skipped with a warning on the Sign page.

### Media rules

- Decode off the hardware render thread.
- Use a bounded frame queue to avoid exhausting Pi memory.
- Preload only the next item when possible.
- Enforce a maximum upload size and supported codec list.
- Treat a single unreadable item as an item-level error; continue to the next item and record the error in status.

## 7. Captive portal and offline behavior

1. AP assigns clients an address through DHCP.
2. Local DNS resolves the configured hostname and portal address.
3. Common captive-check URLs return a redirect or portal response.
4. The portal includes a clear “Continue offline” path.
5. No feature depends on an external CDN, remote font, analytics service, or internet API.
6. If an upstream iPhone connection is detected, the Pi may route outbound traffic, but local playback and administration remain independent of it.

## 8. UI information architecture

### Main navigation

- **Dashboard**: current output, playback controls, system health.
- **Playlists**: playlist list and editor.
- **Library**: uploaded media and upload action.
- **Text panels**: reusable text panel editor.
- **Settings**: matrix, network, storage, startup playlist, diagnostics.

### Core flows

#### First boot

1. Join the Pi access point.
2. Captive portal opens.
3. User sees matrix dimensions and a short setup checklist.
4. User selects or creates a default playlist.
5. User starts playback or opens the library to upload content.

#### Create a playlist

1. Select **Playlists > New playlist**.
2. Add media or a text panel.
3. Reorder items by drag-and-drop or move buttons.
4. Set duration/repeats and transition per item.
5. Preview the result in the matrix-shaped preview.
6. Save and optionally set as startup playlist.

#### Upload media

1. Select **Library > Upload**.
2. Choose one or more local files.
3. Show validation/progress per file.
4. Generate thumbnails.
5. Offer “Add to playlist” after successful upload.

## 9. Visual direction

- Dark, high-contrast UI suitable for use near a bright LED display.
- Bright accent colors derived from RGB matrix colors.
- Large touch targets for phone use.
- Persistent playback status and a clear stop control.
- Matrix-shaped previews with a 1px pixel-grid texture.
- Avoid decorative gradients behind controls; reserve motion and color for previews.

The static browser mockup is in [mockup.html](./mockup.html). Open it directly in a browser; it uses no build step or external assets.

## 10. Delivery phases

### Phase 0 - Hardware and risk spike

- Confirm matrix dimensions, scan pattern, color order, GPIO configuration, and power requirements.
- Verify `rpi-rgb-led-matrix` can render a test pattern on the target Pi.
- Confirm Pi OS version and network-manager capabilities.
- Decide whether video decoding will use ffmpeg subprocesses or a Python decoder.

**Exit criteria:** test pattern runs for 30 minutes without crashes or visible corruption; AP is reachable from an iPhone.

### Phase 1 - Application skeleton

- Create service layout, configuration, logging, and health checks.
- Add mock matrix adapter and local development mode.
- Add SQLite migrations and media storage directories.
- Build the first dashboard shell.

**Exit criteria:** application starts on Pi and laptop; status endpoint and mock output work.

### Phase 2 - Library and text panels

- Implement upload validation, thumbnails, metadata, and deletion.
- Implement text-panel persistence and preview rendering.
- Add font installation/packaging rules.

**Exit criteria:** user can upload each supported media class and create a text panel offline.

### Phase 3 - Playback engine

- Implement item normalization and frame sources.
- Implement scheduler, repeats, durations, pause/resume/stop, and error recovery.
- Add initial transitions and matrix output adapter.

**Exit criteria:** a playlist runs unattended for at least 8 hours and recovers from a bad item.

### Phase 4 - Playlist editor and captive portal

- Implement full playlist editing and preview.
- Configure AP, DHCP/DNS, portal detection, and offline landing behavior.
- Add startup playlist and safe boot behavior.

**Exit criteria:** a new user can join the AP, upload content, build a playlist, and start playback using only a phone.

### Phase 5 - Hardening and release

- Add systemd services, watchdog behavior, log rotation, and backup/export.
- Test power loss during upload and playback.
- Test storage-full, malformed media, hot disconnect, and unavailable matrix cases.
- Document installation, recovery, and supported media formats.

**Exit criteria:** repeatable installation image/process and a documented recovery path.

## 11. Test strategy

### Automated

- API validation and permission-free local access.
- Playlist ordering, repeats, duration, and state transitions.
- Text layout, color modes, borders, and overflow handling.
- Transition frame correctness at small matrix sizes.
- Upload validation and cleanup on failed uploads.
- Mock matrix adapter output.

### Hardware-in-the-loop

- Static image, GIF, video, and text output.
- Brightness and color-order checks.
- Long-running playback.
- AP/captive portal on iPhone and laptop.
- Reboot, power interruption, and storage-full recovery.

### Acceptance checklist

- [ ] Works with no internet connection.
- [ ] iPhone can join the AP and reach the portal.
- [ ] Uploads do not block playback.
- [ ] A playlist can run indefinitely.
- [ ] Every requested transition is previewable.
- [ ] Text remains readable at the target matrix resolution.
- [ ] Stop always blanks the matrix safely.
- [ ] Errors are visible in the UI and logs.

## 12. Decisions

The questions this section originally listed have been settled as follows:

1. **Matrix:** the first build is a 96x64 viewing area from three parallel 64x32 panels, using the confirmed driver flags with the `Rotate:90` mapper. Sign size is configuration, not a constant: more signs of many sizes are planned, so everything drawn scales to the configured size.
2. **OS and networking:** DietPi (Debian 13). `hostapd` and `dnsmasq` serve the AP on `wlan0`, and DietPi's network manager stays in charge of `eth0`.
3. **Uploads:** 250 MiB limit. JPEG, PNG, WebP, GIF, MP4, WebM, and MOV are accepted. Videos are validated with ffprobe and played from a small H.264 copy made in the background.
4. **Time vs. repeats:** set per item. Still pictures and messages show for a time; animations either loop to fill a time or play N whole times. Scrolling text always gets at least one full pass.
5. **Preview:** both. Browser previews of messages, items, and playlist run-throughs are rendered by the sign's own engine, and the Sign page mirrors the live panel.
6. **Brightness:** not configurable yet (see below).
7. **Deleting referenced media:** blocked while a playlist item or message background uses it.

## 13. Related applications

This sign is one of three applications for managing matrix displays with
varied information:

- **Protest**, this repository: informative displays of words. Messages
  first, with pictures and video in support.
- **MatrixPrism:** generative streams of artistic expression matched to
  music, composed on a laptop and streamed as DDP.
- **matrix-jack**, the receiver (`matrix-dmx`): Art-Net, sACN, and DDP in.
  It is moving out of MatrixPrism into its own repository, to be shared by
  both as a submodule.

They meet at standard protocols (DDP, Art-Net, sACN) and mDNS discovery,
rather than shared code. See
[docs/protocol-receiver-plan.md](docs/protocol-receiver-plan.md).

## 14. Status and open items

Built beyond the MVP list: text effects and colour styles, bundled display fonts, play modes (loop, shuffle, random, once) with resume, media tags with batch selection and grouping, a whole-sign backup download, a live panel mirror, heat and power monitoring, brightness and gamma control with a test card, and the `protest.local` name over mDNS.

Not built yet:

- **A companion app that designs signs with an LLM** (Sign Studio): slogans and full message styles, checked with the sign's own renderer, guided by ratings of existing media and messages (given by users or inferred from use, such as play counts and how many playlists include an item), and delivered as importable design packs. See [docs/companion-designer-plan.md](docs/companion-designer-plan.md).
- **Text reveals and transitions:** letters that type on, rise, or drop in before a message settles; a neon power-on; feathered wipe, glitch, and neon transitions between items; eased slides; a globe scroll; and an optional dark backing plate. Eight were prototyped at 96×64 and all fit the Pi's frame budget. See [docs/text-transitions-plan.md](docs/text-transitions-plan.md).
- **Live data and charts on the sign** (eventually): fetch data from a chosen source, format it, and insert it into the display, as a message whose text fills from the data or as a chart drawn for the panel (a big number with a trend line, a few bars, progress toward a goal). No plan yet; it fits Protest's role as the informative sign, and its sources would need care because the sign is often offline.
- **Counting street sounds** with a USB microphone: car horns, shouting, crowds, sirens, counted on the device without keeping any audio. See [docs/sound-events-plan.md](docs/sound-events-plan.md).
- **Battery and power awareness.** The first sign runs from a battery pack carried on a wheelchair, so power is runtime and weight. The biggest draws are video and animations with white or yellow backgrounds: white lights all three LED colours and yellow two, frame after frame. Plan: estimate each item's average and peak draw from its frames when the background worker processes it (calibrated from the measured 14–15 A on near-white plus a black-screen reading), show heavy items in the library, estimate runtime from the pack's watt-hours, and offer an optional limiter that dims frames above a set draw, as WLED's brightness limiter does. Bright white is wanted for its impact, so the aim is to manage the draw, not remove the white: cap the peak (the fuse risk), and keep the playlist's average down (battery life) by spacing heavy items between light ones. The INA226/INA228 would replace the estimate with a measurement.
- **Sensors, through a Pi Pico 2 sensor agent** on USB, so no Pi pins are needed: a VEML7700 for ambient light (automatic brightness), an SHT45 for enclosure temperature and humidity, an INA228 with an external 50 A shunt on the panels' 5 V feed (sampled fast enough to catch peaks, with a hardware over-current alert), and an INA226 on the battery side. The two DIY packs' BMSes are read over isolated UARTs on the Pico 2; the EcoFlow Delta Pro 3 is read through EcoFlow's cloud API only when the sign has internet. Optional fan control and physical buttons. See [docs/sensor-agent-plan.md](docs/sensor-agent-plan.md).
- **Receiving Art-Net, sACN, and DDP** with MatrixPrism's `matrix-dmx` as a submodule, so any pixel sender (LedFx, xLights, MatrixPrism's composer) can drive the sign, but only after a connected person approves the display request over a new real-time notification channel. The sign is invisible as a receiver by default and after every power-up; an admin opts in to receiving and to being discoverable. See [docs/protocol-receiver-plan.md](docs/protocol-receiver-plan.md). Sending to WLED panels was dropped ([docs/wled-output-plan.md](docs/wled-output-plan.md)).
- **Upstream internet through a phone.** A phone joined to the sign's Wi-Fi cannot share its connection back over the same link. This needs USB tethering or a second Wi-Fi adapter joining the phone's hotspot, plus NAT.
- **Access control.** The Wi-Fi passphrase is the only protection. An operator PIN is planned, needed first for approving live input.
- **Automatic brightness.** Brightness and gamma are adjustable, but not yet automatic. The panels are outdoor 1/8-scan panels, chosen to be seen in daylight, so full brightness in sun is the normal daytime setting. Following an ambient light sensor is better than a time-of-day schedule, since it handles cloud, shade, and dusk, and it is the biggest power saver: dimming only when it is darker keeps full impact in daylight.
- **Restoring a backup from the web UI.** The README documents the manual steps.
- **Drag-and-drop reordering.** Items move with up/down buttons.

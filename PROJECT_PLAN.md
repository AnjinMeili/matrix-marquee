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

- Development/deployment target: Raspberry Pi at `192.168.1.194` over SSH.
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

## 12. Decisions to confirm before implementation

1. Exact matrix width, height, panel count, scan rate, and color order.
2. Preferred Pi OS image and networking stack.
3. Maximum upload size and supported video codecs.
4. Whether “time to view” and “number of repeats” are mutually exclusive or can both apply.
5. Whether playlist preview means a browser simulation, a live matrix preview, or both.
6. Desired default brightness and whether brightness should be scheduleable.
7. Whether media deletion should be blocked when an item is referenced by a playlist.

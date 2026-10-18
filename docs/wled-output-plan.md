# Plan: stream the sign to WLED panels (dropped)

**Status:** dropped on 2026-10-17. The owner is no longer considering sending
from the sign; only receiving is planned. See
[protocol-receiver-plan.md](protocol-receiver-plan.md). Kept for reference.

## The request

Send what the sign shows to WLED panels as well, over DDP. The owner already
streams to two WLED panels from MatrixPrism over Wi-Fi, and they keep up well:

| Panel | Driver | Notes |
|---|---|---|
| 128×64 | HUB75 on an ESP32 | Refresh is not the limit; Wi-Fi is |
| 64×64 | WS2812B | 4,096 LEDs are limited by data rate (~30 µs per LED per output); the achievable fps depends on how many outputs WLED splits them across |

## Is it reasonable?

Yes. Both panels together are 12,288 pixels, about 37 KB a frame:

| Rate | Bandwidth |
|---|---|
| 30 fps | ~8.8 Mbit/s |
| 20 fps | ~5.9 Mbit/s |
| 15 fps | ~4.4 Mbit/s |
| Still content | almost none (1 fps keep-alive) |

The sign's AP (2.4 GHz, channel 6, 20 MHz) manages roughly 25–35 Mbit/s of
UDP in clear air, so 15–20 fps leaves headroom. In a crowd, airtime is the
risk, not raw bandwidth: DDP is UDP with no retransmission. DDP's push flag
keeps lost packets from showing as torn frames, and WLED falls back to its
own effects a few seconds after a stream stops.

## Reuse from MatrixPrism

MatrixPrism (`~/Documents/src/matrixprism`, Node) already has the proven
pieces; this project is Python, so they would be ported, keeping their
tested choices rather than sharing code:

- `composer/lib/ddp.js` — `DdpSender` (1,440-byte packets, push flag,
  sequence) and `parseMatrixInfo` (panel size from WLED's `/json/info`);
- `composer/lib/mdns.js` — `_wled._tcp` discovery;
- its receivers work (T39): a per-receiver frame limit.

MatrixPrism's own multi-panel plan (`docs/multi-panel-plan.md`, T35: one
canvas, windows per panel) is not built and suits walls of panels better than
companion panels next to a sign.

## Design

1. **DDP output.** A sender thread per panel that only ever takes the newest
   frame, so a slow or absent panel never holds up the sign. Frames go out
   only when they change, with a 1 fps keep-alive. A frame-rate cap per
   panel, set automatically from the fps WLED reports it achieves (this
   matters for the WS2812B panel), and adjustable.
2. **Discovery.** `_wled._tcp` over mDNS, plus the AP's DHCP leases probed
   for `/json/info`. Size, name, and signal strength (RSSI) come from WLED;
   a weak link shows as a warning and can lower that panel's frame rate.
3. **Content per panel.** Either mirror the sign's picture with a fit (fill,
   whole, stretch), or redraw messages natively at each panel's shape
   (128×64 wide, 64×64 square) and mirror pictures and video. Native text is
   recommended: the sign is 3:2 and the panels are 2:1 and 1:1, so mirrored
   text is letterboxed or cropped.
4. **Settings.** A panel list: on/off, fit, fps cap, brightness, and live
   status. Brightness per panel helps the panels match: WS2812B and HUB75
   render colour differently, and WLED's brightness limiter dims bright
   frames on the WS2812B panel.
5. **Tests.** Packet layout (header, offsets, push on the last packet,
   sequence), scaling and cropping, and frames dropped rather than queued.

Pixels go out row-major; WLED's own 2D settings map them to the panels'
wiring, as MatrixPrism already relies on.

## Decisions for the owner when this is taken up

- Mirror only, or native text with mirrored media?
- When the sign stops: panels go dark, or back to their own WLED effects?
- Do the panels join the sign's Wi-Fi, or stay on another network?

## Effort

About a day for the sender, discovery, settings, and tests.

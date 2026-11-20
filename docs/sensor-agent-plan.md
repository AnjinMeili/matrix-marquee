# Plan: a sensor agent on a Pi Pico

**Status:** planned on 2026-10-17; not built. The Pico 2 is the selected baseline. Replaces wiring the sensors to
the Pi's `i2c0` (GPIO 0/1).

## The request

The sign needs more sensors than the Pi has free pins for:
- enclosure temperature and humidity;
- panel current and voltage;
- ambient light for automatic brightness;
- battery state for portable installations.

The three HUB75 chains use GPIO 2–27, leaving only GPIO 0/1. Adding a sensor
there means working on the Pi's header every time.

Instead, a Raspberry Pi Pico reads every sensor and reports to the Pi over
USB, as a serial port. It uses no GPIO, so the panel wiring is never touched
again, and new sensors are a change to the Pico, not the Pi.

## Why a Pico

| | Pico / Pico 2 | ESP32 | Sensors straight on `i2c0` |
|---|---|---|---|
| Pi pins used | none (USB) | none (USB) | GPIO 0/1 |
| Radio | none | 2.4 GHz Wi-Fi, beside the sign's own AP | none |
| Fast current sampling for peaks | yes, about 1 kHz | yes | no: Python polling would miss peaks and use the Pi's CPU |
| Spare ADC, PWM, buttons | yes | yes | no |
| Cost | about $5 | about $5–10 | $0 |

The Pico 2 (RP2350) is the baseline. It has two UARTs for
battery-management systems (BMSes), as well as two I2C buses. An ESP32 only earns its radio
for a sensor that must be wireless, such as one on a separate battery cart.

## Hardware

```text
                       USB (power + serial)
  Pi 4  ◄──────────────────────────────────────────►  Pico
                                                       │
           I2C0 (GP4 SDA, GP5 SCL), 400 kHz            │
   ┌──────────────┬──────────────┬──────────────┬──────┘
   │              │              │              │
 MAX44009      SHT45          INA228         INA226
 light         enclosure      panels' 5 V    battery side
 0x4A          0x44           0x40           0x41
                              ALERT → GP15
           I2C1 (GP6 SDA, GP7 SCL): optional second SHT45 at the air intake (0x44)
           UART0 (GP0 TX, GP1 RX) and UART1 (GP8 TX, GP9 RX), each through an isolator:
                              the DIY packs' BMSes
           GP16, GP17: fan PWM out / tach in (if the fans are 4-pin)
           GP18–GP20: buttons (optional)
```

- **Light (MAX44009, 0x4A).**
  - **Range:** reads from 0.045 to 188,000 lux and switches range by itself,
    so it covers night to full sun without saturating. A BH1750 saturates
    near 65,000 lux, below full sun. A VEML7700 (0x10, to about 120,000 lux)
    is the fallback.
  - **Reading it:** the lux value's two bytes must be read in one I2C
    transaction, with a repeated start, or a range change between the reads
    gives a wrong value.
  - **Address:** the A0 pin selects 0x4B for a second sensor.
  - **Mounting:** at the sign's top edge, facing the viewers and hooded from
    the LEDs, so it never sees the sign's own light.
  - **Sun on the panels:** dark panels in full sun gain heat on top of their
    own. Plotting light level beside enclosure temperature shows how much the
    sun adds.
- **Enclosure (SHT45, 0x44).**
  - Main sensor: at the top rear, on the exhaust side behind the panels.
  - Optional second sensor at the intake, to show whether the fans are
    working (exhaust minus intake). The SHT45 has a fixed address, so the
    second goes on the Pico's other I2C bus.
- **Panel current (INA228, 0x40).**
  - **Shunt:** an external 50 A / 75 mV shunt, high side, on the panels' 5 V
    feed. Breakout boards' own shunts carry only about 10 A.
  - **Voltage:** sense the bus voltage at the panel end, so voltage drop in
    the leads shows.
  - **ALERT** goes to GP15, set to trip at an over-current limit in hardware.
- **Battery side (INA226, 0x41).** On the pack's output, before the 5 V
  converter. With the INA228 after it, the sign knows the pack's voltage,
  the true draw on the battery, and the converter's losses. Runtime estimates
  depend on all three. When the pack has a BMS the sign can read, the BMS
  gives the same figures plus the state of charge, so the INA226 becomes a
  fallback for packs without one (see "Power sources").
- **Fans:** PWM and tach, if they are 4-pin; 2-pin fans need a logic-level
  MOSFET on a PWM pin.
- **Buttons, optional:** for example a large "next item" button.

**Cabling.**
- Keep I2C leads under about 50 cm, by placing the Pico near the sensors.
  Only the USB cable runs back to the Pi.
- On a mobile installation, use a short USB cable with strain relief,
  right-angle or locking.
- The Pico draws its power from the Pi's USB. The ground is shared through the
  USB cable and the panels' ground, as the Pi and panels already share one.

## Power sources

The sign should identify its active power source, report its charge, and
estimate its remaining runtime.

| Source | How the sign reads it | Works offline? |
|---|---|---|
| Battery pack with BMS | The BMS's UART, wired to the Pico 2 | Yes |
| Cloud-managed battery | The provider's API, with a locally stored developer key | No: only when the sign has internet |

- **Battery packs: prefer a wired UART to Bluetooth when available.**
  - A wire is steadier than a Bluetooth link on a mobile display, and it adds
    nothing on 2.4 GHz beside the sign's access point.
  - JK, JBD, Daly, and ANT protocols are all documented by open-source
    projects (the ESPHome BMS components, for example). The driver depends on
    the brand.
  - **Use an isolator** (ADuM1201 or similar) on each UART. A BMS's UART
    ground is often the cells' negative (B−). The rest of the system sits on
    the switched negative (P−), so wiring them together directly could bypass
    the BMS's protection or damage the port.
  - A pack's Bluetooth stays free for its own phone app.
- **Cloud-managed batteries are optional.** Keep access disabled by default,
  store API keys locally, never log them, and fall back to local INA228 and
  INA226 readings whenever the sign is offline.
- **One shape for every source.** Each adapter reports the same fields:
  `source`, `soc_pct`, `pack_v`, `pack_a`, `watts_out`, `minutes_left`,
  `temp_c`, and `cells_v` (where available). Whichever source is connected
  shows as the active one on the dashboard. Its readings drive the runtime
  estimate and a low-battery warning (event, dashboard, and optionally an
  automatic dim at a level you set).
- **The efficiency of the path matters.** Running the 5 V supply from an
  AC battery output goes through its inverter, which has its own idle draw
  and conversion loss. A DC output, if one suits, into a 5 V buck
  converter usually wastes less. With INA readings on both sides, the sign
  would show the difference.

## Peaks, and what the fuse cares about

Panel current pulses at the refresh rate, in the kHz range. The 2200 µF
capacitors at each panel absorb those pulses, so neither the supply nor the
fuse sees them.

What does reach the fuse is a white frame held for a while. Fuses respond to
heating over milliseconds to seconds, so the useful readings are:
- the INA228's own averages, over about 1 ms each (16 samples of 50 µs),
  sampled continuously by the Pico;
- reported to the Pi as each second's average and peak.

The ALERT pin catches anything over the hardware limit without waiting for
software, and the Pico reports it straight away.

## Messages

USB serial: one JSON object per line in each direction, small enough to read
in a terminal.

The Pico sends:

```json
{"type":"hello","firmware":"0.1.0","sensors":{"light":true,"enclosure":true,"intake":false,"panel_power":true,"battery":true,"fans":false}}
{"type":"reading","t":1234,"lux":48210,"temp_c":41.2,"rh":38.0,"panel_a":11.8,"panel_a_peak":13.1,"panel_v":5.08,"battery_v":12.9,"battery_a":5.2,"fan_rpm":null}
{"type":"alert","kind":"overcurrent","panel_a":18.4}
{"type":"battery","source":"bms1","soc_pct":82,"pack_v":13.21,"pack_a":-5.4,"temp_c":24.5,"cells_v":[3.30,3.31,3.30,3.30]}
{"type":"button","name":"next"}
```

- Readings come once a second.
- A sensor that is missing or fails reads `null`, and the rest carry on.
- `t` is the Pico's uptime in seconds, so the Pi can tell whether the Pico
  has restarted.

The Pi sends:

```json
{"cmd":"ping"}
{"cmd":"fan","percent":60}
{"cmd":"alert","panel_a":18.0}
```

**Safety without the Pi.** If the Pi has said nothing for 10 s, the Pico runs
the fans at full speed. It does the same at power-up, until the Pi sets them.
A hung Pi therefore never leaves the fans off.

## The Pi side

- **A new `sensors.py`** with a `SensorAgent` thread. It finds the Pico by its
  USB ID under `/dev/serial/by-id/`, or through `MATRIX_PLAYER_SENSOR_PORT`
  (added to `.env.example`). It reconnects when the cable is bumped and keeps
  the latest reading in memory.
  - It never blocks rendering or a request, per the "never block the sign"
    rule.
  - With no Pico, the sign works exactly as it does today, and the Heat &
    power card says "Sensors not connected".
- **The health monitor** adds the readings to its per-minute record, with
  additive columns, still written every few minutes to spare the SD card. It
  logs events for over-current, enclosure heat, a dew-point warning (enclosure
  air near condensation), low battery, and the Pico disconnecting or
  reconnecting.
- **Features that use the readings:**
  - **Automatic brightness:**
    - light level drives brightness on a log-lux curve;
    - readings are smoothed over 20–30 s, with steps of a few percent, so
      passing shadows don't make it pump;
    - you set the minimum and maximum, and the slider still overrides.
  - **Power limiter:**
    - measured current calibrates the estimate made from each frame's pixels;
    - an ALERT dims straight away, then the sign recovers gradually.
  - **Battery:** pack voltage and current give runtime left, shown on the
    dashboard.
  - **Fans:** speed follows the enclosure temperature.
  - **Buttons:** they act like the matching buttons in the web UI.
- **Web UI:**
  - the Heat & power card gains light, enclosure, current, and battery rows;
  - charts follow the `dataviz` skill;
  - the Settings screen gains an "Automatic brightness" switch with
    minimum and maximum settings.

## Firmware

- **Where:** `firmware/sensor-agent/` in this repository, in MicroPython.
  - It reads the INA228 at about 1 kHz, which is plenty for 1 ms averages.
  - If it falls short, that one loop moves to C with the Pico SDK.
- **Robustness:** each sensor driver is a small module that is probed at
  start-up. The hardware watchdog restarts the Pico if its loop hangs.
- **Installing:** copy the files with `mpremote cp`. The README gets a
  "Sensor agent" section. Its version appears in `hello` and on the Heat &
  power card.
- **Sharing:** the agent knows nothing about MatrixMarquee beyond the message
  format, so MatrixPrism or matrix-jack could use it later.

## Tests

- **Message parsing:** good lines, broken lines, `null` sensors, and a Pico
  restarting mid-stream.
- **Reconnecting:** a fake serial port that disappears and returns.
- **Brightness curve:** smoothing, hysteresis, the user's minimum and maximum,
  and the manual override winning.
- **Limiter:** an alert dims at once, then recovers.
- **Health monitor:** sensor columns are written in minute buckets.
- **Firmware:** the MicroPython drivers' register maths, run under CPython
  with a fake I2C bus.

## Open decisions

- **The battery BMS:** its protocol, which decides the UART driver; and the
  pack's chemistry, cell count, and capacity.
- **How each source feeds the panels:** AC into the 20 A supply, or DC
  through a buck converter.
- **The cloud battery link:** worth setting up (it needs a provider account
  and internet), or rely on local INA readings?
- **Fans:** 2-pin or 4-pin, and should the Pico control them?
- **Buttons:** wanted, and which actions?
- **Mounting** for the light sensor and Pico on the display.

## Effort

- About a day for the firmware and the Pi-side agent, with tests.
- Then half a day each for automatic brightness, the battery estimate, and the
  limiter, once the hardware is on the bench.
- Build it in that order: the agent and readings first, so the sensors can be
  checked against your clamp meter before anything acts on them.

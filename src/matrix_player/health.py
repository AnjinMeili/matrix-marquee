"""Heat and power monitoring for an enclosed outdoor sign.

Samples the Pi's temperature, CPU clock, and firmware throttle flags every few
seconds, keeps a per-minute record for a week so problems that happened while
nobody watched are still visible, and logs an event whenever a problem starts
or clears. The Pi's temperature stands in for the enclosure as a whole.
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from .database import Database

logger = logging.getLogger(__name__)

WARM_C = 70.0  # worth checking the fans
HOT_C = 80.0  # the Pi 4 starts slowing itself down here
HYSTERESIS_C = 3.0  # cool this far below a level before calling it cleared
SAMPLE_SECONDS = 10.0
KEEP_SECONDS = 7 * 24 * 3600

# Bits of `vcgencmd get_throttled`: low bits are "now", the same bits << 16 are
# "has happened since boot".
UNDERVOLTAGE = 0x1
FREQ_CAPPED = 0x2
THROTTLED = 0x4
SOFT_TEMP_LIMIT = 0x8
SINCE_BOOT_SHIFT = 16

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
CPU_FREQ = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
VOLTAGE_ALARMS = Path("/sys/class/hwmon")


@dataclass
class Reading:
    temperature_c: Optional[float]
    flags: Optional[int]  # throttle bits, or None when the firmware can't be asked
    freq_mhz: Optional[int]


def read_temperature() -> Optional[float]:
    try:
        return round(int(THERMAL_ZONE.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_freq_mhz() -> Optional[int]:
    try:
        return int(CPU_FREQ.read_text().strip()) // 1000
    except (OSError, ValueError):
        return None


def read_throttled() -> Optional[int]:
    vcgencmd = shutil.which("vcgencmd")
    if vcgencmd:
        try:
            output = subprocess.run(
                [vcgencmd, "get_throttled"], capture_output=True, text=True, timeout=3, check=True
            ).stdout
            match = re.search(r"0x[0-9a-fA-F]+", output)
            if match:
                return int(match.group(0), 16)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    # Without vcgencmd, the kernel's under-voltage alarm still covers power.
    for alarm in VOLTAGE_ALARMS.glob("hwmon*/in0_lcrit_alarm"):
        try:
            return UNDERVOLTAGE if alarm.read_text().strip() == "1" else 0
        except OSError:
            continue
    return None


def read_sensors() -> Reading:
    return Reading(read_temperature(), read_throttled(), read_freq_mhz())


@dataclass
class _Minute:
    minute: int
    temp_max: Optional[float] = None
    flags: int = 0
    freq_min: Optional[int] = None


@dataclass
class _Problems:
    """Which problems are active, for spotting when one starts or clears."""

    undervoltage: bool = False
    throttled: bool = False
    warm: bool = False
    hot: bool = False
    seen: Dict[str, float] = field(default_factory=dict)


class HealthMonitor:
    def __init__(
        self,
        database: Database,
        sampler: Callable[[], Reading] = read_sensors,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.sampler = sampler
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current: Optional[Reading] = None
        self._minute: Optional[_Minute] = None
        self._problems = _Problems()
        self._last_prune = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="health-monitor", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception:
                logger.exception("health_sample_failed")
            self._stop.wait(SAMPLE_SECONDS)

    # ----- sampling -----

    def sample(self) -> Reading:
        reading = self.sampler()
        now = self.clock()
        minute = int(now // 60)
        with self._lock:
            self._current = reading
            if self._minute is not None and self._minute.minute != minute:
                finished, self._minute = self._minute, None
            else:
                finished = None
            if self._minute is None:
                self._minute = _Minute(minute)
            bucket = self._minute
            if reading.temperature_c is not None:
                bucket.temp_max = max(bucket.temp_max or reading.temperature_c, reading.temperature_c)
            if reading.flags is not None:
                bucket.flags |= reading.flags & 0xF
            if reading.freq_mhz is not None:
                bucket.freq_min = min(bucket.freq_min or reading.freq_mhz, reading.freq_mhz)
        if finished is not None:
            self._write_minute(finished)
        self._track_events(reading, now)
        if now - self._last_prune > 3600:
            self._last_prune = now
            self._prune(now)
        return reading

    def _write_minute(self, bucket: _Minute) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO health_samples (minute, temp_max, flags, freq_min) VALUES (?, ?, ?, ?)
                   ON CONFLICT(minute) DO UPDATE SET
                     temp_max = MAX(COALESCE(temp_max, excluded.temp_max), COALESCE(excluded.temp_max, temp_max)),
                     flags = flags | excluded.flags,
                     freq_min = MIN(COALESCE(freq_min, excluded.freq_min), COALESCE(excluded.freq_min, freq_min))""",
                (bucket.minute, bucket.temp_max, bucket.flags, bucket.freq_min),
            )

    def _flush(self) -> None:
        with self._lock:
            bucket, self._minute = self._minute, None
        if bucket is not None:
            try:
                self._write_minute(bucket)
            except Exception:
                logger.exception("health_flush_failed")

    def _prune(self, now: float) -> None:
        cutoff = now - KEEP_SECONDS
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM health_samples WHERE minute < ?", (int(cutoff // 60),))
            connection.execute("DELETE FROM health_events WHERE at < ?", (cutoff,))

    def _record(self, kind: str, detail: str, now: float, problem: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO health_events (at, kind, detail) VALUES (?, ?, ?)", (now, kind, detail)
            )
        log = logger.warning if problem else logger.info
        log("health_event", extra={"kind": kind, "detail": detail})

    def _track_events(self, reading: Reading, now: float) -> None:
        state = self._problems
        flags = reading.flags or 0
        temp = reading.temperature_c
        temp_text = f"{temp:.1f} °C" if temp is not None else "temperature unknown"
        checks = [
            ("undervoltage", bool(flags & UNDERVOLTAGE), "power_low", "power_ok",
             "Supply voltage dropped too low", "Supply voltage back to normal"),
            ("throttled", bool(flags & (THROTTLED | SOFT_TEMP_LIMIT)), "throttled", "throttle_cleared",
             f"Pi slowing itself down at {temp_text}", f"Pi back to full speed at {temp_text}"),
        ]
        if temp is not None:
            hot = temp >= HOT_C or (state.hot and temp > HOT_C - HYSTERESIS_C)
            warm = temp >= WARM_C or (state.warm and temp > WARM_C - HYSTERESIS_C)
            checks += [
                ("hot", hot, "hot", "cooled_from_hot",
                 f"Running hot: {temp_text}", f"Cooled below {HOT_C:.0f} °C ({temp_text})"),
                # "Warm" stays active while hot, so heating up never logs a cool-down.
                ("warm", warm or hot, "warm", "cooled",
                 f"Getting warm: {temp_text}", f"Cooled down ({temp_text})"),
            ]
        for name, active, start_kind, clear_kind, start_text, clear_text in checks:
            was = getattr(state, name)
            if active and not was:
                state.seen[name] = now
                self._record(start_kind, start_text, now, problem=True)
            elif was and not active:
                self._record(clear_kind, clear_text, now, problem=False)
            setattr(state, name, active)

    # ----- reporting -----

    def summary(self) -> dict:
        """Current level and a plain-language message for the Sign page."""
        with self._lock:
            reading = self._current
            bucket_max = self._minute.temp_max if self._minute else None
        now = self.clock()
        day_ago = now - 24 * 3600
        row = self.database.fetch_one(
            "SELECT MAX(temp_max) AS temp_max FROM health_samples WHERE minute >= ?",
            (int(day_ago // 60),),
        )
        day_max = max(
            (value for value in (row["temp_max"] if row else None, bucket_max) if value is not None),
            default=None,
        )
        recent = self.database.fetch_all(
            """SELECT at, kind, detail FROM health_events
               WHERE at >= ? AND kind IN ('power_low', 'throttled', 'hot')
               ORDER BY at DESC LIMIT 5""",
            (day_ago,),
        )
        temp = reading.temperature_c if reading else None
        flags = (reading.flags or 0) if reading else 0
        sensors = reading is not None and (reading.temperature_c is not None or reading.flags is not None)
        level, message = "ok", "Temperature and power are normal."
        if not sensors:
            level, message = "unknown", "No temperature or power sensors on this device."
        elif flags & UNDERVOLTAGE:
            level, message = "critical", "Power is too low for the Pi right now. Check the supply and its cables."
        elif flags & (THROTTLED | SOFT_TEMP_LIMIT) or (temp is not None and temp >= HOT_C):
            level = "critical"
            message = (
                f"Running hot ({temp:.0f} °C). The Pi is slowing itself down, so motion may stutter. Check the fans."
                if temp is not None
                else "The Pi is slowing itself down. Check the fans."
            )
        elif temp is not None and temp >= WARM_C:
            level, message = "warning", f"Getting warm ({temp:.0f} °C). Make sure the fans have airflow."
        elif recent or flags & ((UNDERVOLTAGE | THROTTLED) << SINCE_BOOT_SHIFT):
            level = "warning"
            message = (
                f"{recent[0]['detail']} earlier. It is fine now."
                if recent
                else "The power dipped or the Pi slowed down earlier. It is fine now."
            )
        return {
            "level": level,
            "message": message,
            "temperature_c": temp,
            "freq_mhz": reading.freq_mhz if reading else None,
            "day_max_c": day_max,
            "undervoltage_now": bool(flags & UNDERVOLTAGE),
            "throttled_now": bool(flags & (THROTTLED | SOFT_TEMP_LIMIT)),
            "undervoltage_since_boot": bool(flags & (UNDERVOLTAGE << SINCE_BOOT_SHIFT)),
            "throttled_since_boot": bool(flags & ((THROTTLED | SOFT_TEMP_LIMIT) << SINCE_BOOT_SHIFT)),
            "warm_c": WARM_C,
            "hot_c": HOT_C,
        }

    def history(self, hours: int = 24, bucket_minutes: int = 5) -> List[dict]:
        """Per-bucket max temperature and any problem flags, oldest first."""
        self._flush_current_copy()
        now = self.clock()
        start_minute = int((now - hours * 3600) // 60)
        rows = self.database.fetch_all(
            """SELECT (minute / ?) * ? AS bucket, MAX(temp_max) AS temp_max,
                      MAX(flags & 1) AS undervoltage, MAX((flags >> 2) & 1 | (flags >> 3) & 1) AS throttled
               FROM health_samples WHERE minute >= ? GROUP BY bucket ORDER BY bucket""",
            (bucket_minutes, bucket_minutes, start_minute),
        )
        return [
            {
                "at": int(row["bucket"]) * 60,
                "temp_max": row["temp_max"],
                "undervoltage": bool(row["undervoltage"]),
                "throttled": bool(row["throttled"]),
            }
            for row in rows
        ]

    def _flush_current_copy(self) -> None:
        """Writes the in-progress minute without ending it, so history is current."""
        with self._lock:
            bucket = _Minute(**vars(self._minute)) if self._minute else None
        if bucket is not None:
            self._write_minute(bucket)

    def events(self, hours: int = 24, limit: int = 20) -> List[dict]:
        rows = self.database.fetch_all(
            "SELECT at, kind, detail FROM health_events WHERE at >= ? ORDER BY at DESC LIMIT ?",
            (self.clock() - hours * 3600, limit),
        )
        problems = {"power_low", "throttled", "hot", "warm"}
        return [
            {"at": row["at"], "kind": row["kind"], "detail": row["detail"], "problem": row["kind"] in problems}
            for row in rows
        ]

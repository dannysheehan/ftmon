"""Global config.toml loading (FS-02) and quiet hours (NO-03).

Deliberately forgiving: a broken or missing config.toml yields the defaults
with a warning list — the daemon must keep monitoring on a bad edit (same
posture as PM-04 for monitor definitions), never refuse to start.

Quiet hours are wall-clock *local* time: "hold notifications overnight"
means the user's overnight, wherever the laptop currently thinks it is.
Tests pass an explicit tzinfo for determinism; production leaves it None
(system local zone). datetime.fromtimestamp on a passed-in wall timestamp is
a conversion, not a clock read, so TS-03 is preserved.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

__all__ = ["QuietHours", "AppConfig", "load_config", "parse_hhmm"]


def parse_hhmm(text: str) -> int:
    """'HH:MM' -> minutes since local midnight. Raises ValueError."""
    hh, _, mm = text.partition(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"not a time of day: {text!r}")
    return h * 60 + m


@dataclass(frozen=True)
class QuietHours:
    """NO-03: delivery-only suppression window. Incidents open, escalate and
    clear regardless; only warning-and-below *notifications* are held."""

    start_min: int  # minutes since local midnight
    end_min: int
    tz: tzinfo | None = None  # None = system local; tests inject a fixed zone

    def active(self, wall: float) -> bool:
        m = self._minute_of_day(wall)
        if self.start_min == self.end_min:
            return False  # zero-length window: treat as disabled, not always-on
        if self.start_min < self.end_min:
            return self.start_min <= m < self.end_min
        return m >= self.start_min or m < self.end_min  # crosses midnight

    def _minute_of_day(self, wall: float) -> int:
        dt = datetime.fromtimestamp(wall, self.tz)
        return dt.hour * 60 + dt.minute


@dataclass(frozen=True)
class AppConfig:
    tick_seconds: float = 5.0
    collect_cmdline: bool = True  # SE-04
    quiet: QuietHours | None = None  # None = quiet hours disabled
    web_port: int = 8420


def load_config(config_file: Path, tz: tzinfo | None = None) -> tuple[AppConfig, list[str]]:
    """Returns (config, warnings). Every failure mode degrades to a default
    plus a warning string — see module docstring for why."""
    warnings: list[str] = []
    try:
        raw = tomllib.loads(config_file.read_text())
    except FileNotFoundError:
        return AppConfig(), []
    except (OSError, tomllib.TOMLDecodeError) as e:
        return AppConfig(), [f"config.toml unreadable, using defaults: {e}"]

    def num(section: str, key: str, default: float, lo: float, hi: float) -> float:
        v = raw.get(section, {}).get(key, default)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
            warnings.append(f"[{section}] {key} = {v!r} invalid; using {default}")
            return default
        return float(v)

    tick = num("daemon", "tick_seconds", 5.0, 1.0, 60.0)
    port = int(num("web", "port", 8420, 1024, 65535))
    cc = raw.get("privacy", {}).get("collect_cmdline", True)
    if not isinstance(cc, bool):
        warnings.append(f"[privacy] collect_cmdline = {cc!r} invalid; using true")
        cc = True

    quiet = None
    q = raw.get("quiet_hours", {})
    if q.get("enabled", False) is True:
        try:
            quiet = QuietHours(
                start_min=parse_hhmm(str(q.get("start", "22:00"))),
                end_min=parse_hhmm(str(q.get("end", "08:00"))),
                tz=tz,
            )
        except (ValueError, TypeError) as e:
            warnings.append(f"[quiet_hours] invalid start/end; quiet hours off: {e}")

    return AppConfig(tick_seconds=tick, collect_cmdline=cc, quiet=quiet,
                     web_port=port), warnings

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

import os
import stat
import tomllib
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from urllib.parse import urlsplit

__all__ = [
    "AppConfig", "ChannelConfig", "QuietHours", "SecretRef", "SecretValue",
    "load_config", "parse_hhmm",
]


class SecretValue:
    """A resolved credential which cannot leak through normal formatting (SE-05).

    Adapters deliberately have to opt into the private accessor at the point
    where they construct authentication data.  Keeping the unsafe operation
    conspicuous makes accidental logging during configuration much less likely.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def _reveal(self) -> str:
        return self.__value


@dataclass(frozen=True)
class SecretRef:
    """An external credential reference, never a credential itself (SE-05)."""

    env: str | None = None
    file: Path | None = None

    def __post_init__(self) -> None:
        if (self.env is None) == (self.file is None):
            raise ValueError("exactly one environment or file secret reference is required")

    def resolve(self, environ: dict[str, str] | None = None) -> SecretValue:
        if self.env is not None:
            value = (os.environ if environ is None else environ).get(self.env)
            if value is None:
                raise ValueError(f"secret environment variable {self.env!r} is not set")
        else:
            assert self.file is not None
            value = self._read_file(self.file)
        # Credential files commonly end in one newline. Strip surrounding ASCII
        # whitespace first, but reject embedded controls that are unsafe in headers.
        value = value.strip(" \t\r\n\v\f")
        if not value:
            raise ValueError("secret reference resolved to an empty value")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("secret contains a NUL or embedded newline")
        return SecretValue(value)

    @staticmethod
    def _read_file(path: Path) -> str:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise ValueError(f"secret credential file cannot be opened safely: {path}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"secret credential file is not a regular file: {path}")
            if info.st_size > 8192:
                raise ValueError(f"secret credential file exceeds 8 KiB: {path}")
            if info.st_uid != os.geteuid():
                raise ValueError(f"secret credential file is not owned by this account: {path}")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError(
                    f"secret credential file must not be group/world accessible: {path}"
                )
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                return stream.read(8193)
        except UnicodeError as exc:
            raise ValueError(f"secret credential file is not UTF-8 text: {path}") from exc
        finally:
            if fd >= 0:
                os.close(fd)


@dataclass(frozen=True)
class ChannelConfig:
    enabled: bool = False
    min_severity: int = 2
    settings: tuple[tuple[str, object], ...] = ()
    secret: SecretRef | None = None


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
    channels: tuple[tuple[str, ChannelConfig], ...] = (
        ("desktop", ChannelConfig(enabled=True, min_severity=0)),
    )

    def channel(self, name: str) -> ChannelConfig | None:
        return dict(self.channels).get(name)


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

    channels = _parse_channels(raw, warnings)
    return AppConfig(tick_seconds=tick, collect_cmdline=cc, quiet=quiet,
                     web_port=port, channels=channels), warnings


_SEVERITIES = {"info": 0, "notice": 1, "warning": 2, "error": 3, "critical": 4}


def _parse_channels(raw: dict, warnings: list[str]) -> tuple[tuple[str, ChannelConfig], ...]:
    notify = raw.get("notify", {})
    if not isinstance(notify, dict):
        warnings.append("[notify] must be a table; remote channels disabled")
        notify = {}
    parsed: list[tuple[str, ChannelConfig]] = []
    for name in ("desktop", "ntfy", "webhook", "smtp"):
        section = notify.get(name, {})
        if not isinstance(section, dict):
            warnings.append(f"[notify.{name}] must be a table; channel disabled")
            section = {}
        default_enabled = name == "desktop"
        enabled = section.get("enabled", default_enabled)
        severity = section.get("min_severity", "info" if name == "desktop" else "warning")
        errors: list[str] = []
        if not isinstance(enabled, bool):
            errors.append("enabled must be true or false")
        if severity not in _SEVERITIES:
            errors.append("min_severity must be info, notice, warning, error, or critical")

        secret: SecretRef | None = None
        settings: dict[str, object] = {}
        if name == "ntfy":
            secret = _secret_ref(section, "token", errors)
            base_url = section.get("base_url", "https://ntfy.sh")
            topic = section.get("topic")
            if not _http_url(base_url, allow_path=False):
                errors.append("base_url must be an http(s) origin without credentials")
            if enabled is True and (not isinstance(topic, str) or not topic.strip()):
                errors.append("topic is required when enabled")
            settings = {"base_url": base_url, "topic": topic}
        elif name == "webhook":
            secret = _secret_ref(section, "url", errors)
        elif name == "smtp":
            secret = _secret_ref(section, "password", errors)
            host = section.get("host")
            tls = section.get("tls", "starttls")
            port = section.get("port", 587)
            recipients = section.get("to")
            if enabled is True and (not isinstance(host, str) or not host):
                errors.append("host is required when enabled")
            if tls not in ("starttls", "implicit"):
                errors.append("tls must be starttls or implicit")
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                errors.append("port must be an integer from 1 to 65535")
            if enabled is True and (
                not isinstance(recipients, list)
                or not recipients
                or not all(isinstance(item, str) and item for item in recipients)
            ):
                errors.append("to must be a non-empty string array when enabled")
            settings = {
                "host": host, "port": port, "tls": tls,
                "username": section.get("username"), "from": section.get("from"),
                "to": recipients,
            }

        # Disabled channels may be incomplete scaffolding, but unsafe literal
        # secrets are always errors so a later enable cannot activate them.
        if enabled is True and name != "desktop" and secret is None:
            errors.append("an external secret reference is required when enabled")
        if enabled is True and secret is not None:
            try:
                resolved = secret.resolve()
                if name == "webhook" and not _http_url(resolved._reveal(), allow_path=True):
                    errors.append("resolved webhook URL must be http(s) without credentials")
            except ValueError:
                # Do not propagate resolver details: this warning is safe for
                # doctor/web/log surfaces and can never contain the credential.
                errors.append("secret reference is unavailable or unsafe")
        if errors:
            warnings.extend(f"[notify.{name}] {error}; channel disabled" for error in errors)
            enabled = False
        parsed.append((name, ChannelConfig(
            enabled=enabled is True,
            min_severity=_SEVERITIES.get(severity, 2),
            settings=tuple(settings.items()),
            secret=secret,
        )))
    return tuple(parsed)


def _secret_ref(section: dict, stem: str, errors: list[str]) -> SecretRef | None:
    if stem in section:
        errors.append(f"literal {stem} is forbidden; use {stem}_env or {stem}_file")
    env, file = section.get(f"{stem}_env"), section.get(f"{stem}_file")
    if env is not None and (not isinstance(env, str) or not env):
        errors.append(f"{stem}_env must be a non-empty string")
        env = None
    if file is not None and (not isinstance(file, str) or not file):
        errors.append(f"{stem}_file must be a non-empty path string")
        file = None
    if env is not None and file is not None:
        errors.append(f"{stem}_env and {stem}_file are mutually exclusive")
        return None
    return SecretRef(env=env) if env is not None else (SecretRef(file=Path(file)) if file else None)


def _http_url(value: object, *, allow_path: bool) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return (
        parts.scheme in ("http", "https") and bool(parts.hostname)
        and parts.username is None and parts.password is None
        and (allow_path or not parts.query) and not parts.fragment
        and (allow_path or parts.path in ("", "/"))
    )

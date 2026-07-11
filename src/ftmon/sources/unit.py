"""Service/process watchdog sampler (SA-04, SPEC 7.7.6).

Watchlist-driven: entities are *synthetic* (CA-08) — a watched unit that is
down still produces an entity with present=0 every cycle, because absence
is the alerting signal, never the entity's removal. That inversion is the
whole reason watchlist sources exist: for discovered entities (processes)
disappearing means "resolved", for expected services it means "page me".

systemd units are read with `systemctl show` (SA-04 names this mechanism):
one exec per watched unit per cycle is fine at the expected scale (a
handful of watchlist entries at 60 s), and it works identically for system
and --user units without a dbus binding. The exec is injected (run_cmd) so
tests supply canned output instead of needing systemd in CI (TS-02).

The optional `during = "HH:MM-HH:MM"` field scopes *when the check applies*:
outside the window the entity reports present=1 (a backup service is
supposed to be dead at noon — that's health, not failure).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import ClassVar

import psutil

from ftmon.clock import Clock
from ftmon.config import parse_hhmm
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


def _systemctl_show(unit: str) -> str:
    """Default run_cmd: `systemctl show` never fails for unknown units (it
    reports ActiveState=inactive), so a typo'd watchlist entry alerts as
    down instead of crashing the sampler (PL-03)."""
    try:
        return subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveState,NRestarts",
             "--no-pager"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


class UnitSampler:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["unit"]

    def __init__(self, clock: Clock,
                 run_cmd: Callable[[str], str] = _systemctl_show) -> None:
        self._clock = clock
        self._run_cmd = run_cmd

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        entities: list[EntitySample] = []
        for item in options.get("watchlist", ()):
            if not isinstance(item, Mapping):
                continue  # validated shape is dicts; tolerate garbage (PL-03)
            if self._clock.monotonic() > deadline_mono:
                break
            if not _in_window(item.get("during"), now):
                # out of scope right now: report healthy, not absent — an
                # UNKNOWN would freeze confirm counters instead of clearing
                entity = self._out_of_window_entity(item)
                if entity is not None:
                    entities.append(entity)
                continue
            if "unit" in item:
                entities.append(self._sample_unit(str(item["unit"])))
            elif "process" in item:
                entities.append(self._sample_process(str(item["process"])))
        return Snapshot(source=self.decl.name, ts=now, entities=tuple(entities))

    def _out_of_window_entity(self, item: Mapping) -> EntitySample | None:
        if "unit" in item:
            name = str(item["unit"])
            return EntitySample(entity_id=f"unit:{name}",
                                attrs={"unit": name, "kind": "unit"},
                                metrics={"present": 1.0})
        if "process" in item:
            pat = str(item["process"])
            return EntitySample(entity_id=f"proc:{pat}",
                                attrs={"unit": pat, "kind": "process"},
                                metrics={"present": 1.0})
        return None

    def _sample_unit(self, unit: str) -> EntitySample:
        out = self._run_cmd(unit)
        props = dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )
        present = 1.0 if props.get("ActiveState") == "active" else 0.0
        metrics: dict[str, float] = {"present": present}
        try:
            metrics["restarts"] = float(int(props.get("NRestarts", "")))
        except ValueError:
            pass  # older systemd / non-service units: no counter, not zero
        return EntitySample(
            entity_id=f"unit:{unit}",
            attrs={"unit": unit, "kind": "unit"},
            metrics=metrics,
        )

    def _sample_process(self, pattern: str) -> EntitySample:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        present = 0.0
        for proc in psutil.process_iter(["name"]):
            try:
                if rx.search(proc.info["name"] or ""):
                    present = 1.0
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return EntitySample(
            entity_id=f"proc:{pattern}",
            attrs={"unit": pattern, "kind": "process"},
            metrics={"present": present},
        )


def _in_window(during, wall: float) -> bool:
    """`during` absent -> always in scope. Window may cross midnight (same
    convention as quiet hours)."""
    if not during:
        return True
    try:
        start_s, _, end_s = str(during).partition("-")
        start, end = parse_hhmm(start_s), parse_hhmm(end_s)
    except ValueError:
        return True  # unparseable window: fail toward checking, not silence
    dt = datetime.fromtimestamp(wall)
    m = dt.hour * 60 + dt.minute
    if start == end:
        return True
    if start < end:
        return start <= m < end
    return m >= start or m < end

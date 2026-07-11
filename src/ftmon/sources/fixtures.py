"""Scenario fixtures (TS-04): replayable fake sources for tests and repro.

One JSONL format serves unit tests, the tier-1 e2e harness, and manual
"replay what my machine did" tooling — that sharing is the point of TS-04:
a scenario captured once (or written by hand in a bug report) runs
identically at every test tier. Lines:

    {"at": 0,  "source": "process", "entities": [
        {"entity_id": "firefox:100:1", "attrs": {"name": "firefox"},
         "metrics": {"rss_bytes": 1000000, "cpu_pct": 2.0}}]}
    {"at": 60, "event": {"source": "journald", "provider": "kernel",
        "severity": 3, "message": "Out of memory: ..."}}

`at` is seconds relative to daemon start (first sample), so the same file
works at any wall time. A sampler line is a *state*: it stays in force until
the next line for the same source — a 40-minute steady state is one line,
not 40. Event lines feed the M3 FixtureEventSource; the loader already
accepts them so scenario files never need version bumps.

Named library scenarios are built programmatically (a 60-minute leak is a
loop, not 60 hand-written lines) and can be exported to JSONL with
`dump_scenario` for use outside the test tree.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ftmon.model import EntitySample, EventRecord, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

__all__ = [
    "Scenario",
    "FixtureSampler",
    "FixtureEventSource",
    "load_scenario",
    "dump_scenario",
    "scenario",
    "fixture_samplers",
    "SCENARIO_NAMES",
]


@dataclass(frozen=True)
class Scenario:
    """Parsed scenario: sample lines per source (sorted by `at`), plus event
    lines for the M3 event fixture."""

    samples: Mapping[str, tuple[dict, ...]]  # source -> lines sorted by at
    events: tuple[dict, ...]

    def sources(self) -> set[str]:
        return set(self.samples)


def _build(lines: list[dict]) -> Scenario:
    by_source: dict[str, list[dict]] = {}
    events: list[dict] = []
    for i, line in enumerate(lines):
        if "at" not in line:
            raise ValueError(f"scenario line {i}: missing 'at'")
        if "event" in line:
            events.append(line)
        elif "source" in line and "entities" in line:
            by_source.setdefault(line["source"], []).append(line)
        else:
            raise ValueError(f"scenario line {i}: need source+entities or event")
    return Scenario(
        samples={s: tuple(sorted(v, key=lambda x: x["at"])) for s, v in by_source.items()},
        events=tuple(sorted(events, key=lambda x: x["at"])),
    )


def load_scenario(path: Path) -> Scenario:
    lines = [json.loads(text) for text in path.read_text().splitlines() if text.strip()]
    return _build(lines)


def dump_scenario(scn: Scenario, path: Path) -> None:
    all_lines = [line for lines in scn.samples.values() for line in lines]
    all_lines += list(scn.events)
    all_lines.sort(key=lambda x: x["at"])
    path.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in all_lines))


class FixtureSampler:
    """Sampler protocol over a scenario's lines for one source.

    Relative time anchors at the first sample() call — not construction —
    because the daemon may spend real ticks loading definitions before the
    monitor is first due; the scenario's t=0 must mean "first observation".
    """

    decl: ClassVar[SourceDecl]  # set per-instance below; ClassVar to satisfy protocol

    def __init__(self, scn: Scenario, source: str):
        self.decl = SOURCE_DECLS[source]
        self._lines = scn.samples.get(source, ())
        self._source = source
        self._t0: float | None = None

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        if self._t0 is None:
            self._t0 = now
        rel = now - self._t0
        current: dict | None = None
        for line in self._lines:  # last line with at <= rel is the state in force
            if line["at"] > rel:
                break
            current = line
        entities = tuple(
            EntitySample(
                entity_id=e["entity_id"],
                attrs=dict(e.get("attrs", {})),
                metrics={k: float(v) for k, v in e.get("metrics", {}).items()},
            )
            for e in (current["entities"] if current else ())
        )
        return Snapshot(source=self._source, ts=now, entities=entities)


def fixture_samplers(scn: Scenario) -> dict[str, FixtureSampler]:
    return {source: FixtureSampler(scn, source) for source in scn.sources()}


class FixtureEventSource:
    """EventSource protocol over a scenario's event lines (TS-04).

    The cursor is the index of the next undelivered line, as a string —
    minimal, but it exercises the exact DM-15 contract the e2e harness
    asserts: a restart with the persisted cursor must not replay delivered
    events and must not skip undelivered ones."""

    decl: ClassVar[SourceDecl] = SOURCE_DECLS["events"]

    def __init__(self, scn: Scenario):
        self._events = scn.events
        self._idx = 0
        self._t0: float | None = None
        self._alive = False

    def start(self, cursor: str | None) -> None:
        self._alive = True
        if cursor is not None:
            try:
                self._idx = min(int(cursor), len(self._events))
            except ValueError:
                self._idx = 0

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        if self._t0 is None:
            self._t0 = now
        rel = now - self._t0
        out: list[EventRecord] = []
        while (self._idx < len(self._events) and len(out) < max_items
               and self._events[self._idx]["at"] <= rel):
            line = self._events[self._idx]
            e = line["event"]
            out.append(EventRecord(
                ts=self._t0 + float(line["at"]),
                ingest_ts=now,
                source=str(e.get("source", "journald")),
                provider=str(e.get("provider", "unknown")),
                event_id=(str(e["event_id"]) if e.get("event_id") is not None
                          else None),
                severity=int(e.get("severity", 0)),
                message=str(e.get("message", "")),
            ))
            self._idx += 1
        return out, (str(self._idx) if out else None)

    def queue_depth(self) -> int:
        return 0  # nothing buffers: lines become due as sim time passes

    dropped = 0

    def alive(self) -> bool:
        return self._alive

    def stop(self) -> None:
        self._alive = False


# -- named scenario library (TS-04) -----------------------------------------
# Grows with the milestones; each name is used by at least one e2e test.


def _proc(at: float, *ents: tuple[str, str, float, float]) -> dict:
    return {
        "at": at,
        "source": "process",
        "entities": [
            {"entity_id": eid, "attrs": {"name": name},
             "metrics": {"rss_bytes": rss, "cpu_pct": cpu}}
            for eid, name, rss, cpu in ents
        ],
    }


def _steady() -> Scenario:
    return _build([_proc(0, ("calm:100:1", "calm", 50 * 2**20, 1.0))])


def _firefox_leak() -> Scenario:
    """firefox grows 2 MB/min for 30 min, then holds flat for 40 min —
    enough to open a leak incident and then recover it."""
    lines = []
    for minute in range(31):
        lines.append(_proc(minute * 60.0,
                           ("firefox:200:1", "firefox",
                            100 * 2**20 + minute * 2 * 2**20, 3.0)))
    lines.append(_proc(31 * 60.0, ("firefox:200:1", "firefox", 160 * 2**20, 3.0)))
    return _build(lines)


def _entity_vanishes() -> Scenario:
    """Leaks for 20 min, then the process exits: the incident must close
    with an entity-gone recovery (CA-08/IN-07)."""
    lines = []
    for minute in range(21):
        lines.append(_proc(minute * 60.0,
                           ("leaky:300:1", "leaky",
                            100 * 2**20 + minute * 2 * 2**20, 2.0)))
    lines.append({"at": 21 * 60.0, "source": "process", "entities": []})
    return _build(lines)


def _oom_burst() -> Scenario:
    """12 kernel OOM kills over 6 minutes, then silence: one episode that
    opens on the first, accumulates occurrences (cooldown-limited renotify),
    and quiet-clears 30 minutes after the last (IN-08). A steady process
    keeps the samplers busy so the scenario also runs under monitors that
    need a process source."""
    lines: list[dict] = [_proc(0, ("calm:100:1", "calm", 50 * 2**20, 1.0))]
    for i in range(12):
        lines.append({
            "at": 30.0 + i * 30.0,
            "event": {"source": "journald", "provider": "kernel", "severity": 3,
                      "message": f"Out of memory: Killed process {4000 + i}"
                                 " (chrome) total-vm:1024kB"},
        })
    return _build(lines)


def _disk_ladder() -> Scenario:
    """used_pct walks 80 -> 87 -> 98 -> 87 -> 70 on one mount: opens at
    warning, escalates to critical, silently downgrades back, then recovers —
    the full IN-03 ladder on a single shared incident. Five minutes per
    plateau gives two-rung confirm/clear counters (confirm 2 / clear 2 at a
    60s interval) room to run their course inside each plateau."""
    total = float(100 * 2**30)
    lines = []
    for minute, pct in ((0, 80.0), (5, 87.0), (10, 98.0), (15, 87.0), (20, 70.0)):
        used = total * pct / 100.0
        lines.append({
            "at": minute * 60.0,
            "source": "disk",
            "entities": [
                {"entity_id": "/",
                 "attrs": {"fstype": "ext4", "device": "/dev/sda1"},
                 "metrics": {"total_bytes": total, "used_bytes": used,
                             "free_bytes": total - used, "used_pct": pct}},
            ],
        })
    return _build(lines)


def _disk_filling_linear() -> Scenario:
    """One ext4 mount grows 1 GiB/min for 90 minutes (CA-09/TS-09).

    A point every monitor interval matters here: slope and monotonic confidence
    must be evaluated from the original observations, not sparse plateaus or
    display-downsampled history.
    """
    total = float(200 * 2**30)
    lines = []
    for minute in range(91):
        used = float((50 + minute) * 2**30)
        lines.append({
            "at": minute * 60.0,
            "source": "disk",
            "entities": [{
                "entity_id": "/data",
                "attrs": {"fstype": "ext4", "device": "/dev/sdb1"},
                "metrics": {
                    "total_bytes": total,
                    "used_bytes": used,
                    "free_bytes": total - used,
                    "used_pct": 100.0 * used / total,
                },
            }],
        })
    return _build(lines)


def _service_flap() -> Scenario:
    """present flips 1 -> 0 every 2 minutes for 20 minutes, then holds up:
    five quick open/clear cycles. The later re-opens land within IN-05's
    10-minute flap window of three prior clears, so they must arrive marked
    flapping and start at the slowest backoff tier."""
    lines = []
    for i in range(11):  # t = 0..20 min; even i is up — ends up and holds
        lines.append({
            "at": i * 120.0,
            "source": "unit",
            "entities": [
                {"entity_id": "unit:flappy.service",
                 "attrs": {"unit": "flappy.service", "kind": "unit"},
                 "metrics": {"present": 1.0 if i % 2 == 0 else 0.0,
                             "restarts": float(i // 2)}},
            ],
        })
    return _build(lines)


def _proc_churn() -> Scenario:
    """300 processes per minute for 20 minutes, 290 with fresh identities
    every minute (~5800 distinct ids): a busy build box. Nothing alerts —
    the point is RB-03/SA-05: persistence and rings must stay bounded by
    top-N selection, not grow with identity churn. Metrics vary by index
    (no RNG) so replays are bit-identical; the stable ten out-rank the
    churners on rss so the persisted set is mostly stable ids."""
    lines = []
    for minute in range(20):
        ents = [(f"stable{n}:{n}:1", f"stable{n}",
                 float((50 + n) * 2**20), 1.0 + n / 10)
                for n in range(10)]
        ents += [(f"churn{n}:{minute * 1000 + n}:1", f"churn{n}",
                  float((10 + n % 37) * 2**20), (n % 23) / 10)
                 for n in range(290)]
        lines.append(_proc(minute * 60.0, *ents))
    return _build(lines)


_LIBRARY = {
    "steady": _steady,
    "firefox-leak-2mb-min": _firefox_leak,
    "entity-vanishes-mid-incident": _entity_vanishes,
    "oom-event-burst": _oom_burst,
    "disk-ladder-updown": _disk_ladder,
    "disk-filling-linear": _disk_filling_linear,
    "service-flap": _service_flap,
    "proc-churn-300": _proc_churn,
}

SCENARIO_NAMES = tuple(sorted(_LIBRARY))


def scenario(name_or_path: str) -> Scenario:
    """Resolve a named library scenario, or load a JSONL file by path."""
    builder = _LIBRARY.get(name_or_path)
    if builder is not None:
        return builder()
    path = Path(name_or_path)
    if path.exists():
        return load_scenario(path)
    raise ValueError(
        f"unknown scenario {name_or_path!r}; names: {', '.join(SCENARIO_NAMES)}"
    )

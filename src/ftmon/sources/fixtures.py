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

from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

__all__ = [
    "Scenario",
    "FixtureSampler",
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


_LIBRARY = {
    "steady": _steady,
    "firefox-leak-2mb-min": _firefox_leak,
    "entity-vanishes-mid-incident": _entity_vanishes,
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

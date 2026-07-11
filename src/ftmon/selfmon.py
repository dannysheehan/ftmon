"""Self source: the daemon sampling itself (RB-02).

Exists so budget enforcement is a *monitor like any other* — rules in
self.toml, history in the DB, incidents through the normal engine — instead
of privileged special-case code. The daemon mutates one SelfStats object in
place; the sampler snapshots it. Counters accumulate monotonically so the
`rate()`/`delta()` calc functions work on them (they are declared "counter"
in SOURCE_DECLS).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import psutil

from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


@dataclass
class SelfStats:
    cycle_s: float = 0.0
    tick_overruns: int = 0
    event_queue_depth: int = 0
    events_dropped: int = 0
    events_unstored: int = 0
    ring_mem_bytes: int = 0
    source_activity_age_s: float = 0.0
    counters: dict[str, int] = field(default_factory=dict)

    def count(self, name: str) -> None:
        """Callback handed to expr eval / writer / rings as their counter."""
        self.counters[name] = self.counters.get(name, 0) + 1


class SelfSampler:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["self"]

    def __init__(self, stats: SelfStats, db_file: Path):
        self._stats = stats
        self._db_file = db_file
        self._proc = psutil.Process()

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        s = self._stats
        try:
            db_bytes = float(self._db_file.stat().st_size)
        except OSError:
            db_bytes = 0.0
        metrics: dict[str, float] = {
            "cpu_pct": float(self._proc.cpu_percent(None)),
            "rss_bytes": float(self._proc.memory_info().rss),
            "db_bytes": db_bytes,
            "cycle_s": s.cycle_s,
            "tick_overruns": float(s.tick_overruns),
            "event_queue_depth": float(s.event_queue_depth),
            "events_dropped": float(s.events_dropped),
            "events_unstored": float(s.events_unstored),
            "ring_mem_bytes": float(s.ring_mem_bytes),
            "source_activity_age_s": s.source_activity_age_s,
            "eval_unknown_total": float(s.counters.get("eval_unknown_total", 0)),
            "samples_rejected": float(s.counters.get("samples_rejected", 0)),
            "external_checks_skipped": float(
                s.counters.get("external_checks_skipped", 0)
            ),
            # Category suffixes remain available in SelfStats for diagnosis;
            # the persisted self entity exposes bounded totals so plugin output
            # cannot create an unbounded metric namespace.
            "external_check_failures": float(sum(
                value for name, value in s.counters.items()
                if name.startswith("external_check_failures:")
            )),
            "external_perfdata_rejected": float(sum(
                value for name, value in s.counters.items()
                if name.startswith("external_perfdata_rejected:")
            )),
        }
        entity = EntitySample(entity_id="ftmon", attrs={}, metrics=metrics)
        return Snapshot(source=self.decl.name, ts=now, entities=(entity,))

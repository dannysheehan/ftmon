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
    events_received: int = 0
    events_repeated: int = 0
    event_rate_per_min: float = 0.0
    events_unstored: int = 0
    ring_mem_bytes: int = 0
    source_activity_age_s: float = 0.0
    notify_pending_total: int = 0
    notify_due_claimable: int = 0
    notify_quiet_held: int = 0
    notify_failed: int = 0
    notify_oldest_claimable_due_age_s: float = 0.0
    notify_worker_alive: float = 1.0
    # DM-05 is defined on *used* pages, which only a database connection can
    # report; the sampler has none, so the daemon reads the page counts on its
    # own connection each tick. Zero until the first successful read.
    db_file_bytes: float = 0.0
    db_used_bytes: float = 0.0
    db_freelist_bytes: float = 0.0
    db_headroom_bytes: float = 0.0
    entities_persisted: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def count(self, name: str) -> None:
        """Callback handed to expr eval / writer / rings as their counter."""
        self.counters[name] = self.counters.get(name, 0) + 1


class SelfSampler:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["self"]

    def __init__(self, stats: SelfStats):
        self._stats = stats
        self._proc = psutil.Process()

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        s = self._stats
        metrics: dict[str, float] = {
            "cpu_pct": float(self._proc.cpu_percent(None)),
            "rss_bytes": float(self._proc.memory_info().rss),
            # Kept as file allocation, its historical meaning. Redefining a
            # persisted metric would put a step into every chart spanning the
            # upgrade that no database ever took (issue #104 decision D1);
            # DM-05 rules must reference db_used_bytes instead.
            "db_bytes": s.db_file_bytes,
            "db_file_bytes": s.db_file_bytes,
            "db_used_bytes": s.db_used_bytes,
            "db_freelist_bytes": s.db_freelist_bytes,
            # Signed: negative means over budget, which is the interesting case
            # and would be erased by clamping at zero.
            "db_headroom_bytes": s.db_headroom_bytes,
            "entities_persisted": float(s.entities_persisted),
            "cycle_s": s.cycle_s,
            "tick_overruns": float(s.tick_overruns),
            "event_queue_depth": float(s.event_queue_depth),
            "events_dropped": float(s.events_dropped),
            "events_received": float(s.events_received),
            "events_repeated": float(s.events_repeated),
            "event_rate_per_min": s.event_rate_per_min,
            "events_unstored": float(s.events_unstored),
            "ring_mem_bytes": float(s.ring_mem_bytes),
            "source_activity_age_s": s.source_activity_age_s,
            "notify_pending_total": float(s.notify_pending_total),
            "notify_due_claimable": float(s.notify_due_claimable),
            "notify_quiet_held": float(s.notify_quiet_held),
            # Bounded total rather than one series per channel: five extra
            # persisted series bill against the same DM-16 catalog budget, and
            # doctor/`/self` read the per-channel split straight from
            # notification_deliveries, which needs no series at all.
            "notify_failed": float(s.notify_failed),
            "notify_oldest_claimable_due_age_s": s.notify_oldest_claimable_due_age_s,
            "notify_worker_alive": s.notify_worker_alive,
            "notify_store_errors": float(s.counters.get("notify_store_errors", 0)),
            "eval_unknown_total": float(s.counters.get("eval_unknown_total", 0)),
            "samples_rejected": float(s.counters.get("samples_rejected", 0)),
            "sqlite_lock_errors": float(s.counters.get("sqlite_lock_errors", 0)),
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

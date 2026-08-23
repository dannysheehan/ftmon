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
from ftmon.sources.base import PIPELINE_PHASES, SAMPLER_SOURCE_NAMES, SOURCE_DECLS


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
    db_allocated_bytes: float = 0.0
    db_used_bytes: float = 0.0
    db_freelist_bytes: float = 0.0
    db_headroom_bytes: float = 0.0
    # Where the tick goes (RB-02, issue #106). Cumulative seconds, not
    # last-tick gauges: the self monitor samples every 60 s while ticks run
    # every 5 s, so a last-tick reading of a stage that only runs on some
    # ticks -- sampling, retention -- reports 0 almost always. Measured on the
    # canary: sampling and retention read zero in 10 of 10 samples while
    # commit, which every tick performs, read non-zero in 6.
    #
    # Counters make utilization the derived quantity it actually is:
    # delta(counter) / elapsed_wall gives the fraction of one core, correctly
    # even when a sample is missed. Fixed cardinality on purpose: the source
    # split uses seven compile-time names, never monitor/alias/runtime names,
    # for the same reason external_check_failures is a summed total.
    sampling_seconds_total: float = 0.0
    sampling_seconds_by_source: dict[str, float] = field(
        default_factory=lambda: {source: 0.0 for source in SAMPLER_SOURCE_NAMES}
    )
    pipeline_seconds_total: float = 0.0
    # Five compile-time phases, never a per-monitor dimension (#143). The blob
    # this splits includes persist, so "which phase grew" distinguishes an
    # in-memory walk from catalog/SQLite pressure.
    pipeline_seconds_by_phase: dict[str, float] = field(
        default_factory=lambda: {phase: 0.0 for phase in PIPELINE_PHASES}
    )
    commit_seconds_total: float = 0.0
    actions_outbox_seconds_total: float = 0.0
    retention_seconds_total: float = 0.0
    prune_seconds_total: float = 0.0
    reap_seconds_total: float = 0.0
    # 1 when the most recent retention pass had to degrade (DM-05).
    db_degrading: float = 0.0
    # None until a tick has run: publishing 0 on the first tick after a
    # restart would look like a measurement of nothing persisted.
    entities_persisted: int | None = None
    series_persisted: int | None = None
    promotion_limited_monitors: int | None = None
    promotion_rejections_total: int = 0
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
            # db_bytes is stat() of the main file — exactly what this metric
            # measured before #104, so its stored history stays continuous
            # (decision D1). db_allocated_bytes is SQLite's logical size.
            # These are NOT the same in WAL mode: the main file lags logical
            # allocation between checkpoints (measured ~1 MB on a live FTMON
            # database), which is why the earlier attempt to serve both from
            # one value broke the very continuity D1 promised.
            # Neither is the budget signal: DM-05 rules use db_used_bytes.
            "db_bytes": s.db_file_bytes,
            "db_allocated_bytes": s.db_allocated_bytes,
            "db_used_bytes": s.db_used_bytes,
            "db_freelist_bytes": s.db_freelist_bytes,
            # Signed: negative means over budget, which is the interesting case
            # and would be erased by clamping at zero.
            "db_headroom_bytes": s.db_headroom_bytes,
            "db_degrading": s.db_degrading,
            "db_degradations": float(s.counters.get("db_degradations", 0)),
            "cycle_s": s.cycle_s,
            # Stage breakdown of cycle_s (#106). cycle_s alone says the tick
            # cost 240 ms without saying whether sampling, evaluation, the
            # commit or retention explains it -- the question #107's scope
            # turns on, and one the disposable spike profiler could only
            # answer against a clone.
            #
            # sampling covers *every* SA-06 shared sample, not just the
            # process source: restricting it to `process` would push disk,
            # net, unit and self sampling into the pipeline counter,
            # replacing one mislabelled metric with another. External check
            # *preparation* runs before the monitor loop and is outside both
            # -- it is bounded separately by EC-02 deadlines.
            #
            # prune and reap are subcomponents of retention, never additive
            # peers: summing all seven would double-count the retention pass.
            "sampling_seconds_total": s.sampling_seconds_total,
            **{
                f"sampling_{source}_seconds_total":
                    s.sampling_seconds_by_source.get(source, 0.0)
                for source in SAMPLER_SOURCE_NAMES
            },
            "pipeline_seconds_total": s.pipeline_seconds_total,
            **{
                f"pipeline_{phase}_seconds_total":
                    s.pipeline_seconds_by_phase.get(phase, 0.0)
                for phase in PIPELINE_PHASES
            },
            "commit_seconds_total": s.commit_seconds_total,
            "actions_outbox_seconds_total": s.actions_outbox_seconds_total,
            "retention_seconds_total": s.retention_seconds_total,
            "prune_seconds_total": s.prune_seconds_total,
            "reap_seconds_total": s.reap_seconds_total,
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
        # Omitted rather than zeroed while unknown: EX-06 makes a missing
        # metric UNKNOWN, which is what "no tick has run yet" means.
        if s.entities_persisted is not None:
            metrics["entities_persisted"] = float(s.entities_persisted)
        if s.series_persisted is not None:
            metrics["series_persisted"] = float(s.series_persisted)
        if s.promotion_limited_monitors is not None:
            metrics["promotion_limited_monitors"] = float(s.promotion_limited_monitors)
        metrics["promotion_rejections_total"] = float(s.promotion_rejections_total)
        entity = EntitySample(entity_id="ftmon", attrs={}, metrics=metrics)
        return Snapshot(source=self.decl.name, ts=now, entities=(entity,))

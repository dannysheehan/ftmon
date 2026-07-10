"""Daemon composition root: wires clock, definitions, samplers, pipeline,
scheduler, and the store's single bulk writer (DESIGN section 2).

Deliberately thin: every behavior with rules attached lives in a tested
component; this module only assembles them and owns process-level concerns —
the single-instance lock (PM-02), signal handling, the 30 s definition
rescan (PM-04), and draining pipeline self-events into the tick transaction.

M1 scope: samples, evaluates, persists. Incidents/notifications (M2) and
event sources (M3) plug into on_tick later; their absence here is by
milestone plan, not oversight.
"""

from __future__ import annotations

import fcntl
import sys
from dataclasses import dataclass, field

from ftmon import definitions
from ftmon.clock import Clock, ControlledClock, SystemClock
from ftmon.definitions.loader import MonitorDef
from ftmon.engine.pipeline import Pipeline
from ftmon.engine.rings import RingStore
from ftmon.engine.scheduler import DueTable, Scheduler
from ftmon.paths import Paths, get_paths
from ftmon.selfmon import SelfSampler, SelfStats
from ftmon.sources.disk import DiskSampler
from ftmon.sources.process import ProcessSampler
from ftmon.sources.system import SystemSampler
from ftmon.store import db as store_db
from ftmon.store.writer import TickWriter

_RESCAN_EVERY_S = 30.0  # PM-04


@dataclass
class DaemonCore:
    """Testable core: everything except process-level concerns. The e2e
    harness (M2) drives this with a ControlledClock; production run() wraps
    it with lock/signals."""

    paths: Paths
    clock: Clock
    monitors: dict[str, MonitorDef] = field(default_factory=dict)
    stop: bool = False

    def __post_init__(self) -> None:
        self.stats = SelfStats()
        self.conn = store_db.connect(self.paths.db_file)
        store_db.migrate(self.conn)
        self.writer = TickWriter(self.conn, on_reject=lambda _n: self.stats.count(
            "samples_rejected"))
        self.rings = RingStore()
        self.samplers = {
            "process": ProcessSampler(self.clock),
            "disk": DiskSampler(self.clock),
            "system": SystemSampler(self.clock),
            "self": SelfSampler(self.stats, self.paths.db_file),
        }
        self.pipeline = Pipeline(self.samplers, self.rings, self.stats.count)
        self.due = DueTable()
        self._last_rescan = -_RESCAN_EVERY_S
        self._load_definitions(initial=True)

    def _load_definitions(self, initial: bool = False) -> None:
        """PM-04: apply adds/changes/removes; an invalid file keeps the
        currently loaded version (or stays unloaded after restart)."""
        defs, errors = definitions.load_dir(self.paths.monitors_dir)
        now = self.clock.now()
        for path, err in errors:
            # Surfaced as a self-event so status/CLI can report it; the
            # daemon itself must keep running (PM-04).
            print(f"config_error: {path}: {err}", file=sys.stderr)
            self.stats.count("config_errors")
        seen = set()
        for mdef in defs:
            seen.add(mdef.name)
            if mdef.source not in self.samplers:
                if initial:
                    print(
                        f"monitor {mdef.name}: source {mdef.source!r} not available "
                        "in this milestone; skipped",
                        file=sys.stderr,
                    )
                continue
            current = self.monitors.get(mdef.name)
            if current is not None and current.content_hash == mdef.content_hash:
                continue
            windows: dict[str, float] = {}
            for metric, w in mdef.windows:
                windows[metric] = max(w, windows.get(metric, 0.0))
            self.rings.configure(mdef.name, mdef.interval_s, windows)
            self.monitors[mdef.name] = mdef
            self.due.add(mdef.name, mdef.interval_s, self.clock.monotonic())
            self.writer.record_monitor_load(mdef.name, now, mdef.content_hash,
                                            mdef.normalized_toml)
        for name in [n for n in self.monitors if n not in seen]:
            del self.monitors[name]
            self.due.remove(name)
            self.rings.forget_monitor(name)

    def on_tick(self, wall: float, mono: float, gap_s: float) -> None:
        started = self.clock.monotonic()
        if gap_s:
            self.stats.count("clock_gaps")
        if mono - self._last_rescan >= _RESCAN_EVERY_S:
            self._last_rescan = mono
            self._load_definitions()
        cache: dict = {}
        for name in self.due.due(mono, lambda _n: self._overrun()):
            mdef = self.monitors.get(name)
            if mdef is None:
                continue
            # SA-02: sampler budget of 10s inside the 5s-tick world means an
            # overrunning monitor skips slots rather than queueing (SA-01).
            self.pipeline.run_monitor(mdef, wall, mono + 10.0, self.writer, cache)
        for ev in self.pipeline.drain_self_events():
            self.writer.add_event(ev)
        self.stats.ring_mem_bytes = self.rings.mem_bytes()
        self.rings.evict_if_over(self._is_protected, self.stats.count)
        self.writer.set_meta("last_tick_ts", repr(wall))
        self.writer.commit_tick()
        self.stats.cycle_s = self.clock.monotonic() - started

    def _overrun(self) -> None:
        self.stats.tick_overruns += 1

    def _is_protected(self, monitor: str, entity_id: str) -> bool:
        return entity_id in self.pipeline.promoted(monitor)

    def run_loop(self, tick_s: float = 5.0) -> None:
        Scheduler(self.clock, tick_s).run(self.on_tick, lambda: self.stop)


def run(args) -> int:
    """Entry point for `ftmon daemon` (PM-02 single instance, signals)."""
    import signal

    paths = get_paths()
    paths.ensure()

    lock_file = open(paths.lock_file, "w")  # noqa: SIM115 - held for process lifetime
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ftmon daemon already running (lock held); exiting", file=sys.stderr)
        return 1

    clock: Clock
    if getattr(args, "clock", "system") == "controlled":
        clock = ControlledClock()  # test harness drives via FTMON_CLOCK_SOCK (TS-05)
    else:
        clock = SystemClock()

    core = DaemonCore(paths=paths, clock=clock)

    def _stop(_sig, _frame):
        core.stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    tick_s = 5.0
    print(f"ftmon daemon started ({len(core.monitors)} monitors)", file=sys.stderr)
    core.run_loop(tick_s)
    print("ftmon daemon stopped", file=sys.stderr)
    return 0

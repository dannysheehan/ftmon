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
from ftmon.config import AppConfig, load_config
from ftmon.definitions.loader import MonitorDef
from ftmon.engine import incidents as inc
from ftmon.engine.effects import EffectExecutor
from ftmon.engine.events import EventEngine
from ftmon.engine.pipeline import EvalOutcome, Pipeline
from ftmon.engine.rings import RingStore
from ftmon.engine.scheduler import DueTable, Scheduler
from ftmon.model import EventRecord, GroupState, IncidentCore, RungState
from ftmon.notify import FileNotifier
from ftmon.notify.base import Notifier
from ftmon.paths import Paths, get_paths
from ftmon.selfmon import SelfSampler, SelfStats
from ftmon.sources.base import EventSource
from ftmon.sources.disk import DiskSampler
from ftmon.sources.net import NetSampler
from ftmon.sources.process import ProcessSampler
from ftmon.sources.system import SystemSampler
from ftmon.sources.unit import UnitSampler
from ftmon.store import db as store_db
from ftmon.store.outbox import Outbox
from ftmon.store.retention import BaselineLookup, Retention
from ftmon.store.writer import TickWriter

_RESCAN_EVERY_S = 30.0  # PM-04
_RETENTION_EVERY_S = 60.0  # DM-04: incremental; a minute cadence keeps passes tiny

IncidentKey = tuple[str, str, str]  # (monitor, entity_id, group)


@dataclass
class DaemonCore:
    """Testable core: everything except process-level concerns. The e2e
    harness (M2) drives this with a ControlledClock; production run() wraps
    it with lock/signals."""

    paths: Paths
    clock: Clock
    monitors: dict[str, MonitorDef] = field(default_factory=dict)
    notifiers: list[Notifier] | None = None
    config: AppConfig | None = None  # None = load from paths.config_file
    # None = no event pipeline (most unit tests). Production run() passes
    # JournaldEventSource; --fixtures passes FixtureEventSource. Injected
    # rather than built here so DaemonCore never spawns journalctl in tests.
    event_source: EventSource | None = None
    stop: bool = False

    def __post_init__(self) -> None:
        self.stats = SelfStats()
        if self.config is None:
            self.config, config_warnings = load_config(self.paths.config_file)
            for w in config_warnings:
                print(f"config warning: {w}", file=sys.stderr)
        self.conn = store_db.connect(self.paths.db_file)
        store_db.migrate(self.conn)
        self.writer = TickWriter(self.conn, on_reject=lambda _n: self.stats.count(
            "samples_rejected"))
        self.rings = RingStore()
        self.samplers = {
            "process": ProcessSampler(self.clock),
            "disk": DiskSampler(self.clock),
            "system": SystemSampler(self.clock),
            "unit": UnitSampler(self.clock),
            "net": NetSampler(self.clock),
            "self": SelfSampler(self.stats, self.paths.db_file),
        }
        # Rollups/retention/baselines (DM-04/05, CA-05) run in-daemon; the
        # lookup is handed to the pipeline so baseline() in rules reads the
        # learned values, invalidated whenever a retention pass writes.
        self.retention = Retention(self.conn)
        self.baselines = BaselineLookup(self.conn)
        self._last_retention = -_RETENTION_EVERY_S
        self.pipeline = Pipeline(self.samplers, self.rings, self.stats.count,
                                 baseline_lookup=self.baselines)
        self.due = DueTable()
        # Incident machinery (M2): pure engine + executor + outbox delivery.
        # The file notifier is unconditional (NO-02: it is the audit trail);
        # production run() appends the desktop channel.
        self.executor = EffectExecutor(self.writer)
        self.outbox = Outbox(
            self.conn,
            self.notifiers if self.notifiers is not None
            else [FileNotifier(self.paths.notifications_file)],
            quiet=self.config.quiet,  # NO-03: delivery-side only
        )
        self._istates: dict[IncidentKey, GroupState] = {}
        self._group_rungs: dict[tuple[str, str], tuple[inc.RungConfig, ...]] = {}
        self._last_rescan = -_RESCAN_EVERY_S
        # Event pipeline (M3): engine exists iff a source was injected;
        # started lazily once an events-source monitor is actually loaded.
        self.event_monitors: dict[str, MonitorDef] = {}
        self.events_engine = (
            EventEngine(source=self.event_source, executor=self.executor,
                        counter=self.stats.count)
            if self.event_source is not None else None
        )
        self._load_definitions(initial=True)
        self._rebuild_incidents()
        if self.events_engine is not None and self.event_monitors:
            self._start_events()
        self.outbox.recover(self.clock.now())  # NO-04 startup pass

    def _start_events(self) -> None:
        """DM-15: resume from the persisted cursor; rebuild open episodes so
        a restart cannot re-open (and re-notify) a live one."""
        assert self.events_engine is not None
        row = self.conn.execute(
            "SELECT cursor FROM cursors WHERE source = ?",
            (self.events_engine.cursor_name,),
        ).fetchone()
        self.events_engine.start(row["cursor"] if row else None)
        rows = self.conn.execute(
            "SELECT * FROM incidents WHERE state IN ('open', 'acked')"
        ).fetchall()
        self.events_engine.rebuild(rows, list(self.event_monitors.values()))

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
            if mdef.source == "events":
                # Event monitors have no sampler/rings/schedule: the event
                # engine consumes them every tick against the live stream.
                current = self.event_monitors.get(mdef.name)
                if current is not None and current.content_hash == mdef.content_hash:
                    continue
                if current is not None and self.events_engine is not None:
                    self.events_engine.supersede(mdef.name, now)  # MD-06
                self.event_monitors[mdef.name] = mdef
                self.writer.record_monitor_load(mdef.name, now, mdef.content_hash,
                                                mdef.normalized_toml)
                continue
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
            if current is not None:
                # MD-06: a changed definition never inherits confirmation
                # progress or open incidents from its previous self.
                self._supersede_monitor(mdef.name, now)
            windows: dict[str, float] = {}
            for metric, w in mdef.windows:
                windows[metric] = max(w, windows.get(metric, 0.0))
            self.rings.configure(mdef.name, mdef.interval_s, windows)
            self.monitors[mdef.name] = mdef
            self._index_groups(mdef)
            self.due.add(mdef.name, mdef.interval_s, self.clock.monotonic())
            self.writer.record_monitor_load(mdef.name, now, mdef.content_hash,
                                            mdef.normalized_toml)
        for name in [n for n in self.monitors if n not in seen]:
            self._supersede_monitor(name, now)  # MD-09
            del self.monitors[name]
            self.due.remove(name)
            self.rings.forget_monitor(name)
        for name in [n for n in self.event_monitors if n not in seen]:
            if self.events_engine is not None:
                self.events_engine.supersede(name, now)  # MD-09
            del self.event_monitors[name]

    def _index_groups(self, mdef: MonitorDef) -> None:
        """Rung configs per (monitor, group), severity-descending — the
        order the incident engine's ownership rule depends on (IN-03)."""
        by_group: dict[str, list[inc.RungConfig]] = {}
        for rule in mdef.rules:
            by_group.setdefault(rule.group, []).append(
                inc.RungConfig(
                    rule_id=rule.id,
                    severity=rule.severity,
                    confirm_cycles=rule.confirm_cycles,
                    clear_cycles=rule.clear_cycles,
                    action=rule.action,
                    notify_recovery=rule.notify_recovery,
                )
            )
        for key in [k for k in self._group_rungs if k[0] == mdef.name]:
            del self._group_rungs[key]
        for group, rungs in by_group.items():
            rungs.sort(key=lambda r: -r.severity)
            self._group_rungs[(mdef.name, group)] = tuple(rungs)

    def _group_cfg(self, monitor: str, entity_id: str, group: str) -> inc.GroupConfig | None:
        rungs = self._group_rungs.get((monitor, group))
        if rungs is None:
            return None
        return inc.GroupConfig(monitor=monitor, entity_id=entity_id, group=group, rungs=rungs)

    def _supersede_monitor(self, monitor: str, now: float) -> None:
        for key in [k for k in self._istates if k[0] == monitor]:
            cfg = self._group_cfg(*key)
            if cfg is None:
                self._istates.pop(key)
                continue
            st, effects = inc.clear_superseded(cfg, self._istates[key], now)
            if effects:
                st = self.executor.apply(cfg, st, effects, now)
            self._istates.pop(key)

    def _rebuild_incidents(self) -> None:
        """Restart continuity (IN-02/DM-14): reload open/acked incidents so
        backoff schedules survive. The owning rung is marked confirmed —
        conservative: a genuinely recovered condition still needs its
        clear_cycles of FALSE to close, but an incident can never evaporate
        just because the daemon restarted. Confirm counters themselves are
        memory-only by design (DESIGN D3)."""
        rows = self.conn.execute(
            "SELECT * FROM incidents WHERE state IN ('open', 'acked')"
        ).fetchall()
        for row in rows:
            if row["monitor"] in self.event_monitors:
                continue  # episode incidents rebuild in _start_events (IN-08)
            key = (row["monitor"], row["entity_id"], row["grp"])
            cfg = self._group_cfg(*key)
            now = self.clock.now()
            if cfg is None:
                # Monitor/group no longer exists on disk: MD-09 supersede.
                self.writer.upsert_incident(
                    row["id"], row["monitor"], row["grp"], row["entity_id"],
                    state="cleared", severity=row["severity"],
                    owning_rule=row["owning_rule"], opened_ts=row["opened_ts"],
                    last_change_ts=now, cleared_ts=now, clear_reason="superseded",
                    ack_by=row["ack_by"], ack_ts=row["ack_ts"],
                    notify_count=row["notify_count"], occurrences=row["occurrences"],
                    flapping=bool(row["flapping"]),
                )
                continue
            last_notify = self.conn.execute(
                "SELECT MAX(created_ts) FROM outbox WHERE incident_id = ?", (row["id"],)
            ).fetchone()[0]
            rungs = {r.rule_id: RungState() for r in cfg.rungs}
            owner = next((r for r in cfg.rungs if r.rule_id == row["owning_rule"]),
                         cfg.rungs[0])
            rungs[owner.rule_id] = RungState(confirmed=True,
                                             confirm_count=owner.confirm_cycles)
            core = IncidentCore(
                incident_id=row["id"],
                state=row["state"],
                severity=row["severity"],
                owning_rule=owner.rule_id,
                opened_ts=row["opened_ts"],
                last_notify_ts=float(last_notify) if last_notify else row["opened_ts"],
                notify_count=row["notify_count"],
                backoff_tier=(len(inc.BACKOFF_S) - 1 if row["flapping"]
                              else min(max(row["notify_count"] - 1, 0),
                                       len(inc.BACKOFF_S) - 1)),
                flap_clears=(),
                occurrences=row["occurrences"],
            )
            self._istates[key] = GroupState(rungs=rungs, core=core)

    def _refresh_acks(self) -> None:
        """Acks land in the DB from CLI/MCP/web (PM-03 small writes); the
        engine only needs the flag flipped on its in-memory core."""
        from dataclasses import replace

        acked = {row["id"] for row in self.conn.execute(
            "SELECT id FROM incidents WHERE state = 'acked'"
        ).fetchall()}
        for key, st in self._istates.items():
            core = st.core
            if core and core.state == "open" and core.incident_id in acked:
                self._istates[key] = GroupState(
                    rungs=st.rungs, core=replace(core, state="acked")
                )
        if self.events_engine is not None:
            self.events_engine.refresh_acks(acked)

    def on_tick(self, wall: float, mono: float, gap_s: float) -> None:
        started = self.clock.monotonic()
        if gap_s:
            self.stats.count("clock_gaps")
        if mono - self._last_rescan >= _RESCAN_EVERY_S:
            self._last_rescan = mono
            self._load_definitions()
            self._refresh_acks()
        cache: dict = {}
        outcomes: list[EvalOutcome] = []
        for name in self.due.due(mono, lambda _n: self._overrun()):
            mdef = self.monitors.get(name)
            if mdef is None:
                continue
            # SA-02: sampler budget of 10s inside the 5s-tick world means an
            # overrunning monitor skips slots rather than queueing (SA-01).
            outcomes.extend(
                self.pipeline.run_monitor(mdef, wall, mono + 10.0, self.writer, cache)
            )
        self._step_incidents(outcomes, wall)
        if self.events_engine is not None and self.event_monitors:
            if not self.events_engine._started:
                self._start_events()  # an events monitor appeared on rescan
            self.events_engine.tick(list(self.event_monitors.values()), wall,
                                    mono, self.writer)
            self.stats.event_queue_depth = self.events_engine.queue_depth
            self.stats.events_dropped = self.events_engine.dropped
            self.stats.source_activity_age_s = self.events_engine.last_activity_age_s
        for monitor, entity_id in self.pipeline.drain_gone():
            self._clear_gone(monitor, entity_id, wall)
        for ev in self.pipeline.drain_self_events():
            self.writer.add_event(ev)
        self.stats.ring_mem_bytes = self.rings.mem_bytes()
        self.rings.evict_if_over(self._is_protected, self.stats.count)
        self.writer.set_meta("last_tick_ts", repr(wall))
        self.writer.commit_tick()
        # NO-04: delivery strictly after the transition committed.
        self.outbox.flush(wall)
        if mono - self._last_retention >= _RETENTION_EVERY_S:
            self._last_retention = mono
            self._run_retention(wall)
        self.stats.cycle_s = self.clock.monotonic() - started

    def _run_retention(self, wall: float) -> None:
        """Rollups + pruning + baselines (DM-04/05, CA-05), its own bounded
        transaction after the tick commit. DM-05 degradation steps become
        self-events; the events buffer flushes with the next tick's commit."""
        notes = self.retention.run(wall)
        if self.retention.baselines_updated:
            self.baselines.invalidate()
        for note in notes:
            self.stats.count("db_degradations")
            self.writer.add_event(EventRecord(
                ts=wall, ingest_ts=wall, source="self", provider="ftmon.retention",
                event_id=None, severity=1, message=note,
            ))

    def _step_incidents(self, outcomes: list[EvalOutcome], wall: float) -> None:
        grouped: dict[IncidentKey, dict[str, inc.RungEval]] = {}
        for o in outcomes:
            grouped.setdefault((o.monitor, o.entity_id, o.group), {})[o.rule_id] = (
                inc.RungEval(o.result, o.message)
            )
        for key, evals in grouped.items():
            cfg = self._group_cfg(*key)
            if cfg is None:
                continue
            st = self._istates.get(key) or inc.empty_state(cfg)
            st, effects = inc.step_group(cfg, st, evals, wall)
            if effects:
                st = self.executor.apply(cfg, st, effects, wall)
            self._istates[key] = st

    def _clear_gone(self, monitor: str, entity_id: str, wall: float) -> None:
        for key in [k for k in self._istates if k[0] == monitor and k[1] == entity_id]:
            cfg = self._group_cfg(*key)
            if cfg is None:
                continue
            st, effects = inc.clear_for_entity_gone(cfg, self._istates[key], wall)
            if effects:
                st = self.executor.apply(cfg, st, effects, wall)
            self._istates[key] = st

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

    # Production channels (NO-02): audit file always; desktop popups when a
    # notification daemon is reachable. DaemonCore tests run file-only.
    from ftmon.notify import DesktopNotifier

    # Event source before core construction: DaemonCore starts the event
    # engine (cursor resume, episode rebuild) inside __post_init__.
    scn = None
    if getattr(args, "fixtures", None):
        from ftmon.sources import fixtures

        scn = fixtures.scenario(args.fixtures)
        event_source: EventSource | None = (
            fixtures.FixtureEventSource(scn) if scn.events else None)
    else:
        from ftmon.sources.journald import JournaldEventSource

        event_source = JournaldEventSource()

    core = DaemonCore(
        paths=paths,
        clock=clock,
        notifiers=[FileNotifier(paths.notifications_file), DesktopNotifier()],
        event_source=event_source,
    )

    if scn is not None:
        # TS-04/TS-05: replace live samplers with scenario replay. In-place
        # update — the pipeline holds a reference to this same dict.
        from ftmon.sources import fixtures

        core.samplers.update(fixtures.fixture_samplers(scn))
        print(f"fixtures: {args.fixtures} ({', '.join(sorted(scn.sources()))})",
              file=sys.stderr)

    def _stop(_sig, _frame):
        core.stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    tick_s = core.config.tick_seconds if core.config else 5.0
    total = len(core.monitors) + len(core.event_monitors)
    print(f"ftmon daemon started ({total} monitors)", file=sys.stderr)
    core.run_loop(tick_s)
    if core.events_engine is not None:
        core.events_engine.stop()  # reap the journalctl reader
    print("ftmon daemon stopped", file=sys.stderr)
    return 0

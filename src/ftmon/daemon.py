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
import sqlite3
import sys
from dataclasses import dataclass, field
from queue import SimpleQueue

from ftmon import definitions
from ftmon.checks import CheckRunner, ExternalSampler
from ftmon.checks.registry import RegistryError
from ftmon.checks.registry import empty as empty_registry
from ftmon.checks.registry import load as load_check_registry
from ftmon.clock import Clock, ControlledClock, SystemClock
from ftmon.config import AppConfig, QuietHours, load_config
from ftmon.definitions.loader import MonitorDef
from ftmon.engine import incidents as inc
from ftmon.engine.actions import ActionRunner
from ftmon.engine.effects import EffectExecutor
from ftmon.engine.events import EventEngine
from ftmon.engine.pipeline import EvalOutcome, Pipeline
from ftmon.engine.rings import RingStore
from ftmon.engine.scheduler import DueTable, Scheduler
from ftmon.model import EventRecord, GroupState, IncidentCore, RungState
from ftmon.notify import FileNotifier, NtfyNotifier, SmtpNotifier, WebhookNotifier
from ftmon.notify.base import DeliveryError, Notifier
from ftmon.paths import Paths, get_paths
from ftmon.selfmon import SelfSampler, SelfStats
from ftmon.sources.base import EventSource
from ftmon.sources.disk import DiskSampler
from ftmon.sources.net import NetSampler
from ftmon.sources.process import ProcessSampler
from ftmon.sources.system import SystemSampler
from ftmon.sources.unit import UnitSampler
from ftmon.store import db as store_db
from ftmon.store.outbox import DispatchWorker, Outbox
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
    background_dispatch: bool = False
    stop: bool = False

    def __post_init__(self) -> None:
        self.stats = SelfStats()
        self._delivery_failures: SimpleQueue[tuple[str, str, float]] = SimpleQueue()
        self._reload_global_config = self.config is None
        if self._reload_global_config:
            self.config, config_warnings = load_config(self.paths.config_file)
            for w in config_warnings:
                print(f"config warning: {w}", file=sys.stderr)
        self._config_stamp = self._config_file_stamp()
        self._notifier_override = tuple(self.notifiers) if self.notifiers is not None else None
        self.conn = store_db.connect(self.paths.db_file)
        store_db.migrate(self.conn)
        self.writer = TickWriter(self.conn, on_reject=lambda _n: self.stats.count(
            "samples_rejected"))
        self.rings = RingStore()
        self.check_registry = empty_registry()
        self._registry_stamp: tuple[int, int] | None = None
        self.external_sampler = ExternalSampler(
            self.check_registry,
            # Deadline arithmetic must share the scheduler's monotonic clock;
            # controlled-clock tests intentionally use a different epoch from
            # the host monotonic clock.
            CheckRunner(self.paths.state_dir, self.clock),
            self.stats.count,
            self.clock,
        )
        self._reload_check_registry(initial=True)
        self.samplers = {
            "process": ProcessSampler(self.clock),
            "disk": DiskSampler(self.clock),
            "system": SystemSampler(self.clock),
            "unit": UnitSampler(self.clock),
            "net": NetSampler(self.clock),
            "self": SelfSampler(self.stats, self.paths.db_file),
            "external": self.external_sampler,
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
        # Tests can inject exact channels. Production derives desktop delivery
        # from explicit config so the server profile cannot accidentally pop up
        # through a lingering graphical session (PM-08); file remains mandatory.
        self.executor = EffectExecutor(self.writer)
        self.actions = ActionRunner(self.conn, self.paths)
        notifiers = self._build_notifiers(self.config)
        available = {notifier.name for notifier in notifiers}
        self.writer.set_delivery_channels({
            name: channel.min_severity
            for name, channel in self.config.channels
            if channel.enabled and name in available
        })
        self.outbox = self._new_outbox(notifiers, self.config.quiet)
        self._istates: dict[IncidentKey, GroupState] = {}
        self._group_rungs: dict[tuple[str, str], tuple[inc.RungConfig, ...]] = {}
        self._last_rescan = -_RESCAN_EVERY_S
        self._reload_requested = False
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
        self.dispatch_worker: DispatchWorker | None = None
        if self.background_dispatch:
            # Network adapters run only on this worker connection; sampling
            # never waits for their ten-second timeout (DESIGN 10.7).
            self.outbox.reset_inflight()
            self.dispatch_worker = DispatchWorker(
                self.paths.db_file, notifiers, self.clock.now,
                quiet=self.config.quiet, on_terminal=self._record_delivery_failure,
            )
            self.dispatch_worker.start()
        else:
            self.outbox.recover(self.clock.now())  # deterministic tests

    def _build_notifiers(self, config: AppConfig) -> list[Notifier]:
        """Construct only validated channels; one bad remote stays isolated."""
        if self._notifier_override is not None:
            return list(self._notifier_override)
        notifiers: list[Notifier] = [FileNotifier(self.paths.notifications_file)]
        desktop = config.channel("desktop")
        if desktop is not None and desktop.enabled:
            from ftmon.notify import DesktopNotifier

            desktop_notifier = DesktopNotifier()
            if desktop_notifier.available:
                notifiers.append(desktop_notifier)
            else:
                print("config warning: [notify.desktop] desktop_unavailable; "
                      "channel disabled", file=sys.stderr)
                self.stats.count("config_errors")
        remote_types = {
            "ntfy": NtfyNotifier,
            "webhook": WebhookNotifier,
            "smtp": SmtpNotifier,
        }
        for name, notifier_type in remote_types.items():
            channel = config.channel(name)
            if channel is None or not channel.enabled:
                continue
            try:
                notifiers.append(notifier_type(channel))
            except DeliveryError as exc:
                # Loading normally catches readiness first. Constructor failure
                # remains isolated if a secret rotates between validation/use.
                print(f"config warning: [notify.{name}] {exc}; channel disabled",
                      file=sys.stderr)
                self.stats.count("config_errors")
        return notifiers

    def _new_outbox(
        self, notifiers: list[Notifier], quiet: QuietHours | None
    ) -> Outbox:
        return Outbox(
            self.conn, notifiers, quiet=quiet,
            # Terminal remote failures become ordinary self-events on the next
            # tick, avoiding a recursive notification failure loop.
            on_terminal=self._record_delivery_failure,
        )

    def _config_file_stamp(self) -> tuple[int, int, int] | None:
        try:
            info = self.paths.config_file.stat()
        except OSError:
            return None
        # Atomic replacement can preserve timestamp/size; inode closes that
        # otherwise-real missed-reload window without hashing every 5-second tick.
        return info.st_ino, info.st_mtime_ns, info.st_size

    def _check_registry_stamp(self) -> tuple[int, int, int] | None:
        try:
            info = self.paths.check_registry_file.stat()
        except OSError:
            return None
        return info.st_ino, info.st_mtime_ns, info.st_size

    def _reload_check_registry(self, *, initial: bool = False) -> None:
        """Atomically publish only a complete administrator authority file."""
        stamp = self._check_registry_stamp()
        if not initial and stamp == self._registry_stamp:
            return
        if stamp is None:
            # A missing default registry means no external authority. If a
            # previously valid file disappears, retain it until a valid
            # replacement arrives, matching EC-06's atomic reload contract.
            return
        try:
            registry = load_check_registry(self.paths.check_registry_file, paths=self.paths)
        except RegistryError as exc:
            print(f"config_error: checks.toml: {exc.category}", file=sys.stderr)
            self.stats.count("config_errors")
            if not initial:
                self.writer.add_event(EventRecord(
                    ts=self.clock.now(), ingest_ts=self.clock.now(), source="self",
                    provider="ftmon.config", event_id=None, severity=2,
                    message=f"external check registry rejected: {exc.category}",
                ))
            return
        self._registry_stamp = stamp
        self.check_registry = registry
        self.external_sampler.set_registry(registry)

    def _reload_channels(self) -> None:
        """NO-10: apply changed channel config at a delivery-attempt boundary."""
        if not self._reload_global_config:
            return
        stamp = self._config_file_stamp()
        if stamp == self._config_stamp:
            return
        self._config_stamp = stamp
        if stamp is None:
            print("config warning: config.toml removed; keeping loaded channels",
                  file=sys.stderr)
            return
        config, warnings = load_config(self.paths.config_file)
        for warning in warnings:
            print(f"config warning: {warning}", file=sys.stderr)
        if any(warning.startswith("config.toml unreadable") for warning in warnings):
            # A half-written/manual syntax error must not replace working remote
            # delivery with desktop defaults. Atomic writers avoid this, but
            # keeping the last good snapshot makes hand edits safe too.
            return
        notifiers = self._build_notifiers(config)
        available = {notifier.name for notifier in notifiers}
        if self.dispatch_worker is not None:
            self.dispatch_worker.reconfigure(notifiers, config.quiet)
        self.writer.set_delivery_channels({
            name: channel.min_severity
            for name, channel in config.channels
            if channel.enabled and name in available
        })
        self.outbox = self._new_outbox(notifiers, config.quiet)
        self.config = config

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
        defs, errors = definitions.load_dir(
            self.paths.monitors_dir,
            actions_dir=self.paths.actions_dir,
            require_actions=True,
            check_aliases=frozenset(self.check_registry),
            require_checks=True,
        )
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
                "SELECT MAX(created_ts) FROM notifications WHERE incident_id = ?", (row["id"],)
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
                ack_by=row["ack_by"],
                ack_ts=float(row["ack_ts"]) if row["ack_ts"] is not None else None,
            )
            self._istates[key] = GroupState(rungs=rungs, core=core)

    def _refresh_acks(self) -> None:
        """Acks land in the DB from CLI/MCP/web (PM-03 small writes); the
        engine only needs the flag flipped on its in-memory core."""
        from dataclasses import replace

        acked = {
            row["id"]: (row["ack_by"], row["ack_ts"])
            for row in self.conn.execute(
                "SELECT id, ack_by, ack_ts FROM incidents WHERE state = 'acked'"
            ).fetchall()
        }
        for key, st in self._istates.items():
            core = st.core
            if core and core.state == "open" and core.incident_id in acked:
                by, ts = acked[core.incident_id]
                self._istates[key] = GroupState(
                    rungs=st.rungs,
                    core=replace(
                        core,
                        state="acked",
                        ack_by=by,
                        ack_ts=float(ts) if ts is not None else None,
                    ),
                )
        if self.events_engine is not None:
            self.events_engine.refresh_acks(set(acked))

    def on_tick(self, wall: float, mono: float, gap_s: float) -> None:
        started = self.clock.monotonic()
        if gap_s:
            self.stats.count("clock_gaps")
        if self._reload_requested or mono - self._last_rescan >= _RESCAN_EVERY_S:
            self._reload_requested = False
            self._last_rescan = mono
            self._reload_channels()
            self._reload_check_registry()
            self._load_definitions()
            self._refresh_acks()
        cache: dict = {}
        outcomes: list[EvalOutcome] = []
        due_names = self.due.due(mono, lambda _n: self._overrun())
        due_defs = [self.monitors[name] for name in due_names if name in self.monitors]
        # The scheduler owns the complete due set, so it can run each alias
        # once fairly before definitions project that immutable raw evidence.
        self.external_sampler.prepare(due_defs, mono + 10.0)
        for name in due_names:
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
        while not self._delivery_failures.empty():
            channel, reason, ts = self._delivery_failures.get()
            self.writer.add_event(EventRecord(
                ts=ts, ingest_ts=ts, source="self", provider="ftmon.notify",
                event_id=None, severity=2,
                message=f"notification channel {channel} failed: {reason}",
            ))
        self.stats.ring_mem_bytes = self.rings.mem_bytes()
        self.rings.evict_if_over(self._is_protected, self.stats.count)
        self.writer.set_meta("last_tick_ts", repr(wall))
        try:
            self.writer.commit_tick()
        except sqlite3.OperationalError as exc:
            # PM-10: busy_timeout exceeded — drop this tick, stay alive.
            if "locked" not in str(exc).lower():
                raise
            self.stats.count("sqlite_lock_errors")
            # Buffered for the next successful commit (same pattern as
            # retention self-events); commit_tick already cleared the rest.
            self.writer.add_event(EventRecord(
                ts=wall, ingest_ts=wall, source="self", provider="ftmon.store",
                event_id=None, severity=2,
                message=f"tick write locked; dropped buffered writes: {exc}",
            ))
            self.executor.drain_actions()  # must not fire for uncommitted work
            self.stats.cycle_s = self.clock.monotonic() - started
            return
        # AC-02 actions are post-commit so their 30-second timeout cannot
        # extend the daemon's single tick transaction (PM-03).
        self.actions.run_pending(self.executor.drain_actions(), wall)
        # NO-04: delivery strictly after the transition committed.
        if self.dispatch_worker is None:
            self.outbox.flush(wall)
        else:
            self.dispatch_worker.wake()
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

    def _record_delivery_failure(self, channel: str, reason: str) -> None:
        """NO-07: expose terminal delivery failure without recursive notify."""
        self._delivery_failures.put((channel, reason, self.clock.now()))

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

    def request_reload(self) -> None:
        """PM-11: the SIGHUP handler may only record the request — the reload
        itself runs at the top of the next tick, never inside the handler."""
        self._reload_requested = True

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
        event_source=event_source,
        background_dispatch=not isinstance(clock, ControlledClock),
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

    def _reload(_sig, _frame):
        # PM-11: the default disposition for SIGHUP terminates the process —
        # exactly wrong for the conventional Unix reload signal.
        core.request_reload()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGHUP, _reload)

    tick_s = core.config.tick_seconds if core.config else 5.0
    total = len(core.monitors) + len(core.event_monitors)
    print(f"ftmon daemon started ({total} monitors)", file=sys.stderr)
    try:
        core.run_loop(tick_s)
    finally:
        # Network and journal readers own OS resources; an unexpected sampler
        # error must not leave either background boundary alive during teardown.
        if core.dispatch_worker is not None:
            core.dispatch_worker.stop()
        if core.events_engine is not None:
            core.events_engine.stop()  # reap the journalctl reader
    print("ftmon daemon stopped", file=sys.stderr)
    return 0

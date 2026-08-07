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

import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
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
from ftmon.paths import Paths, current_platform, get_paths, set_private_permissions
from ftmon.selfmon import SelfSampler, SelfStats
from ftmon.sources.base import EventSource
from ftmon.sources.disk import DiskSampler
from ftmon.sources.net import NetSampler
from ftmon.sources.process import ProcessSampler
from ftmon.sources.system import SystemSampler
from ftmon.sources.unit import UnitSampler
from ftmon.store import db as store_db
from ftmon.store.db import is_locked_error
from ftmon.store.outbox import DispatchWorker, Outbox
from ftmon.store.outbox import backlog as outbox_backlog
from ftmon.store.retention import BaselineLookup, Retention
from ftmon.store.writer import TickWriter

_RESCAN_EVERY_S = 30.0  # PM-04
_RETENTION_EVERY_S = 60.0  # DM-04: incremental; a minute cadence keeps passes tiny
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 3
_DAEMON_LOG = logging.getLogger("ftmon.daemon.file")
_DAEMON_LOG.propagate = False

IncidentKey = tuple[str, str, str]  # (monitor, entity_id, group)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        set_private_permissions(Path(self.baseFilename), 0o600)
        return stream


def _configure_daemon_log(path: Path) -> RotatingFileHandler:
    """Write the PM-06 daemon log independently of a service wrapper's stderr."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    set_private_permissions(path.parent, 0o700)
    for existing in tuple(_DAEMON_LOG.handlers):
        _DAEMON_LOG.removeHandler(existing)
        existing.close()
    handler = _PrivateRotatingFileHandler(
        path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    _DAEMON_LOG.addHandler(handler)
    _DAEMON_LOG.setLevel(logging.INFO)
    return handler


def _daemon_message(message: str) -> None:
    print(message, file=sys.stderr)
    _DAEMON_LOG.info(message)


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
    # None = current_platform() (production). Tests for a monitor.platforms
    # set that excludes the host running the suite (e.g. Windows-only
    # profiles, exercised on Linux CI) override this the same way they
    # override Clock, rather than the host OS gating what's testable.
    platform: str | None = None

    def __post_init__(self) -> None:
        self.platform = self.platform or current_platform()
        self.stats = SelfStats()
        self._delivery_failures: SimpleQueue[tuple[str, str, float]] = SimpleQueue()
        # (category, fatal, ts) — the worker thread cannot touch the writer.
        self._dispatch_faults: SimpleQueue[tuple[str, bool, float]] = SimpleQueue()
        self._reload_global_config = self.config is None
        if self._reload_global_config:
            self.config, config_warnings = load_config(self.paths.config_file)
            for w in config_warnings:
                _daemon_message(f"config warning: {w}")
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
            "self": SelfSampler(self.stats),
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
            EventEngine(
                source=self.event_source,
                executor=self.executor,
                counter=self.stats.count,
                cursor_name=getattr(self.event_source, "cursor_name", "journald"),
            )
            if self.event_source is not None else None
        )
        self._load_definitions(initial=True)
        self._rebuild_incidents()
        if self.events_engine is not None and self.event_monitors:
            self._start_events()
        self.dispatch_worker: DispatchWorker | None = None
        # PM-12: doctor reads only the database, so it cannot otherwise tell a
        # controlled-clock run that has no worker by design from a worker that
        # died. Recorded before the worker starts so the answer exists even if
        # the worker never gets a connection.
        self.writer.set_meta(
            "notify_dispatch_mode",
            "background" if self.background_dispatch else "synchronous",
        )
        if self.background_dispatch:
            # Network adapters run only on this worker connection; sampling
            # never waits for their ten-second timeout (DESIGN 10.7). The
            # worker owns reset_inflight too: doing it here first meant a lock
            # on the main connection aborted startup before any recovery path
            # existed (PM-12, issue #98).
            self.dispatch_worker = DispatchWorker(
                self.paths.db_file, notifiers, self.clock.now,
                quiet=self.config.quiet, on_terminal=self._record_delivery_failure,
                on_store_error=self._record_dispatch_recovery,
                on_fatal=self._record_dispatch_fatal,
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
            from ftmon.notify import desktop_notifier_for_platform

            desktop_notifier = desktop_notifier_for_platform()
            if desktop_notifier is not None and desktop_notifier.available:
                notifiers.append(desktop_notifier)
            else:
                _daemon_message(
                    "config warning: [notify.desktop] desktop_unavailable; channel disabled"
                )
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
                _daemon_message(
                    f"config warning: [notify.{name}] {exc}; channel disabled"
                )
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
            _daemon_message(f"config_error: checks.toml: {exc.category}")
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
            _daemon_message("config warning: config.toml removed; keeping loaded channels")
            return
        config, warnings = load_config(self.paths.config_file)
        for warning in warnings:
            _daemon_message(f"config warning: {warning}")
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

    def _union_event_channels(self) -> tuple[dict[str, dict], dict[str, str]]:
        """Union channels across every loaded event-sourced monitor -- there
        is one shared EvtSubscribe pass for the whole daemon, not one per
        monitor (win_evtlog.py). A `query=None` request (everything on that
        channel) is a superset of any filtered request, so it always wins
        regardless of which monitor loads first or last -- that is not a
        conflict, just the union of "everything" and "a subset of it". Only
        two differing *non-empty* queries for the same path are a genuine,
        unresolvable conflict; that keeps the first-seen query and reports
        it (via the caller merging into subscribe_errors) rather than
        silently picking one. A no-op on Linux/macOS: JournaldEventSource
        monitors never populate source_options.channels."""
        by_path: dict[str, dict] = {}
        conflicts: dict[str, str] = {}
        for mdef in self.event_monitors.values():
            for entry in mdef.source_options.get("channels", ()):
                path, query = entry["path"], entry.get("query")
                existing = by_path.get(path)
                if existing is None:
                    by_path[path] = {"path": path, "query": query}
                    continue
                if existing["query"] == query:
                    continue
                if existing["query"] is None or query is None:
                    by_path[path] = {"path": path, "query": None}  # unfiltered wins
                    continue
                conflicts[path] = (
                    f"channel {path!r} requested with conflicting queries "
                    f"across monitors; kept {existing['query']!r}, ignored "
                    f"{query!r} from monitor {mdef.name!r}"
                )
        return by_path, conflicts

    def _start_events(self) -> None:
        """DM-15: resume from the persisted cursor; rebuild open episodes so
        a restart cannot re-open (and re-notify) a live one."""
        assert self.events_engine is not None
        by_path, conflicts = self._union_event_channels()
        configure = getattr(self.event_source, "configure", None)
        if configure is not None and by_path:
            from ftmon.sources.win_evtlog import ChannelSpec

            configure(tuple(ChannelSpec(**c) for c in by_path.values()))
        row = self.conn.execute(
            "SELECT cursor FROM cursors WHERE source = ?",
            (self.events_engine.cursor_name,),
        ).fetchone()
        self.events_engine.start(row["cursor"] if row else None)
        if conflicts:
            # start() just reset subscribe_errors; merge conflicts in after,
            # not before -- a real EvtSubscribe failure for the same channel
            # takes priority over the conflict note.
            errors = getattr(self.event_source, "subscribe_errors", None)
            if errors is not None:
                for path, msg in conflicts.items():
                    errors.setdefault(path, msg)
        rows = self.conn.execute(
            "SELECT * FROM incidents WHERE state IN ('open', 'acked')"
        ).fetchall()
        self.events_engine.rebuild(rows, list(self.event_monitors.values()))

    def _warn_on_unapplied_event_channels(self) -> None:
        """Once the event reader has started, its subscribed channels are
        fixed for the daemon's lifetime (win_evtlog.py: one EvtSubscribe
        pass, not a hot-reconfigure path) -- a monitor loaded afterward
        (e.g. a newly-approved draft) requesting a channel nobody
        subscribed to yet would otherwise sit there silently, never
        receiving anything. Surfaced the same way a bad EvtSubscribe query
        is surfaced (EventEngine._report_channel_errors)."""
        errors = getattr(self.event_source, "subscribe_errors", None)
        configured_fn = getattr(self.event_source, "configured_paths", None)
        if errors is None or configured_fn is None:
            return
        configured = configured_fn()
        by_path, _conflicts = self._union_event_channels()
        for path in by_path:
            if path in configured or path in errors:
                continue
            errors[path] = (
                f"channel {path!r} requested by a monitor loaded after the "
                "event reader already started; restart the daemon to "
                "subscribe to it"
            )

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
            _daemon_message(f"config_error: {path}: {err}")
            self.stats.count("config_errors")
        seen = set()
        for mdef in defs:
            if self.platform not in mdef.platforms:
                # PL-01/PL-02: declared but unenforced was the actual gap —
                # a monitor's platforms list must gate loading, not just
                # validate as a well-formed subset of schema.PLATFORMS.
                if initial:
                    _daemon_message(
                        f"monitor {mdef.name}: not applicable on platform "
                        f"{self.platform!r} (declares {sorted(mdef.platforms)}); skipped"
                    )
                continue
            # MD-05: enabled=false stays on disk for one-line re-enable / git
            # history, but contributes no sampling, event ingestion, or rule
            # evaluation. Omitting it from `seen` also drops a previously
            # enabled monitor on the next rescan.
            if not mdef.enabled:
                continue
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
                    _daemon_message(
                        f"monitor {mdef.name}: source {mdef.source!r} not available "
                        "in this milestone; skipped"
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
        if (
            self.events_engine is not None
            and self.events_engine._started
            and not self.event_monitors
        ):
            self.events_engine.stop()

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
            row_ls = self.conn.execute(
                "SELECT last_seen FROM entities WHERE monitor = ? AND entity_id = ?",
                (row["monitor"], row["entity_id"]),
            ).fetchone()
            # IN-09: CA-08 grace state is memory-only; without this seed an
            # entity that vanished during downtime leaves its incident open
            # forever (rules evaluate None, so clear cycles never accumulate).
            # No stored row: fall back to now — the full grace re-runs rather
            # than clearing on hearsay.
            self.pipeline.seed_seen(
                row["monitor"],
                row["entity_id"],
                float(row_ls["last_seen"])
                if row_ls and row_ls["last_seen"] is not None else now,
            )

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
        # Before the samplers run, not after: the self sampler reads these
        # straight out of SelfStats, so measuring later would publish last
        # tick's database size against this tick's everything else.
        self._sample_db_pages()
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
            else:
                self._warn_on_unapplied_event_channels()
            self.events_engine.tick(list(self.event_monitors.values()), wall,
                                    mono, self.writer)
            self.stats.event_queue_depth = self.events_engine.queue_depth
            self.stats.events_dropped = self.events_engine.dropped
            self.stats.events_received = self.events_engine.received
            self.stats.events_repeated = self.events_engine.repeated
            self.stats.event_rate_per_min = self.events_engine.event_rate_per_min
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
        while not self._dispatch_faults.empty():
            category, fatal, ts = self._dispatch_faults.get()
            self.stats.count("notify_store_errors")
            # The category is a closed vocabulary, so this event can never
            # carry a path or credential out of an exception message (SE-04).
            self.writer.add_event(EventRecord(
                ts=ts, ingest_ts=ts, source="self", provider="ftmon.notify",
                event_id=None, severity=3 if fatal else 2,
                message=(
                    f"notification dispatcher stopped: {category}" if fatal
                    else f"notification dispatcher recovering: {category}"
                ),
            ))
        self._sample_outbox_backlog(wall)
        self.stats.ring_mem_bytes = self.rings.mem_bytes()
        self.rings.evict_if_over(self._is_protected, self.stats.count)
        self.writer.set_meta("last_tick_ts", repr(wall))
        try:
            self.writer.commit_tick()
        except sqlite3.OperationalError as exc:
            # PM-10: busy_timeout exceeded — drop this tick, stay alive.
            if not is_locked_error(exc):
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
        if self.retention.entities_reaped:
            # Reap runs on retention's own connection/transaction, so nothing
            # else notices these rows are gone unless told: the writer's
            # long-lived series-id cache (one instance per daemon lifetime)
            # would otherwise hand a returning identity a series id that no
            # longer exists, and a cached baseline for the same key would
            # keep answering for an entity that no longer has a baseline row.
            for monitor, entity_id in self.retention.reaped_keys:
                self.writer.evict_series_cache(monitor, entity_id)
            self.baselines.invalidate()
            self.stats.count("entities_reaped")
            self.writer.add_event(EventRecord(
                ts=wall, ingest_ts=wall, source="self", provider="ftmon.retention",
                event_id=None, severity=0,
                message=f"catalog reap: {self.retention.entities_reaped} "
                         "gone entities removed (MD-09)",
            ))

    def _record_delivery_failure(self, channel: str, reason: str) -> None:
        """NO-07: expose terminal delivery failure without recursive notify."""
        self._delivery_failures.put((channel, reason, self.clock.now()))

    def _sample_db_pages(self) -> None:
        """Publish the DM-05 storage picture into the self source (#104).

        DM-05 governs *used* pages, so an alarm on file allocation fires while
        the defined budget is healthy — a freed page is immediately reusable
        and costs nothing. The three PRAGMAs are cheap header reads on a
        connection this thread already owns; a lock or error keeps the previous
        values rather than publishing zero, since a momentary read failure is
        not evidence that the database shrank.
        """
        try:
            size = store_db.db_size_report(self.conn)
        except sqlite3.Error:
            return
        self.stats.db_allocated_bytes = float(size["allocated_bytes"])
        self.stats.db_used_bytes = float(size["used_bytes"])
        self.stats.db_freelist_bytes = float(size["freelist_bytes"])
        # stat() of the main file, which in WAL mode lags logical allocation
        # between checkpoints. Kept separate precisely because they differ:
        # db_bytes is what the pre-#104 metric measured and must keep meaning.
        if size["file_bytes"] is not None:
            self.stats.db_file_bytes = float(size["file_bytes"])
        # Signed against DM-05's normative target, not whatever level a
        # definition alarms at, so the reported distance to the budget stays
        # stable when a threshold is retuned. The constant lives with the
        # arithmetic in store.db so enforcement and reporting cannot drift.
        self.stats.db_headroom_bytes = float(store_db.DB_BUDGET_BYTES - size["used_bytes"])
        self.stats.entities_persisted = self.pipeline.persisted_entities(self.monitors)
        self.stats.series_persisted = self.pipeline.persisted_series(self.monitors)

    def _sample_outbox_backlog(self, wall: float) -> None:
        """Fold delivery debt into the self source (NO-10).

        Read on the daemon's own connection rather than giving SelfSampler a
        second one: these are the same rows the tick is already free to read,
        and an extra connection would only add contention. A read failure is
        never worth dropping a tick over — the gauges simply keep their last
        values, and the dispatcher's own state still tells doctor the truth.
        """
        try:
            counts = outbox_backlog(self.conn, wall, self.config.quiet)
        except sqlite3.Error:
            return
        self.stats.notify_pending_total = int(counts["pending_total"])
        self.stats.notify_due_claimable = int(counts["due_claimable"])
        self.stats.notify_quiet_held = int(counts["quiet_held"])
        self.stats.notify_failed = int(counts["failed"])
        self.stats.notify_oldest_claimable_due_age_s = float(
            counts["oldest_claimable_due_age_s"]
        )
        worker = self.dispatch_worker
        self.stats.notify_worker_alive = (
            1.0 if worker is None or worker.alive() else 0.0
        )

    def _record_dispatch_recovery(self, category: str) -> None:
        """PM-12: the dispatcher hit a store fault and is reconnecting."""
        self._dispatch_faults.put((category, False, self.clock.now()))

    def _record_dispatch_fatal(self, category: str) -> None:
        """PM-12: the dispatcher stopped for good; nothing will deliver now.

        Also goes to the daemon log, because doctor and `/self` only help
        someone who is already looking — and issue #98's whole complaint was
        that nobody was.
        """
        self._dispatch_faults.put((category, True, self.clock.now()))
        _daemon_message(
            f"notification dispatcher stopped: {category}; deliveries will not "
            "drain until the daemon restarts (see `ftmon doctor`)"
        )

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
    _configure_daemon_log(paths.log_file)

    from ftmon.paths import try_lock_exclusive

    lock_file = open(paths.lock_file, "w")  # noqa: SIM115 - held for process lifetime
    if not try_lock_exclusive(lock_file):
        _daemon_message("ftmon daemon already running (lock held); exiting")
        return 1
    # CL-07: `ftmon monitor rescan` signals this pid. The flock, not the pid
    # text, remains the single-instance authority (PM-02).
    import os

    lock_file.write(str(os.getpid()))
    lock_file.flush()

    clock: Clock
    if getattr(args, "clock", "system") == "controlled":
        clock = ControlledClock()  # platform endpoint selected by paths (TS-05/PL-01)
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
        from ftmon.sources import event_source_for_platform

        event_source = event_source_for_platform()

    core = DaemonCore(
        paths=paths,
        clock=clock,
        event_source=event_source,
        background_dispatch=not isinstance(clock, ControlledClock),
        # The checked-in deterministic scenarios model the Linux fixture
        # definitions (including systemd units).  Keep TS-04/TS-05 replay
        # host-independent when the harness itself runs on macOS or Windows.
        platform="linux" if scn is not None else None,
    )

    if scn is not None:
        # TS-04/TS-05: replace live samplers with scenario replay. In-place
        # update — the pipeline holds a reference to this same dict.
        from ftmon.sources import fixtures

        core.samplers.update(fixtures.fixture_samplers(scn))
        _daemon_message(f"fixtures: {args.fixtures} ({', '.join(sorted(scn.sources()))})")

    def _stop(_sig, _frame):
        core.stop = True

    def _reload(_sig, _frame):
        # PM-11: the default disposition for SIGHUP terminates the process —
        # exactly wrong for the conventional Unix reload signal.
        core.request_reload()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):  # Windows console graceful-stop equivalent
        signal.signal(signal.SIGBREAK, _stop)
    if hasattr(signal, "SIGHUP"):  # POSIX only; no reload signal on Windows
        signal.signal(signal.SIGHUP, _reload)

    from ftmon.paths import start_reload_watcher

    start_reload_watcher(core.request_reload)  # PM-11 equivalent on Windows; no-op on POSIX

    tick_s = core.config.tick_seconds if core.config else 5.0
    total = len(core.monitors) + len(core.event_monitors)
    _daemon_message(f"ftmon daemon started ({total} monitors)")
    try:
        core.run_loop(tick_s)
    except BaseException:
        _DAEMON_LOG.exception("ftmon daemon crashed")
        raise
    finally:
        # Network and journal readers own OS resources; an unexpected sampler
        # error must not leave either background boundary alive during teardown.
        if core.dispatch_worker is not None:
            core.dispatch_worker.stop()
        if core.events_engine is not None:
            core.events_engine.stop()  # reap the journalctl reader
    _daemon_message("ftmon daemon stopped")
    return 0

"""Event pipeline: drain -> rules -> store-filter -> storm guard -> episodes
(DM-09, DM-10, DM-15, SA-03, SA-08, IN-08).

Order matters and is fixed by the spec: rules evaluate against the *live*
stream first (DM-09 — a rule may match info-level events, and matching
forces storage), then the store-filter decides persistence, then the storm
guard rate-defends what actually gets stored. Episodes are stepped last,
from the matches, so a stored-or-not decision never affects alerting.

Supervision (SA-03): the reader subprocess is restarted with exponential
backoff (1 s -> 60 s cap), a self-event on first death, and a
last-activity age exposed for the `self` monitor's stall rule (SA-08).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ftmon.definitions.loader import MonitorDef, RuleDef
from ftmon.engine import episodes as ep
from ftmon.engine.incidents import GroupConfig
from ftmon.engine.render import render_message
from ftmon.model import (
    SEVERITIES,
    EventRecord,
    GroupState,
    IncidentCore,
    RecordEffect,
    severity_name,
)
from ftmon.sources.base import EventSource

DRAIN_BUDGET = 2000  # events per tick; the queue holds the rest (SA-08)
STORM_STORED_PER_MIN = 100  # DM-10
_BACKOFF_START_S = 1.0  # SA-03
_BACKOFF_CAP_S = 60.0

EpisodeKey = tuple[str, str, str]  # (monitor, rule_id, entity_key)


class EventCtx:
    """EvalContext over one canonical event record (PL-02: rules see only
    canonical fields, so an event expression is platform-blind)."""

    def __init__(self, record: EventRecord, params: Mapping[str, float], now: float):
        self._r = record
        self._params = params
        self._now = now

    def metric_last(self, m: str) -> float | None:
        return float(self._r.severity) if m == "severity" else None

    def metric_last_ts(self, m: str) -> float | None:
        return self._r.ingest_ts if m == "severity" else None

    def metric_window(self, m: str, seconds: float) -> list[tuple[float, float]]:
        return []  # events have no sampled history; window fns yield UNKNOWN

    def attr(self, a: str) -> str | None:
        if a == "provider":
            return self._r.provider
        if a == "event_id":
            return self._r.event_id
        if a == "message":
            return self._r.message
        if a == "source":
            return self._r.source
        return None

    def param(self, p: str) -> float:
        return self._params[p]

    def baseline(self, m: str) -> float | None:
        return None

    def now(self) -> float:
        return self._now


@dataclass
class _StormState:
    bucket: int = -1
    stored: int = 0
    active: bool = False
    suppressed: int = 0


@dataclass
class EventEngine:
    """One instance per daemon. tick() runs inside the tick, before commit,
    so everything it writes rides the tick's single transaction (PM-03)."""

    source: EventSource
    executor: object  # engine.effects.EffectExecutor (untyped: layering)
    counter: object  # SelfStats.count
    cursor_name: str = "journald"

    _states: dict[EpisodeKey, ep.EpisodeState] = field(default_factory=dict)
    _storms: dict[tuple[str, str], _StormState] = field(default_factory=dict)
    _started: bool = False
    _deaths: int = 0
    _next_restart_mono: float = 0.0
    _backoff_s: float = _BACKOFF_START_S
    _last_cursor: str | None = None
    _last_activity: float | None = None
    last_activity_age_s: float = 0.0  # -> SelfStats.source_activity_age_s
    queue_depth: int = 0
    dropped: int = 0

    def start(self, cursor: str | None) -> None:
        self._last_cursor = cursor
        self.source.start(cursor)
        self._started = True

    def stop(self) -> None:
        self.source.stop()

    def tick(self, monitors: list[MonitorDef], now: float, mono: float, writer) -> None:
        if not self._started:
            return
        self._supervise(now, mono, writer)

        records, cursor = self.source.drain(now, DRAIN_BUDGET)
        if cursor:
            writer.set_cursor(self.cursor_name, cursor, now)  # DM-15, same txn
            self._last_cursor = cursor  # a reader restart resumes from here
        if records:
            self._last_activity = now
        self.last_activity_age_s = (
            now - self._last_activity if self._last_activity is not None else 0.0
        )
        self.queue_depth = getattr(self.source, "queue_depth", lambda: 0)()
        self.dropped = getattr(self.source, "dropped", 0)

        # 1) rules against the live stream; matches keyed by episode identity
        matches: dict[EpisodeKey, list[tuple[float, str]]] = {}
        matched_records: set[int] = set()
        for mdef in monitors:
            for i, rec in enumerate(records):
                ctx = EventCtx(rec, mdef.parameters, now)
                for rule in mdef.rules:
                    if rule.when.eval(ctx, counter=self.counter) is not True:
                        continue
                    matched_records.add(i)
                    key = (mdef.name, rule.id, _entity_key(rec))
                    matches.setdefault(key, []).append(
                        (rec.ingest_ts, self._render(rule, rec)))

        # 2) store-filter (DM-09) + storm guard (DM-10) on what gets stored
        for i, rec in enumerate(records):
            if not self._should_store(monitors, rec, i in matched_records):
                self.counter("events_unstored")
                continue
            if self._storm_suppressed(rec, now, writer):
                continue
            writer.add_event(rec)
        self._close_quiet_storms(now, writer)

        # 3) episodes: every key with matches, plus every open one (quiet
        # periods need the tick even when nothing matched)
        for key in set(matches) | set(self._states):
            cfg = self._episode_cfg(monitors, key)
            if cfg is None:  # rule/monitor edited away: MD-09-style silent drop
                self._states.pop(key, None)
                continue
            st = self._states.get(key, ep.EpisodeState())
            st, effects = ep.step_episode(cfg, st, tuple(matches.get(key, ())), now)
            if effects:
                gcfg = GroupConfig(monitor=cfg.monitor, entity_id=cfg.entity_key,
                                   group=cfg.rule_id, rungs=())
                gstate = self.executor.apply(gcfg, ep.as_group_state(st), effects, now)
                st = ep.EpisodeState(
                    core=gstate.core, last_seen_ts=st.last_seen_ts,
                    pending_ts=st.pending_ts, flap_clears=st.flap_clears,
                    cooldown_s=st.cooldown_s,
                )
            if st.core is None and not st.pending_ts:
                self._states.pop(key, None)
            else:
                self._states[key] = st

    # -- supervision (SA-03) ---------------------------------------------

    def _supervise(self, now: float, mono: float, writer) -> None:
        if self.source.alive():
            self._backoff_s = _BACKOFF_START_S
            return
        if self._deaths == 0:
            writer.add_event(EventRecord(
                ts=now, ingest_ts=now, source="self", provider="ftmon.events",
                event_id=None, severity=2,
                message="event reader died; restarting with backoff",
            ))
            self._deaths = 1
            self._next_restart_mono = mono  # first restart immediately
        if mono >= self._next_restart_mono:
            # resume from the last drained cursor: the journal replays what
            # happened while the reader was dead (DM-15), nothing is lost
            self.source.start(self._last_cursor)
            self._deaths += 1
            self._next_restart_mono = mono + self._backoff_s
            self._backoff_s = min(self._backoff_s * 2, _BACKOFF_CAP_S)

    # -- storage policy ----------------------------------------------------

    def _should_store(self, monitors: list[MonitorDef], rec: EventRecord,
                      matched: bool) -> bool:
        """DM-09: severity >= store_min_severity (default notice), or any
        rule matched (matching forces storage so `ftmon incident` can show
        the evidence). Self-events always store — they are FTMON's own audit."""
        if matched or rec.source == "self":
            return True
        return rec.severity >= self._store_min(monitors)

    @staticmethod
    def _store_min(monitors: list[MonitorDef]) -> int:
        for mdef in monitors:
            v = mdef.source_options.get("store_min_severity")
            if isinstance(v, str) and v in SEVERITIES:
                return SEVERITIES.index(v)
            if isinstance(v, int) and 0 <= v <= 4:
                return v
        return 1  # notice

    def _storm_suppressed(self, rec: EventRecord, now: float, writer) -> bool:
        """DM-10 per (source, provider): >100 stored/min collapses into one
        event_storm self-event until the rate drops."""
        st = self._storms.setdefault((rec.source, rec.provider), _StormState())
        bucket = int(now // 60)
        if bucket != st.bucket:
            st.bucket = bucket
            st.stored = 0
        if st.active:
            st.suppressed += 1
            return True
        st.stored += 1
        if st.stored > STORM_STORED_PER_MIN:
            st.active = True
            st.suppressed = 1
            writer.add_event(EventRecord(
                ts=now, ingest_ts=now, source="self", provider="ftmon.events",
                event_id=None, severity=1,
                message=f"event_storm: {rec.provider} exceeded "
                        f"{STORM_STORED_PER_MIN}/min; collapsing",
            ))
            return True
        return False

    def _close_quiet_storms(self, now: float, writer) -> None:
        bucket = int(now // 60)
        for (_source, provider), st in self._storms.items():
            # a full minute with nothing suppressed-or-stored ends the storm
            if st.active and bucket > st.bucket:
                writer.add_event(EventRecord(
                    ts=now, ingest_ts=now, source="self", provider="ftmon.events",
                    event_id=None, severity=1,
                    message=f"event_storm over: {provider} "
                            f"({st.suppressed} events collapsed)",
                ))
                st.active = False
                st.suppressed = 0
                st.stored = 0
                st.bucket = bucket

    # -- episode plumbing --------------------------------------------------

    def _render(self, rule: RuleDef, rec: EventRecord) -> str:
        values = {
            "provider": rec.provider,
            "message": rec.message,
            "event_id": rec.event_id,
            "severity": severity_name(rec.severity),
            "source": rec.source,
            "entity": rec.provider,
        }
        return render_message(rule.message, values)

    def _episode_cfg(self, monitors: list[MonitorDef], key: EpisodeKey) -> (
            ep.EpisodeConfig | None):
        monitor, rule_id, entity_key = key
        for mdef in monitors:
            if mdef.name != monitor:
                continue
            for rule in mdef.rules:
                if rule.id == rule_id:
                    return ep.EpisodeConfig(
                        monitor=monitor,
                        rule_id=rule_id,
                        entity_key=entity_key,
                        severity=rule.severity,
                        cooldown_s=rule.cooldown_s or 600.0,
                        clear_after_s=rule.clear_after_s or 1800.0,
                        confirm_count=rule.confirm_count,
                        confirm_window_s=rule.confirm_window_s,
                        notify_recovery=rule.notify_recovery,
                    )
        return None

    def supersede(self, monitor: str, now: float) -> None:
        """MD-06/MD-09 for episodes: the definition changed or vanished —
        silently close its open episodes (the rules changed, not the world;
        same semantics as incidents.clear_superseded)."""
        from dataclasses import replace

        for key in [k for k in self._states if k[0] == monitor]:
            st = self._states.pop(key)
            core = st.core
            if core is None or core.state == "cleared" or core.incident_id is None:
                continue
            gcfg = GroupConfig(monitor=monitor, entity_id=key[2], group=key[1],
                               rungs=())
            self.executor.apply(
                gcfg,
                GroupState(rungs={}, core=replace(core, state="cleared")),
                (RecordEffect("clear", {"reason": "superseded"}),),
                now,
            )

    def refresh_acks(self, acked_ids: set[int]) -> None:
        """Mirror of DaemonCore._refresh_acks for episode cores."""
        from dataclasses import replace

        for key, st in self._states.items():
            core = st.core
            if core and core.state == "open" and core.incident_id in acked_ids:
                self._states[key] = replace(st, core=replace(core, state="acked"))

    def rebuild(self, rows, monitors: list[MonitorDef]) -> None:
        """Restart continuity: reload open/acked episode incidents so a
        daemon restart cannot re-open (and re-notify) a live episode. The
        quiet-period anchor resumes from last_change_ts — conservative: at
        worst the episode stays open one clear_after longer than it would
        have."""
        names = {m.name for m in monitors}
        for row in rows:
            if row["monitor"] not in names:
                continue
            key = (row["monitor"], row["grp"], row["entity_id"])
            self._states[key] = ep.EpisodeState(
                core=_core_from_row(row), last_seen_ts=float(row["last_change_ts"]),
            )


def _core_from_row(row) -> IncidentCore:
    return IncidentCore(
        incident_id=row["id"],
        state=row["state"],
        severity=row["severity"],
        owning_rule=row["owning_rule"],
        opened_ts=row["opened_ts"],
        last_notify_ts=float(row["last_change_ts"]),
        notify_count=row["notify_count"],
        backoff_tier=0,
        flap_clears=(),
        occurrences=row["occurrences"],
    )


def _entity_key(rec: EventRecord) -> str:
    """SPEC 7.7.3 episode identity: (rule, provider, event_id|msg_hash) —
    the rule is in the EpisodeKey; this is the entity part."""
    tail = rec.event_id if rec.event_id else ep.msg_hash(rec.message)
    return f"{rec.provider}:{tail}"

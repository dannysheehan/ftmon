"""Per-monitor sampling pipeline (SA-06): source snapshot -> rings ->
derived -> exemptions -> rule evaluations, plus persistence selection
(SA-05) and entity-disappearance tracking (CA-08).

Rule *evaluations* leave here as TriBools; turning them into incidents is
the M2 incident engine's job (IN-06) — the pipeline stays pure-ish data flow
so the two can be tested independently.

Why persistence is selective: track-all + promote (SA-05) is what keeps the
DB inside DM-05 with hundreds of processes. Everything is sampled into rings
(so promotion heuristics and later queries over the short window work), but
only watchlist/top-N/promoted process entities get durable history. Non-
process sources have few entities and persist everything.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from ftmon.clock import Clock, SystemClock
from ftmon.definitions.loader import MonitorDef
from ftmon.engine.context import EntityCtx
from ftmon.engine.render import render_message
from ftmon.engine.rings import RingStore
from ftmon.expr.tribool import to_tribool
from ftmon.model import EventRecord, Snapshot, TriBool, severity_name
from ftmon.sources.base import Sampler

_DEMOTE_AFTER_S = 30 * 60  # SA-05: demote after 30m without the heuristic holding
PROMOTION_LIMIT_PER_MONITOR = 10  # Chosen per-monitor concentration guardrail
_DURABLE_SOURCES = {"system", "disk", "self"}  # DM-04 retention split (DESIGN 9)


@dataclass(frozen=True)
class EvalOutcome:
    monitor: str
    entity_id: str
    rule_id: str
    group: str
    result: TriBool
    message: str = ""  # rendered rule message; non-empty only when TRUE (MD-02)


@dataclass
class _MonitorState:
    seen: dict[str, float] = field(default_factory=dict)  # entity_id -> last seen wall ts
    promoted: dict[str, float] = field(default_factory=dict)  # entity_id -> last True ts
    promotion_denied: set[str] = field(default_factory=set)
    promotion_limited: bool = False


class Pipeline:
    def __init__(
        self,
        samplers: Mapping[str, Sampler],
        rings: RingStore,
        counter: Callable[[str], None],
        gone_grace_s: float = 300.0,
        baseline_lookup: Callable[[str, str, str], float | None] | None = None,
        # TS-03: stage timing needs a monotonic reading, which only a Clock
        # may take. Defaulted so no caller loses the measurement silently.
        clock: Clock | None = None,
    ):
        self._samplers = samplers
        self._rings = rings
        self._counter = counter
        self._gone_grace_s = gone_grace_s
        self._baseline_lookup = baseline_lookup
        self._clock = clock if clock is not None else SystemClock()
        # Accumulated over a tick and reset by the daemon, because one tick
        # runs many monitors and the operator's question is about the tick.
        self.sample_s = 0.0
        self.evaluate_s = 0.0
        self._state: dict[str, _MonitorState] = {}
        # Self-events buffer: the daemon drains this after each tick and hands
        # the records to the writer - the pipeline must not depend on
        # writer.add_event ordering relative to sample writes.
        self._events: list[EventRecord] = []
        # Gone entities this tick, drained by the daemon so the incident
        # engine can auto-clear (CA-08 -> IN-07).
        self._gone: list[tuple[str, str]] = []
        # DM-16 pressure, per monitor. Counting what `_persist` actually wrote
        # is the only honest answer to "how much catalog are we sustaining":
        # `gone_ts IS NULL` counts every *running* process, because `seen` is
        # populated for the whole snapshot rather than the selected subset.
        self._persisted: dict[str, int] = {}
        # DM-16's series worksheet needs the same treatment as its entity
        # budget: a count of series *written this tick*, not of series whose
        # owning entity happens to still be running.
        self._persisted_series: dict[str, int] = {}
        self._promotion_rejections_total = 0

    def run_monitor(
        self,
        mdef: MonitorDef,
        now: float,
        deadline_mono: float,
        writer,  # store.writer.TickWriter; untyped to keep engine->store loose
        snapshot_cache: dict[object, Snapshot],
    ) -> list[EvalOutcome]:
        entered = self._clock.monotonic()
        sample_before = self.sample_s
        # SA-06: a source shared by several monitors runs once per tick; all
        # consumers see identical values and timestamps.
        # External definitions can map the same immutable raw plugin result to
        # different metric names and units. Cache their projected snapshots per
        # monitor; ExternalSampler separately guarantees the alias executes once.
        cache_key: object = (
            (mdef.source, mdef.name) if mdef.source == "external" else mdef.source
        )
        snap = snapshot_cache.get(cache_key)
        if snap is None:
            # SA-06: one shared sample per (source, options) per tick. Timed
            # separately from everything below because "the tick is slow" has
            # two very different answers -- a sampler blocking on /proc, or
            # rule evaluation over many entities -- and cycle_s cannot tell
            # them apart (#106). Cache hits cost nothing and are not counted.
            sample_started = self._clock.monotonic()
            snap = self._samplers[mdef.source].sample(now, deadline_mono, mdef.source_options)
            self.sample_s += self._clock.monotonic() - sample_started
            snapshot_cache[cache_key] = snap

        st = self._state.setdefault(mdef.name, _MonitorState())
        rings = self._rings

        for ent in snap.entities:
            for metric, value in ent.metrics.items():
                rings.append(mdef.name, ent.entity_id, metric, snap.ts, value)
            st.seen[ent.entity_id] = now

        # Derived metrics feed rings too so rules and later derived can window
        # over them; evaluation order is the loader's topological order (MD-08).
        derived_vals: dict[str, dict[str, float]] = {}
        for ent in snap.entities:
            ctx = self._ctx(mdef, ent.entity_id, ent.attrs, now)
            vals: dict[str, float] = {}
            for name, expr in mdef.derived:
                v = expr.eval(ctx, counter=self._counter)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                    rings.append(mdef.name, ent.entity_id, name, snap.ts, float(v))
                    vals[name] = float(v)
            derived_vals[ent.entity_id] = vals

        exempt_entities: set[str] = set()
        outcomes: list[EvalOutcome] = []
        for ent in snap.entities:
            ctx = self._ctx(mdef, ent.entity_id, ent.attrs, now)
            # CA-07 needs this tick's transient context to decide exclusion,
            # but excluded entities must never enter persistent history.
            if any(e.eval(ctx, counter=self._counter) is True for e in mdef.exempt):
                exempt_entities.add(ent.entity_id)
                continue
            # Message values are this cycle's numbers; rendered only for TRUE
            # results because that is the only case a notification can use.
            values: dict[str, object] = dict(mdef.parameters)
            values.update(ent.attrs)
            values.update(ent.metrics)
            values.update(derived_vals.get(ent.entity_id, {}))
            values["entity"] = ent.attrs.get("display") or ent.attrs.get("name", ent.entity_id)
            values["monitor"] = mdef.name
            for rule in mdef.rules:
                result = to_tribool(rule.when.eval(ctx, counter=self._counter))
                if result is TriBool.UNKNOWN:
                    self._counter("eval_unknown_total")
                message = ""
                if result is TriBool.TRUE:
                    values["severity"] = severity_name(rule.severity)
                    message = render_message(rule.message, values)
                outcomes.append(
                    EvalOutcome(mdef.name, ent.entity_id, rule.id, rule.group, result, message)
                )

        self._persist(mdef, snap, derived_vals, exempt_entities, st, now, writer)
        self._track_gone(mdef, st, now, writer)
        # Everything this call spent that was not the shared sample above.
        self.evaluate_s += (
            (self._clock.monotonic() - entered) - (self.sample_s - sample_before)
        )
        return outcomes

    def promoted(self, monitor: str) -> set[str]:
        return set(self._state.get(monitor, _MonitorState()).promoted)

    def has_persisted_data(self) -> bool:
        """False until a monitor has actually run (RB-02).

        Distinguishes "no tick has recorded anything yet" from "this tick
        persisted nothing", so a restart publishes no reading rather than a
        zero that reads as a measurement.
        """
        return bool(self._persisted)

    def persisted_series(self, monitors: Iterable[str]) -> int:
        """Series written durable history this tick, over `monitors` (DM-16)."""
        return sum(self._persisted_series.get(name, 0) for name in monitors)

    def persisted_entities(self, monitors: Iterable[str]) -> int:
        """Entities currently being written durable history, over `monitors`.

        The caller supplies the loaded set rather than this summing its own
        dict: a removed definition stops contributing pressure immediately,
        while a monitor whose interval means it did not run this tick keeps
        its last count instead of dropping to zero (MD-09, DM-16).
        """
        return sum(self._persisted.get(name, 0) for name in monitors)

    def promotion_limited_monitors(self, monitors: Iterable[str]) -> int:
        """Loaded process monitors currently refusing promotion admissions."""
        return sum(
            self._state.get(name, _MonitorState()).promotion_limited
            for name in monitors
        )

    @property
    def promotion_rejections_total(self) -> int:
        """Promotion admission refusals since this daemon started."""
        return self._promotion_rejections_total

    def _ctx(self, mdef: MonitorDef, entity_id: str, attrs: Mapping, now: float) -> EntityCtx:
        ctx = EntityCtx(
            rings=self._rings,
            monitor=mdef.name,
            entity_id=entity_id,
            attrs=attrs,
            params=mdef.parameters,
            wall=now,
        )
        if self._baseline_lookup is not None:  # CA-05 arrives with the store (M2)
            ctx = replace(ctx, baseline_lookup=self._baseline_lookup)
        return ctx

    def _persist(
        self,
        mdef: MonitorDef,
        snap: Snapshot,
        derived_vals: dict[str, dict[str, float]],
        exempt_entities: set[str],
        st: _MonitorState,
        now: float,
        writer,
    ) -> None:
        selected = self._select_persisted(mdef, snap, st, now, exempt_entities)
        selected.difference_update(exempt_entities)
        # Overwrite rather than accumulate: this is a gauge of current pressure,
        # and a monitor that stops selecting an entity must stop counting it.
        self._persisted[mdef.name] = len(selected)
        series_written = 0
        # DM-04 names "system, disk, self, and watchlist-synthetic entities"
        # as durable. A per-monitor flag could not express the last clause:
        # `net` emits a synthetic listener watchlist beside a discovered
        # `totals`, so one monitor legitimately holds both kinds (issue #119).
        monitor_durable = mdef.source in _DURABLE_SOURCES
        for ent in snap.entities:
            if ent.entity_id in exempt_entities:
                # Purging handles definitions or attributes that become exempt
                # after this monitor already retained history (CA-07).
                writer.forget_entity(mdef.name, ent.entity_id)
                continue
            if ent.entity_id not in selected:
                continue
            writer.upsert_entity(mdef.name, ent.entity_id, now, dict(ent.attrs))
            values = dict(ent.metrics)
            values.update(derived_vals.get(ent.entity_id, {}))
            series_written += len(values)
            for metric, value in values.items():
                sid = writer.series_id(
                    mdef.name, ent.entity_id, metric,
                    monitor_durable or ent.synthetic,
                )
                writer.add_sample(sid, snap.ts, value)
        self._persisted_series[mdef.name] = series_written

    def _select_persisted(
        self,
        mdef: MonitorDef,
        snap: Snapshot,
        st: _MonitorState,
        now: float,
        exempt_entities: set[str],
    ) -> set[str]:
        if mdef.source != "process":
            return {e.entity_id for e in snap.entities}

        # SA-05 (b): union of top-N by cpu and by rss this cycle.
        top_n = int(mdef.source_options.get("top_n", 15))
        selected: set[str] = set()
        for metric in ("cpu_pct", "rss_bytes"):
            ranked = sorted(
                (e for e in snap.entities if metric in e.metrics),
                key=lambda e: e.metrics[metric],
                reverse=True,
            )
            selected.update(e.entity_id for e in ranked[:top_n])

        # SA-05 (c): promotion heuristic over the in-ring short window.
        promotion_matches: set[str] = set()
        if mdef.promotion is not None:
            for ent in snap.entities:
                if ent.entity_id in exempt_entities:
                    continue
                ctx = self._ctx(mdef, ent.entity_id, ent.attrs, now)
                if mdef.promotion.eval(ctx, counter=self._counter) is True:
                    promotion_matches.add(ent.entity_id)

        # Existing admissions keep their slots while the heuristic remains
        # true. New candidates are sorted so sampler enumeration order cannot
        # decide which entities receive durable history at the runtime cap.
        # This is stable admission, not severity ranking: promotion expressions
        # return only a boolean and expose no score by which to rank matches.
        for entity_id in promotion_matches & st.promoted.keys():
            st.promoted[entity_id] = now
        for entity_id, last_true in list(st.promoted.items()):
            if now - last_true > _DEMOTE_AFTER_S:
                del st.promoted[entity_id]
                self._self_event(mdef, now, f"demoted {entity_id}")

        slots = max(0, PROMOTION_LIMIT_PER_MONITOR - len(st.promoted))
        candidates = sorted(promotion_matches - st.promoted.keys())
        for entity_id in candidates[:slots]:
            st.promoted[entity_id] = now
            self._self_event(mdef, now, f"promoted {entity_id}")

        denied = set(candidates[slots:])
        newly_denied = denied - st.promotion_denied
        self._promotion_rejections_total += len(newly_denied)
        limited = bool(denied)
        if limited and not st.promotion_limited:
            self._self_event(
                mdef,
                now,
                f"promotion limit reached: {len(st.promoted)} admitted, "
                f"{len(denied)} refused (limit {PROMOTION_LIMIT_PER_MONITOR})",
                event_id="promotion-limit",
                severity=1,
            )
        elif not limited and st.promotion_limited:
            self._self_event(
                mdef,
                now,
                "promotion limit recovered: no promotion matches are being refused",
                event_id="promotion-limit",
                severity=1,
            )
        st.promotion_denied = denied
        st.promotion_limited = limited
        selected.update(st.promoted)
        return selected

    def _track_gone(self, mdef: MonitorDef, st: _MonitorState, now: float, writer) -> None:
        """CA-08: discovered entities absent past gone_grace are marked gone;
        rings are dropped so a reused entity_id starts clean. Incident
        auto-clear on gone happens in the M2 incident engine."""
        for entity_id, last_seen in list(st.seen.items()):
            if now - last_seen <= self._gone_grace_s:
                continue
            del st.seen[entity_id]
            st.promoted.pop(entity_id, None)
            st.promotion_denied.discard(entity_id)
            self._rings.forget_entity(mdef.name, entity_id)
            writer.upsert_entity(mdef.name, entity_id, last_seen, {}, gone_ts=now)
            self._gone.append((mdef.name, entity_id))  # incident engine input (IN-07)
            self._self_event(mdef, now, f"entity gone: {entity_id}")

    def _self_event(
        self,
        mdef: MonitorDef,
        now: float,
        message: str,
        *,
        event_id: str | None = None,
        severity: int = 0,
    ) -> None:
        self._events.append(
            EventRecord(
                ts=now,
                ingest_ts=now,
                source="self",
                provider=f"ftmon.{mdef.name}",
                event_id=event_id,
                severity=severity,
                message=message,
            )
        )

    def drain_self_events(self) -> list[EventRecord]:
        out = list(self._events)
        self._events.clear()
        return out

    def drain_gone(self) -> list[tuple[str, str]]:
        out = list(self._gone)
        self._gone.clear()
        return out

    def seed_seen(self, monitor: str, entity_id: str, last_seen: float) -> None:
        """IN-09: disappearance tracking (CA-08) is memory-only, so the daemon
        seeds it at startup from stored last_seen for entities with open
        incidents. The ordinary grace path then does the clearing — there is
        deliberately no second clearing mechanism. setdefault: a genuine
        sighting must never be overwritten by a stale stored timestamp."""
        self._state.setdefault(monitor, _MonitorState()).seen.setdefault(entity_id, last_seen)

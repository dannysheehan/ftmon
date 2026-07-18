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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from ftmon.definitions.loader import MonitorDef
from ftmon.engine.context import EntityCtx
from ftmon.engine.render import render_message
from ftmon.engine.rings import RingStore
from ftmon.expr.tribool import to_tribool
from ftmon.model import EventRecord, Snapshot, TriBool, severity_name
from ftmon.sources.base import Sampler

_DEMOTE_AFTER_S = 30 * 60  # SA-05: demote after 30m without the heuristic holding
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


class Pipeline:
    def __init__(
        self,
        samplers: Mapping[str, Sampler],
        rings: RingStore,
        counter: Callable[[str], None],
        gone_grace_s: float = 300.0,
        baseline_lookup: Callable[[str, str, str], float | None] | None = None,
    ):
        self._samplers = samplers
        self._rings = rings
        self._counter = counter
        self._gone_grace_s = gone_grace_s
        self._baseline_lookup = baseline_lookup
        self._state: dict[str, _MonitorState] = {}
        # Self-events buffer: the daemon drains this after each tick and hands
        # the records to the writer - the pipeline must not depend on
        # writer.add_event ordering relative to sample writes.
        self._events: list[EventRecord] = []
        # Gone entities this tick, drained by the daemon so the incident
        # engine can auto-clear (CA-08 -> IN-07).
        self._gone: list[tuple[str, str]] = []

    def run_monitor(
        self,
        mdef: MonitorDef,
        now: float,
        deadline_mono: float,
        writer,  # store.writer.TickWriter; untyped to keep engine->store loose
        snapshot_cache: dict[object, Snapshot],
    ) -> list[EvalOutcome]:
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
            snap = self._samplers[mdef.source].sample(now, deadline_mono, mdef.source_options)
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
        return outcomes

    def promoted(self, monitor: str) -> set[str]:
        return set(self._state.get(monitor, _MonitorState()).promoted)

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
        selected = self._select_persisted(mdef, snap, st, now)
        selected.difference_update(exempt_entities)
        durable = mdef.source in _DURABLE_SOURCES
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
            for metric, value in values.items():
                sid = writer.series_id(mdef.name, ent.entity_id, metric, durable)
                writer.add_sample(sid, snap.ts, value)

    def _select_persisted(
        self, mdef: MonitorDef, snap: Snapshot, st: _MonitorState, now: float
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
        if mdef.promotion is not None:
            for ent in snap.entities:
                ctx = self._ctx(mdef, ent.entity_id, ent.attrs, now)
                if mdef.promotion.eval(ctx, counter=self._counter) is True:
                    if ent.entity_id not in st.promoted:
                        self._self_event(mdef, now, f"promoted {ent.entity_id}")
                    st.promoted[ent.entity_id] = now
        for entity_id, last_true in list(st.promoted.items()):
            if now - last_true > _DEMOTE_AFTER_S:
                del st.promoted[entity_id]
                self._self_event(mdef, now, f"demoted {entity_id}")
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
            self._rings.forget_entity(mdef.name, entity_id)
            writer.upsert_entity(mdef.name, entity_id, last_seen, {}, gone_ts=now)
            self._gone.append((mdef.name, entity_id))  # incident engine input (IN-07)
            self._self_event(mdef, now, f"entity gone: {entity_id}")

    def _self_event(self, mdef: MonitorDef, now: float, message: str) -> None:
        self._events.append(
            EventRecord(
                ts=now,
                ingest_ts=now,
                source="self",
                provider=f"ftmon.{mdef.name}",
                event_id=None,
                severity=0,
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

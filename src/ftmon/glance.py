"""Shared read-side health and glance policy (UI-14, UI-17, UI-18, MC-01, CA-07).

The dashboard and MCP both have to answer "is this daemon fresh, is this
monitor healthy, and what is its one declared primary readout?". Two
implementations of that policy drifted on freshness alone (the web used
`age > 15`, MCP `age >= 15`), so selection lives here once and each consumer
only formats the result (issue #64).

This module is read-only, presentation-free and imports nothing from
`ftmon.web`: MCP must never pull in a Starlette application to read a value.
"""

from __future__ import annotations

from dataclasses import dataclass

from ftmon.paths import Paths
from ftmon.store.query import GlanceSample, Query

# 3x the 5 s base tick (UI-04's staleness rule). Exactly at the boundary is
# still fresh; both consumers share the comparison so neither can re-diverge.
STALE_AFTER_S = 15.0

# MC-01 bound for the additive get_status readouts. A host can validly load
# more monitors than a single response should carry, so the cap is explicit
# and reported rather than assumed from a tile-count estimate (issue #64).
MAX_GLANCES = 64

# UI-17: only a trustworthy tile state may carry a current readout.
GLANCE_STATES = frozenset({"clear", "warning", "error"})

EVENT_RATE_METRIC = "event_rate_per_min"
_EVENT_RATE_WINDOW_S = 120.0


def daemon_stale(age_s: float | None) -> bool:
    """UI-04 staleness: an absent tick timestamp is stale; exactly 15 s is not."""
    return age_s is None or age_s > STALE_AFTER_S


def daemon_alive(age_s: float | None) -> bool:
    """Complement of `daemon_stale` — never a separately-derived threshold."""
    return age_s is not None and age_s <= STALE_AFTER_S


def check_aliases(paths: Paths) -> frozenset[str]:
    """Load administrator check aliases; empty when registry is absent/invalid.

    An unreadable registry must not make every external monitor look loadable
    (EC-01): callers pass this with `require_checks=True` so an unavailable
    alias surfaces as a configuration error instead of a healthy monitor.
    """
    if not paths.check_registry_file.exists():
        return frozenset()
    try:
        from ftmon.checks.registry import load as load_check_registry

        return frozenset(load_check_registry(paths.check_registry_file, paths=paths))
    except ValueError:
        return frozenset()


def health_state(
    *,
    stale: bool,
    has_evidence: bool,
    enabled: bool,
    max_severity: int | None,
    config_error: bool = False,
) -> str:
    """UI-14 precedence evaluated once for every consumer.

    `config_error > stale_or_unknown > disabled > error_or_critical >
    notice_or_warning > clear`. Acknowledgment is deliberately not consulted:
    an acked incident keeps its severity and cannot return a monitor to clear.
    """
    if config_error:
        return "config-error"
    if stale or not has_evidence:
        return "unknown"
    if not enabled:
        return "disabled"
    if max_severity is not None and max_severity >= 3:
        return "error"
    if max_severity is not None:
        return "warning"
    return "clear"


def has_evidence(q: Query | None, mdef) -> bool:
    """Whether the store holds anything proving this monitor ever ran (UI-14)."""
    if q is None:
        return False
    seen = q._conn.execute(
        "SELECT EXISTS(SELECT 1 FROM monitor_loads WHERE monitor=?) "
        "OR EXISTS(SELECT 1 FROM series WHERE monitor=?)",
        (mdef.name, mdef.name),
    ).fetchone()[0] == 1
    if seen or mdef.source != "events":
        return seen
    # An events monitor may hold a journal cursor before any series exists.
    return q._conn.execute("SELECT EXISTS(SELECT 1 FROM cursors)").fetchone()[0] == 1


def open_incidents_by_monitor(q: Query | None) -> dict[str, list]:
    """Live (open or acked) incidents grouped by monitor for UI-14 severity."""
    live: dict[str, list] = {}
    if q is None:
        return live
    for row in q.incidents(state=None):
        if row["state"] != "cleared":
            live.setdefault(row["monitor"], []).append(row)
    return live


@dataclass(frozen=True)
class StoredEntityCtx:
    """Persisted expression context used only to honor CA-07 in glance."""

    query: Query
    monitor: str
    entity_id: str
    attrs: dict[str, str]
    params: dict[str, float]
    wall: float

    def metric_last(self, metric: str) -> float | None:
        point = self.query.entity_metric_last(self.monitor, self.entity_id, metric)
        return None if point is None else point.value

    def metric_last_ts(self, metric: str) -> float | None:
        point = self.query.entity_metric_last(self.monitor, self.entity_id, metric)
        return None if point is None else point.ts

    def metric_window(self, metric: str, seconds: float) -> list[tuple[float, float]]:
        return self.query.entity_metric_window(
            self.monitor, self.entity_id, metric, start=self.wall - seconds
        )

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    def param(self, name: str) -> float:
        return self.params[name]

    def baseline(self, metric: str) -> float | None:
        record = self.query.current_baseline(self.monitor, self.entity_id, metric)
        return None if record is None else record.level

    def now(self) -> float:
        return self.wall


@dataclass(frozen=True)
class GlanceThreshold:
    """One declared MD-12 label with its resolved raw parameter value."""

    label: str
    value: float


@dataclass(frozen=True)
class GlanceReading:
    """One monitor's current primary readout in stored units, unformatted.

    Raw values are the contract (MD-12/UI-17): a consumer may format them, but
    `aggregate` is `max|min` exactly as declared, or `last` for the fixed UI-18
    ingest readout that no definition can declare.
    """

    monitor: str
    entity_id: str
    metric: str
    value: float
    unit: str
    aggregate: str
    thresholds: tuple[GlanceThreshold, ...]


@dataclass(frozen=True)
class GlanceBatch:
    """A bounded, monitor-name-ordered set of readouts plus its MC-01 metadata."""

    readings: tuple[GlanceReading, ...]
    matched: int
    truncated: bool

    @property
    def returned(self) -> int:
        return len(self.readings)


def limits() -> dict[str, int]:
    """Current glance bounds (reads the constant so tests can patch it)."""
    return {"max_glances": MAX_GLANCES}


def _exempt(mdef, q: Query, sample: GlanceSample, now: float) -> bool:
    """CA-07: an excluded entity may never win a readout it cannot own."""
    return any(
        expression.eval(StoredEntityCtx(
            query=q,
            monitor=mdef.name,
            entity_id=sample.entity_id,
            attrs=sample.attrs,
            params=mdef.parameters,
            wall=now,
        )) is True
        for expression in mdef.exempt
    )


def declared_reading(mdef, q: Query | None, state: str, now: float) -> GlanceReading | None:
    """Reduce fresh active samples with the declared aggregate (MD-12/UI-17)."""
    if q is None or mdef.glance is None or state not in GLANCE_STATES:
        return None
    samples = q.glance_samples(
        mdef.name,
        mdef.glance.metric,
        not_before=now - 2 * mdef.interval_s,
    )
    eligible = [sample for sample in samples if not _exempt(mdef, q, sample, now)]
    if not eligible:
        return None
    if mdef.glance.aggregate == "max":
        sample = min(eligible, key=lambda item: (-item.value, -item.ts, item.entity_id))
    else:
        sample = min(eligible, key=lambda item: (item.value, -item.ts, item.entity_id))
    return GlanceReading(
        monitor=mdef.name,
        entity_id=sample.entity_id,
        metric=mdef.glance.metric,
        value=sample.value,
        unit=mdef.glance.unit,
        aggregate=mdef.glance.aggregate,
        thresholds=tuple(
            GlanceThreshold(
                label=threshold.label,
                value=float(mdef.parameters[threshold.parameter]),
            )
            for threshold in mdef.glance.thresholds
        ),
    )


def events_reading(mdef, q: Query | None, state: str, now: float) -> GlanceReading | None:
    """UI-18: the Events source has no sampled entity, so read self ingest rate."""
    if q is None or mdef.source != "events" or state not in GLANCE_STATES:
        return None
    samples = q.glance_samples(
        "self", EVENT_RATE_METRIC, not_before=now - _EVENT_RATE_WINDOW_S
    )
    if not samples:
        return None
    latest = max(samples, key=lambda sample: sample.ts)
    return GlanceReading(
        monitor=mdef.name,
        entity_id="ingest",
        metric=EVENT_RATE_METRIC,
        value=latest.value,
        unit="events/min",
        # Response-level only: MD-12 TOML still accepts max|min alone, since a
        # definition cannot declare this fixed operational readout.
        aggregate="last",
        thresholds=(),
    )


def reading(mdef, q: Query | None, state: str, now: float) -> GlanceReading | None:
    """Declared readout when there is one, else the fixed Events fallback."""
    declared = declared_reading(mdef, q, state, now)
    if declared is not None:
        return declared
    return events_reading(mdef, q, state, now)


def collect(pairs, q: Query | None, now: float) -> GlanceBatch:
    """Compose readouts for `(mdef, state)` pairs, ordered by name and capped."""
    readings = [
        found
        for mdef, state in pairs
        if (found := reading(mdef, q, state, now)) is not None
    ]
    readings.sort(key=lambda item: item.monitor)
    return GlanceBatch(
        readings=tuple(readings[:MAX_GLANCES]),
        matched=len(readings),
        truncated=len(readings) > MAX_GLANCES,
    )


def monitor_glances(defs, q: Query | None, *, stale: bool, now: float) -> GlanceBatch:
    """Bounded readouts for loaded definitions, gated by UI-14 state.

    Consumers that render health themselves (the dashboard) call `health_state`
    and `reading` per monitor; this is the one-call path for consumers that
    only want the readouts (MC-01 `get_status`).
    """
    live = open_incidents_by_monitor(q)
    pairs = []
    for mdef in defs:
        severities = [row["severity"] for row in live.get(mdef.name, [])]
        pairs.append((mdef, health_state(
            stale=stale,
            has_evidence=has_evidence(q, mdef),
            enabled=mdef.enabled,
            max_severity=max(severities, default=None),
        )))
    return collect(pairs, q, now)

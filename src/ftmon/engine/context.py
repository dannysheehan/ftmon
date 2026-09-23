"""EvalContext implementation binding one entity's data for one evaluation.

A fresh, immutable-per-evaluation view rather than a live object: expression
evaluation must be deterministic given (windows, attrs, params, clock) per
EX-03, so everything time-dependent is captured as the tick's wall timestamp,
not read live.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ftmon.engine.rings import RingStore


def _no_baseline(monitor: str, entity_id: str, metric: str) -> float | None:
    """M1 default: baselines arrive in M2 (CA-05); returning None keeps
    baseline-relative rules silent via EX-06 rather than wrong."""
    return None


@dataclass(frozen=True)
class EntityCtx:
    rings: RingStore
    monitor: str
    entity_id: str
    attrs: Mapping[str, str]
    params: Mapping[str, float]
    wall: float
    baseline_lookup: Callable[[str, str, str], float | None] = field(default=_no_baseline)
    # A completed external observation is authoritative about absent inputs;
    # retained windows must not turn those absences into current evidence (EC-11).
    unavailable_metrics: frozenset[str] = frozenset()

    def metric_last(self, m: str) -> float | None:
        if m in self.unavailable_metrics:
            return None
        return self.rings.last(self.monitor, self.entity_id, m)

    def metric_last_ts(self, m: str) -> float | None:
        if m in self.unavailable_metrics:
            return None
        return self.rings.last_ts(self.monitor, self.entity_id, m)

    def metric_window(self, m: str, seconds: float) -> list[tuple[float, float]] | None:
        if m in self.unavailable_metrics:
            # A missing current input is not a cold but valid window: coverage
            # of an empty valid window is zero, which could falsely clear an alert.
            return None
        return self.rings.window(self.monitor, self.entity_id, m, self.wall - seconds)

    def attr(self, a: str) -> str | None:
        return self.attrs.get(a)

    def param(self, p: str) -> float:
        return self.params[p]

    def baseline(self, m: str) -> float | None:
        if m in self.unavailable_metrics:
            return None
        return self.baseline_lookup(self.monitor, self.entity_id, m)

    def now(self) -> float:
        return self.wall

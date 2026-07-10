"""Shared test fixtures: FakeCtx implements expr.EvalContext over dict data."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeCtx:
    """EvalContext over literal data. series: metric -> [(ts, value), ...]."""

    series: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    wall: float = 1_700_000_000.0
    counters: dict[str, int] = field(default_factory=dict)

    def metric_last(self, m):
        pts = self.series.get(m)
        return pts[-1][1] if pts else None

    def metric_last_ts(self, m):
        pts = self.series.get(m)
        return pts[-1][0] if pts else None

    def metric_window(self, m, seconds):
        pts = self.series.get(m, [])
        cutoff = self.wall - seconds
        return [p for p in pts if p[0] >= cutoff]

    def attr(self, a):
        return self.attrs.get(a)

    def param(self, p):
        return self.params[p]

    def baseline(self, m):
        return self.baselines.get(m)

    def now(self):
        return self.wall

    def count(self, name):
        self.counters[name] = self.counters.get(name, 0) + 1

"""In-memory sample windows for expression evaluation (CA-04).

Rings exist because series functions (slope/monot/avg...) need recent points
at every cycle and hitting SQLite per evaluation would violate the resource
budget (RB-01). Capacity is derived from what loaded definitions actually
reference: a metric appearing in a `slope(m, "45m")` on a 60s monitor gets
ceil(2700/60)+2 slots, a metric only read via bare name gets 2. This is also
what makes SA-05's "short window for every process" emerge for free — the
leak monitor's promotion expression references a 15m window, so every process
entity carries ~15 samples in memory and nothing else.

Memory is bounded (default 64 MB): on breach, the least-recently-updated
unprotected entities are evicted whole (protection = watchlist/promoted,
decided by the caller) and a counter fires so the self monitor can report it.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping

_ENTRY_BYTES = 48  # rough per-point cost: tuple + two floats + deque slot


class RingStore:
    def __init__(self, max_bytes: int = 64 * 2**20):
        self._max_bytes = max_bytes
        # capacities per (monitor, metric); entities of one monitor share them
        self._caps: dict[tuple[str, str], int] = {}
        self._intervals: dict[str, float] = {}
        # (monitor, entity_id) -> metric -> deque[(ts, value)]
        self._data: dict[tuple[str, str], dict[str, deque]] = {}
        self._touched: dict[tuple[str, str], float] = {}  # for LRU eviction
        self._entries = 0

    def configure(self, monitor: str, interval_s: float, windows: Mapping[str, float]) -> None:
        """Size rings for one monitor from its referenced windows. Reconfigure
        on definition reload drops that monitor's buffers — a changed rule
        must not inherit a window shaped for its previous self (MD-06)."""
        self._intervals[monitor] = interval_s
        for key in [k for k in self._caps if k[0] == monitor]:
            del self._caps[key]
        for metric, window_s in windows.items():
            self._caps[(monitor, metric)] = int(math.ceil(window_s / interval_s)) + 2
        self.forget_monitor(monitor)

    def append(self, monitor: str, entity_id: str, metric: str, ts: float, value: float) -> None:
        if not (isinstance(value, (int, float)) and math.isfinite(value)):
            return  # DM-01 applies in memory too: NaN/inf never enters a window
        key = (monitor, entity_id)
        series = self._data.setdefault(key, {})
        buf = series.get(metric)
        if buf is None:
            cap = self._caps.get((monitor, metric), 2)
            buf = series[metric] = deque(maxlen=cap)
        if len(buf) == buf.maxlen:
            self._entries -= 1
        buf.append((ts, float(value)))
        self._entries += 1
        self._touched[key] = ts

    def last(self, monitor: str, entity_id: str, metric: str) -> float | None:
        buf = self._data.get((monitor, entity_id), {}).get(metric)
        return buf[-1][1] if buf else None

    def last_ts(self, monitor: str, entity_id: str, metric: str) -> float | None:
        buf = self._data.get((monitor, entity_id), {}).get(metric)
        return buf[-1][0] if buf else None

    def window(
        self, monitor: str, entity_id: str, metric: str, since_ts: float
    ) -> list[tuple[float, float]]:
        buf = self._data.get((monitor, entity_id), {}).get(metric)
        if not buf:
            return []
        return [p for p in buf if p[0] >= since_ts]

    def forget_entity(self, monitor: str, entity_id: str) -> None:
        series = self._data.pop((monitor, entity_id), None)
        if series:
            self._entries -= sum(len(b) for b in series.values())
        self._touched.pop((monitor, entity_id), None)

    def forget_monitor(self, monitor: str) -> None:
        for key in [k for k in self._data if k[0] == monitor]:
            self.forget_entity(*key)

    def mem_bytes(self) -> int:
        return self._entries * _ENTRY_BYTES

    def evict_if_over(
        self, protected: Callable[[str, str], bool], counter: Callable[[str], None]
    ) -> int:
        """CA-04 cap enforcement: evict LRU unprotected entities whole until
        under budget. Whole entities, not single metrics — a partial window
        would silently corrupt slope/monot results."""
        evicted = 0
        if self.mem_bytes() <= self._max_bytes:
            return 0
        for key, _ts in sorted(self._touched.items(), key=lambda kv: kv[1]):
            if self.mem_bytes() <= self._max_bytes:
                break
            if protected(*key):
                continue
            self.forget_entity(*key)
            counter("ring_evictions")
            evicted += 1
        return evicted

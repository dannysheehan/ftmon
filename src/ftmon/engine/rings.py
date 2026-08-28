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
import sys
from collections import deque
from collections.abc import Callable, Mapping

# A retained point owns one tuple and two numeric objects. Deque block storage
# is charged from the deque's actual shallow size as it grows, rather than
# hidden in a guessed per-slot constant. These values therefore follow the
# active Python build instead of assuming one CPython release's object layout.
_POINT_BYTES = sys.getsizeof((0.0, 0.0)) + 2 * sys.getsizeof(0.0)
_ENTITY_KEY_BYTES = sys.getsizeof(("", ""))


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
        # Actual shallow container sizes plus charged point/key objects. This
        # stays O(1) to read: ring_mem_bytes is sampled every tick, and walking
        # tens of thousands of short per-process deques would itself work
        # against RB-01. Definition containers are small and measured in
        # `_definition_bytes` so reload deletion retains honest dict capacity.
        self._data_bytes = sys.getsizeof(self._data) + sys.getsizeof(self._touched)

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
        series = self._data.get(key)
        new_entity = series is None
        if series is None:
            before = sys.getsizeof(self._data)
            series = self._data[key] = {}
            self._data_bytes += (
                sys.getsizeof(self._data) - before
                + _ENTITY_KEY_BYTES
                + sys.getsizeof(series)
            )
        buf = series.get(metric)
        if buf is None:
            cap = self._caps.get((monitor, metric), 2)
            before = sys.getsizeof(series)
            buf = series[metric] = deque(maxlen=cap)
            self._data_bytes += sys.getsizeof(series) - before + sys.getsizeof(buf)
        was_full = len(buf) == buf.maxlen
        before = sys.getsizeof(buf)
        buf.append((ts, float(value)))
        self._data_bytes += sys.getsizeof(buf) - before
        if not was_full:
            self._entries += 1
            self._data_bytes += _POINT_BYTES
        if new_entity:
            before = sys.getsizeof(self._touched)
            self._touched[key] = ts
            self._data_bytes += sys.getsizeof(self._touched) - before
        else:
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
        key = (monitor, entity_id)
        before = sys.getsizeof(self._data)
        series = self._data.pop(key, None)
        self._data_bytes += sys.getsizeof(self._data) - before
        if series is not None:
            entries = sum(len(buf) for buf in series.values())
            self._entries -= entries
            self._data_bytes -= (
                _ENTITY_KEY_BYTES
                + sys.getsizeof(series)
                + sum(sys.getsizeof(buf) for buf in series.values())
                + entries * _POINT_BYTES
            )
        before = sys.getsizeof(self._touched)
        self._touched.pop((monitor, entity_id), None)
        self._data_bytes += sys.getsizeof(self._touched) - before

    def forget_monitor(self, monitor: str) -> None:
        for key in [k for k in self._data if k[0] == monitor]:
            self.forget_entity(*key)

    def mem_bytes(self) -> int:
        """Return the CA-04 charge consumed by both reporting and eviction.

        The charge includes actual container allocation and a conservative
        interpreter-sized point cost. Strings supplied by definitions and
        samplers are shared with other daemon state, so charging them here
        would make ring_mem_bytes double-count memory the rings do not own.
        """
        return (
            sys.getsizeof(self)
            + sys.getsizeof(self.__dict__)
            + sys.getsizeof(self._max_bytes)
            + sys.getsizeof(self._entries)
            + sys.getsizeof(self._data_bytes)
            + self._data_bytes
            + self._definition_bytes()
        )

    def _definition_bytes(self) -> int:
        """Charge the small reloadable capacity maps without per-tick data walks."""
        return (
            sys.getsizeof(self._caps)
            + sum(sys.getsizeof(key) + sys.getsizeof(value) for key, value in self._caps.items())
            + sys.getsizeof(self._intervals)
            + sum(sys.getsizeof(value) for value in self._intervals.values())
        )

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

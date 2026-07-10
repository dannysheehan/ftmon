"""Tick loop and per-monitor due-time bookkeeping (SA-01, SA-07).

All scheduling arithmetic uses the monotonic clock; wall time is read once
per tick and stamped on everything downstream — this is what keeps NTP jumps
and suspend/resume from double-firing or starving monitors. Missed work is
skipped, never queued: after a laptop lid-close there is nothing useful about
running 200 catch-up cycles against a world that no longer exists.
"""

from __future__ import annotations

from collections.abc import Callable

from ftmon.clock import Clock


class DueTable:
    """Per-monitor next-due tracking with overrun-skip semantics (SA-01)."""

    def __init__(self) -> None:
        self._due: dict[str, tuple[float, float]] = {}  # name -> (next_due_mono, interval_s)

    def add(self, name: str, interval_s: float, mono_now: float) -> None:
        # first run is immediate: a freshly loaded monitor should not sit
        # idle for a full interval before producing its first sample
        self._due[name] = (mono_now, interval_s)

    def remove(self, name: str) -> None:
        self._due.pop(name, None)

    def names(self) -> list[str]:
        return list(self._due)

    def due(self, mono_now: float, overrun_counter: Callable[[str], None]) -> list[str]:
        """Monitors due at mono_now; advances their schedules. A monitor more
        than one interval behind skips the missed slots (counted, SA-01) —
        it must not burst to catch up."""
        ready = []
        for name, (next_due, interval) in list(self._due.items()):
            if next_due > mono_now:
                continue
            ready.append(name)
            next_due += interval
            while next_due <= mono_now:
                next_due += interval
                overrun_counter(name)
            self._due[name] = (next_due, interval)
        return ready


class Scheduler:
    """Drives on_tick at a fixed monotonic cadence, flagging clock gaps."""

    def __init__(self, clock: Clock, tick_s: float = 5.0):
        self._clock = clock
        self.tick_s = tick_s

    def run(
        self,
        on_tick: Callable[[float, float, float], None],  # (wall, mono, gap_s)
        should_stop: Callable[[], bool],
    ) -> None:
        next_tick = self._clock.monotonic() + self.tick_s
        while not should_stop():
            self._clock.sleep_until(next_tick)
            if should_stop():  # a stop during sleep must not run one more tick
                return
            mono = self._clock.monotonic()
            wall = self._clock.now()
            # SA-07: waking far past the deadline means suspend/stall; report
            # the gap so the daemon can emit a clock_gap self-event, and
            # re-anchor instead of replaying missed ticks.
            gap_s = mono - next_tick
            on_tick(wall, mono, gap_s if gap_s > 2 * self.tick_s else 0.0)
            next_tick += self.tick_s
            if next_tick <= mono:
                next_tick = mono + self.tick_s

"""Process sampler: per-process CPU, memory, fds, threads, IO (SA-04).

Two constraints shape this module:

- psutil's ``cpu_percent(None)`` measures CPU since the previous call *on the
  same Process object*, so a cache of Process objects must survive across
  samples; the cache key includes create_time because a recycled PID must not
  inherit the old process's CPU-measurement state (DM-02).
- Any psutil read can raise (process exited, permission denied). Failures are
  isolated per entity and per metric (PL-03): a vanished process is skipped, a
  denied metric is omitted, and nothing short of an interpreter error aborts
  the sampling pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ClassVar

import psutil

from ftmon.clock import Clock
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

_PROC_ERRORS = (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError)

_CMDLINE_MAX = 256  # SE-04


def _opt(read: Callable[[], object]) -> object | None:
    """One optional psutil read: value, or None if this process denies it."""
    try:
        return read()
    except _PROC_ERRORS:
        return None


class ProcessSampler:
    """Samples every visible process; selection/promotion happens downstream
    in the engine (SA-05), never here — history for exempt or unselected
    entities must still exist so it can be queried later (CA-07)."""

    decl: ClassVar[SourceDecl] = SOURCE_DECLS["process"]

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        # (pid, create_time) -> Process; see module docstring for why.
        self._procs: dict[tuple[int, int], psutil.Process] = {}

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        entities: list[EntitySample] = []
        live: set[tuple[int, int]] = set()
        for proc in psutil.process_iter([]):
            # Deadline sits between entities because a stuck native call
            # cannot be interrupted in-process (SA-02).
            if self._clock.monotonic() > deadline_mono:
                break
            entity = self._sample_one(proc, live)
            if entity is not None:
                entities.append(entity)
        # Drop cache entries for processes gone this pass; on an early
        # deadline break unseen entries survive one extra round, which is
        # harmless and avoids losing cpu_percent state on slow passes.
        if not live:
            self._procs.clear()
        else:
            self._procs = {k: v for k, v in self._procs.items() if k in live}
        return Snapshot(source=self.decl.name, ts=now, entities=tuple(entities))

    def _sample_one(
        self, proc: psutil.Process, live: set[tuple[int, int]]
    ) -> EntitySample | None:
        try:
            create_time = int(proc.create_time())
            name = proc.name()
        except _PROC_ERRORS:
            return None  # can't even identify it: skip entity (PL-03)

        key = (proc.pid, create_time)
        cached = self._procs.setdefault(key, proc)
        live.add(key)

        attrs: dict[str, str] = {"name": name}
        cmdline = _opt(lambda: " ".join(proc.cmdline()))
        if cmdline:
            attrs["cmdline"] = str(cmdline)[:_CMDLINE_MAX]
        for attr_name, read in (
            ("username", proc.username),
            ("exe", proc.exe),
        ):
            value = _opt(read)
            if value:
                attrs[attr_name] = str(value)

        metrics: dict[str, float] = {}
        # cpu_percent must go through the cached object (module docstring).
        cpu = _opt(lambda: cached.cpu_percent(None))
        if cpu is not None:
            metrics["cpu_pct"] = float(cpu)  # type: ignore[arg-type]
        rss = _opt(lambda: proc.memory_info().rss)
        if rss is not None:
            metrics["rss_bytes"] = float(rss)  # type: ignore[arg-type]
        for metric_name, read in (
            ("num_fds", proc.num_fds),
            ("num_threads", proc.num_threads),
        ):
            value = _opt(read)
            if value is not None:
                metrics[metric_name] = float(value)  # type: ignore[arg-type]
        io = _opt(proc.io_counters)
        if io is not None:
            metrics["io_read_bytes"] = float(io.read_bytes)  # type: ignore[union-attr]
            metrics["io_write_bytes"] = float(io.write_bytes)  # type: ignore[union-attr]

        return EntitySample(
            entity_id=f"{name}:{proc.pid}:{create_time}", attrs=attrs, metrics=metrics
        )

"""System-wide sampler: load, CPU, memory, PSI metrics [SA-04, SA-05].

Single entity per system. Samples load average, CPU percent, memory
metrics, and optional PSI pressure stall information when available.
"""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from typing import ClassVar

import psutil

from ftmon.clock import Clock
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


def parse_psi(text: str) -> float | None:
    """Parse PSI avg60 value from a /proc/pressure/ line.

    Expected format: "some avg10=X avg60=Y avg300=Z total=N"
    Returns the avg60 value, or None if unparseable.
    """
    if not text:
        return None
    match = re.search(r"avg60=([0-9.]+)", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


class SystemSampler:
    """Samples system-wide metrics: load, CPU, memory, PSI.

    Single entity identified as "system". Metrics include:
    - load1, load5, load15: load averages
    - cpu_pct: total system CPU utilization
    - mem_total_bytes, mem_available_bytes, mem_used_bytes: memory
    - swap_used_pct: swap utilization
    - psi_some_cpu, psi_some_mem, psi_some_io: PSI avg60 metrics
      (omitted if /proc/pressure not available)
    """

    decl: ClassVar[SourceDecl] = SOURCE_DECLS["system"]

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def sample(
        self, now: float, deadline_mono: float, options: Mapping
    ) -> Snapshot:
        """Sample system metrics into a single entity snapshot."""
        attrs = {"hostname": socket.gethostname()}

        # Get load averages
        load1, load5, load15 = os.getloadavg()

        # Get CPU percent (cached: rate since last call)
        cpu_pct = psutil.cpu_percent(None)

        # Get memory info
        mem = psutil.virtual_memory()
        mem_total = mem.total
        mem_available = mem.available
        mem_used = mem.used

        # Get swap info
        swap = psutil.swap_memory()
        swap_used_pct = swap.percent

        # Build metrics dict
        metrics: dict[str, float] = {
            "load1": load1,
            "load5": load5,
            "load15": load15,
            "cpu_pct": cpu_pct,
            "mem_total_bytes": float(mem_total),
            "mem_available_bytes": float(mem_available),
            "mem_used_bytes": float(mem_used),
            "swap_used_pct": swap_used_pct,
        }

        # Parse PSI metrics if available
        psi_metrics = self._read_psi()
        metrics.update(psi_metrics)

        entity = EntitySample(
            entity_id="system", attrs=attrs, metrics=metrics
        )
        return Snapshot(source=self.decl.name, ts=now, entities=(entity,))

    def _read_psi(self) -> dict[str, float]:
        """Read PSI metrics from /proc/pressure if available."""
        psi_metrics: dict[str, float] = {}
        psi_files = {
            "psi_some_cpu": "/proc/pressure/cpu",
            "psi_some_mem": "/proc/pressure/memory",
            "psi_some_io": "/proc/pressure/io",
        }

        for metric_name, path in psi_files.items():
            try:
                with open(path) as f:
                    for line in f:
                        if line.startswith("some "):
                            value = parse_psi(line)
                            if value is not None:
                                psi_metrics[metric_name] = value
                            break
            except (FileNotFoundError, OSError):
                # PSI not available on this system
                pass

        return psi_metrics

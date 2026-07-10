"""Disk/mount sampler: space, usage, inodes [SA-04, SA-05].

Samples filesystem metrics per mount point: total/used/free space,
usage percentage, and inode utilization (when available).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import ClassVar

import psutil

from ftmon.clock import Clock
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


class DiskSampler:
    """Samples disk/mount metrics: space, usage, inodes.

    One entity per mount point (identified by mountpoint path).
    Metrics:
    - total_bytes, used_bytes, free_bytes: disk space
    - used_pct: percentage used
    - inode_used_pct: inode usage (omitted where unsupported or f_files==0)

    Attributes:
    - fstype: filesystem type (e.g., ext4)
    - device: backing device path
    """

    decl: ClassVar[SourceDecl] = SOURCE_DECLS["disk"]

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def sample(
        self, now: float, deadline_mono: float, options: Mapping
    ) -> Snapshot:
        """Sample disk metrics for all mounted filesystems."""
        entities: list[EntitySample] = []

        partitions = psutil.disk_partitions(all=False)
        for partition in partitions:
            # Check deadline cooperatively
            if self._clock.monotonic() > deadline_mono:
                break

            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError, FileNotFoundError):
                # Skip inaccessible mountpoints [PL-03]
                continue

            attrs = {"fstype": partition.fstype, "device": partition.device}

            metrics: dict[str, float] = {
                "total_bytes": float(usage.total),
                "used_bytes": float(usage.used),
                "free_bytes": float(usage.free),
                "used_pct": float(usage.percent),
            }

            # Try to get inode usage
            inode_pct = self._get_inode_usage_pct(partition.mountpoint)
            if inode_pct is not None:
                metrics["inode_used_pct"] = inode_pct

            entity = EntitySample(
                entity_id=partition.mountpoint,
                attrs=attrs,
                metrics=metrics,
            )
            entities.append(entity)

        return Snapshot(source=self.decl.name, ts=now, entities=tuple(entities))

    def _get_inode_usage_pct(self, mountpoint: str) -> float | None:
        """Calculate inode usage percentage for a mount.

        Uses os.statvfs to get inode counts. Returns None if:
        - os.statvfs fails
        - f_files is 0 (filesystem doesn't support inode counting)
        """
        try:
            stat = os.statvfs(mountpoint)
            if stat.f_files == 0:
                return None
            used = stat.f_files - stat.f_ffree
            return 100.0 * used / stat.f_files
        except (OSError, PermissionError, ValueError):
            return None

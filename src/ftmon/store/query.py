"""Read-side query layer shared by CLI/MCP/web (DESIGN.md section 12, DM-06).

Callers pass `now`/`start`/`end` in; nothing here reads a clock (TS-03).
Connections used here should be opened readonly (`db.connect(path, readonly=True)`).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

__all__ = ["SeriesPoint", "SeriesResult", "Query", "lttb"]

_RAW_RECENT_S = 48 * 3600
_RAW_MAX_SPAN_S = 12 * 3600
_ROLLUP5M_MAX_SPAN_S = 30 * 86400


@dataclass(frozen=True)
class SeriesPoint:
    ts: int
    value: float


@dataclass(frozen=True)
class SeriesResult:
    monitor: str
    metric: str
    entity_id: str
    resolution: str  # "raw" | "5m" | "1h"
    points: list[SeriesPoint]


def lttb(points: list[SeriesPoint], n: int) -> list[SeriesPoint]:
    """Largest-Triangle-Three-Buckets downsampling to (at most) n points.

    Classic algorithm (Sveinn Steinarsson). Always preserves the first and
    last point. If `points` already has <= n points, it is returned as-is.
    """
    if n >= len(points):
        return list(points)
    if n <= 2:
        return [points[0], points[-1]] if points else []

    sampled = [points[0]]
    every = (len(points) - 2) / (n - 2)
    a = 0  # index of the previously-selected point

    for i in range(n - 2):
        # Range of the "next" bucket, averaged for the area calculation.
        avg_range_start = math.floor((i + 1) * every) + 1
        avg_range_end = math.floor((i + 2) * every) + 1
        avg_range_end = min(avg_range_end, len(points))
        avg_range = points[avg_range_start:avg_range_end] or [points[-1]]
        avg_x = sum(p.ts for p in avg_range) / len(avg_range)
        avg_y = sum(p.value for p in avg_range) / len(avg_range)

        # Range of the current bucket, from which we pick the point that
        # forms the largest triangle with point `a` and the next bucket's
        # average point.
        bucket_start = math.floor(i * every) + 1
        bucket_end = math.floor((i + 1) * every) + 1

        point_a = points[a]
        max_area = -1.0
        max_area_idx = bucket_start
        for idx in range(bucket_start, bucket_end):
            point = points[idx]
            area = abs(
                (point_a.ts - avg_x) * (point.value - point_a.value)
                - (point_a.ts - point.ts) * (avg_y - point_a.value)
            )
            if area > max_area:
                max_area = area
                max_area_idx = idx

        sampled.append(points[max_area_idx])
        a = max_area_idx

    sampled.append(points[-1])
    return sampled


class Query:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _resolution(self, now: float, start: float, end: float) -> str:
        span = end - start
        if end > now - _RAW_RECENT_S and span <= _RAW_MAX_SPAN_S:
            return "raw"
        if span <= _ROLLUP5M_MAX_SPAN_S:
            return "5m"
        return "1h"

    def series(
        self,
        monitor: str,
        metric: str,
        *,
        now: float,
        start: float,
        end: float,
        entity_id: str | None = None,
        max_points: int = 2000,
    ) -> list[SeriesResult]:
        sql = "SELECT id, entity_id FROM series WHERE monitor=? AND metric=?"
        params: list[object] = [monitor, metric]
        if entity_id is not None:
            sql += " AND entity_id=?"
            params.append(entity_id)
        series_rows = self._conn.execute(sql, params).fetchall()

        resolution = self._resolution(now, start, end)
        istart, iend = round(start), round(end)

        results = []
        for row in series_rows:
            sid = row["id"]
            if resolution == "raw":
                rows = self._conn.execute(
                    "SELECT ts, value FROM samples "
                    "WHERE series_id=? AND ts>=? AND ts<=? ORDER BY ts",
                    (sid, istart, iend),
                ).fetchall()
                points = [SeriesPoint(ts=r["ts"], value=r["value"]) for r in rows]
            else:
                table = "rollup5m" if resolution == "5m" else "rollup1h"
                rows = self._conn.execute(
                    f"SELECT bucket, avg FROM {table} "  # noqa: S608 - table is a fixed literal
                    "WHERE series_id=? AND bucket>=? AND bucket<=? ORDER BY bucket",
                    (sid, istart, iend),
                ).fetchall()
                points = [
                    SeriesPoint(ts=r["bucket"], value=r["avg"])
                    for r in rows
                    if r["avg"] is not None
                ]

            if len(points) > max_points:
                points = lttb(points, max_points)

            results.append(
                SeriesResult(
                    monitor=monitor,
                    metric=metric,
                    entity_id=row["entity_id"],
                    resolution=resolution,
                    points=points,
                )
            )
        return results

    def entities(self, monitor: str, *, alive_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM entities WHERE monitor=?"
        params: list[object] = [monitor]
        if alive_only:
            sql += " AND gone_ts IS NULL"
        sql += " ORDER BY entity_id"
        return self._conn.execute(sql, params).fetchall()

    def events(
        self,
        *,
        start: float,
        end: float,
        min_severity: int = 0,
        provider: str | None = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM events WHERE ts>=? AND ts<=? AND severity>=?"
        params: list[object] = [round(start), round(end), min_severity]
        if provider is not None:
            sql += " AND provider=?"
            params.append(provider)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    def incidents(
        self,
        *,
        state: str | None = None,
        monitor: str | None = None,
        since: float | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM incidents WHERE 1=1"
        params: list[object] = []
        if state is not None:
            sql += " AND state=?"
            params.append(state)
        if monitor is not None:
            sql += " AND monitor=?"
            params.append(monitor)
        if since is not None:
            sql += " AND last_change_ts>=?"
            params.append(round(since))
        sql += " ORDER BY last_change_ts DESC"
        return self._conn.execute(sql, params).fetchall()

    def cursor(self, source: str) -> str | None:
        """DM-15: read back a source's persisted cursor (companion to
        writer.TickWriter.set_cursor); None if never set."""
        row = self._conn.execute(
            "SELECT cursor FROM cursors WHERE source=?", (source,)
        ).fetchone()
        return row["cursor"] if row is not None else None

    def status(self, *, now: float) -> dict:
        row = self._conn.execute("SELECT value FROM meta WHERE key='last_tick_ts'").fetchone()
        last_tick_ts = float(row["value"]) if row is not None else None
        last_tick_age_s = (now - last_tick_ts) if last_tick_ts is not None else None

        (page_count,) = self._conn.execute("PRAGMA page_count").fetchone()
        (page_size,) = self._conn.execute("PRAGMA page_size").fetchone()

        (open_incidents,) = self._conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE state != 'cleared'"
        ).fetchone()

        return {
            "last_tick_ts": last_tick_ts,
            "last_tick_age_s": last_tick_age_s,
            "db_bytes": page_count * page_size,
            "open_incidents": open_incidents,
        }

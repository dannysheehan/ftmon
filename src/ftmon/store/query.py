"""Read-side query layer shared by CLI/MCP/web (DESIGN.md section 12, DM-06).

Callers pass `now`/`start`/`end` in; nothing here reads a clock (TS-03).
Connections used here should be opened readonly (`db.connect(path, readonly=True)`).
"""

from __future__ import annotations

import json
import math
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from dataclasses import dataclass
from types import SimpleNamespace

__all__ = [
    "BaselineHistory", "BaselinePage", "BaselineRecord", "GlanceSample", "SeriesPoint",
    "SeriesResult",
    "IncidentDetail", "IncidentHistoryEntry", "Query", "lttb",
]

_RAW_RECENT_S = 48 * 3600
_RAW_MAX_SPAN_S = 12 * 3600
_ROLLUP5M_MAX_SPAN_S = 30 * 86400
_ROLLUP5M_S = 300
_BASELINE_MIN_UPDATES = 240
_BASELINE_DEFAULT_LIMIT = 100
_BASELINE_MAX_LIMIT = 500


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
    statistic: str = "avg"
    lower: list[SeriesPoint] | None = None
    upper: list[SeriesPoint] | None = None
    downsampled: bool = False


@dataclass(frozen=True)
class GlanceSample:
    entity_id: str
    ts: int
    value: float
    attrs: dict[str, str]


@dataclass(frozen=True)
class BaselineRecord:
    monitor: str
    entity_id: str
    metric: str
    level: float
    updates: int
    required_updates: int
    coverage: float
    ready: bool
    updated_at: int
    half_life_s: float


@dataclass(frozen=True)
class BaselineHistory:
    baseline: BaselineRecord
    points: tuple[SeriesPoint, ...]
    history_truncated: bool


@dataclass(frozen=True)
class BaselinePage:
    baselines: tuple[BaselineRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class IncidentHistoryEntry:
    seq: int
    ts: int
    kind: str
    detail: dict


@dataclass(frozen=True)
class IncidentDetail:
    incident: dict
    history: tuple[IncidentHistoryEntry, ...]


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

    def glance_samples(
        self, monitor: str, metric: str, *, not_before: float
    ) -> tuple[GlanceSample, ...]:
        """Return latest current values for active entities (UI-17).

        The correlated MAX uses the existing samples primary key. Selecting
        latest-per-entity here lets presentation apply the definition's exempt
        expressions before its declared aggregate chooses a winner.
        """
        rows = self._conn.execute(
            "SELECT se.entity_id, sa.ts, sa.value, en.attrs FROM series se "
            "JOIN entities en ON en.monitor=se.monitor AND en.entity_id=se.entity_id "
            "AND en.gone_ts IS NULL "
            "JOIN samples sa ON sa.series_id=se.id "
            "AND sa.ts=(SELECT MAX(latest.ts) FROM samples latest "
            "WHERE latest.series_id=se.id) "
            "WHERE se.monitor=? AND se.metric=? AND sa.ts>=? "
            "ORDER BY se.entity_id",
            (monitor, metric, not_before),
        ).fetchall()
        return tuple(
            GlanceSample(
                entity_id=row["entity_id"],
                ts=int(row["ts"]),
                value=float(row["value"]),
                attrs=json.loads(row["attrs"]) if row["attrs"] else {},
            )
            for row in rows
        )

    def entity_metric_last(
        self, monitor: str, entity_id: str, metric: str
    ) -> SeriesPoint | None:
        """Return one persisted raw value for read-side expression context."""
        row = self._conn.execute(
            "SELECT sa.ts,sa.value FROM series se JOIN samples sa ON sa.series_id=se.id "
            "WHERE se.monitor=? AND se.entity_id=? AND se.metric=? "
            "ORDER BY sa.ts DESC LIMIT 1",
            (monitor, entity_id, metric),
        ).fetchone()
        return None if row is None else SeriesPoint(int(row["ts"]), float(row["value"]))

    def entity_metric_window(
        self, monitor: str, entity_id: str, metric: str, *, start: float
    ) -> list[tuple[float, float]]:
        """Return bounded raw evidence for read-side exemption evaluation."""
        rows = self._conn.execute(
            "SELECT sa.ts,sa.value FROM series se JOIN samples sa ON sa.series_id=se.id "
            "WHERE se.monitor=? AND se.entity_id=? AND se.metric=? AND sa.ts>=? "
            "ORDER BY sa.ts LIMIT 10000",
            (monitor, entity_id, metric, start),
        ).fetchall()
        return [(float(row["ts"]), float(row["value"])) for row in rows]

    @staticmethod
    def _baseline_record(row: sqlite3.Row) -> BaselineRecord:
        updates = int(row["updates"])
        return BaselineRecord(
            monitor=row["monitor"],
            entity_id=row["entity_id"],
            metric=row["metric"],
            level=float(row["value"]),
            updates=updates,
            required_updates=_BASELINE_MIN_UPDATES,
            coverage=min(updates / _BASELINE_MIN_UPDATES, 1.0),
            ready=updates >= _BASELINE_MIN_UPDATES,
            updated_at=int(row["updated_bucket"]),
            half_life_s=float(row["half_life_s"]),
        )

    def current_baseline(
        self, monitor: str, entity_id: str, metric: str
    ) -> BaselineRecord | None:
        """Return learned state even before it is ready for rule evaluation."""
        row = self._conn.execute(
            "SELECT s.monitor, s.entity_id, s.metric, b.value, b.updates, "
            "b.updated_bucket, b.half_life_s FROM baselines b "
            "JOIN series s ON s.id = b.series_id "
            "WHERE s.monitor = ? AND s.entity_id = ? AND s.metric = ?",
            (monitor, entity_id, metric),
        ).fetchone()
        return None if row is None else self._baseline_record(row)

    @staticmethod
    def _baseline_filters(
        monitor: str | None, entity_id: str | None, metric: str | None, ready: bool | None
    ) -> dict[str, str | bool | None]:
        if ready is not None and not isinstance(ready, bool):
            raise ValueError("ready must be a boolean")
        return {
            "monitor": monitor,
            "entity_id": entity_id,
            "metric": metric,
            "ready": ready,
        }

    @staticmethod
    def _encode_baseline_cursor(
        key: tuple[str, str, str], filters: dict[str, str | bool | None]
    ) -> str:
        raw = json.dumps(
            {"v": 1, "after": list(key), "filters": filters},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_baseline_cursor(
        cursor: str, filters: dict[str, str | bool | None]
    ) -> tuple[str, str, str]:
        try:
            if not isinstance(cursor, str) or not cursor:
                raise ValueError
            raw = urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4))
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"v", "after", "filters"}
                or payload["v"] != 1
                or payload["filters"] != filters
                or not isinstance(payload["after"], list)
                or len(payload["after"]) != 3
                or not all(isinstance(value, str) for value in payload["after"])
            ):
                raise ValueError
            return tuple(payload["after"])
        except (BinasciiError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid baseline cursor or cursor/filter mismatch") from exc

    def list_baselines(
        self,
        *,
        monitor: str | None = None,
        entity_id: str | None = None,
        metric: str | None = None,
        ready: bool | None = None,
        limit: int = _BASELINE_DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> BaselinePage:
        """List stored rows with deterministic, filter-bound keyset pagination."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _BASELINE_MAX_LIMIT
        ):
            raise ValueError(f"limit must be an integer from 1 to {_BASELINE_MAX_LIMIT}")
        filters = self._baseline_filters(monitor, entity_id, metric, ready)
        after = self._decode_baseline_cursor(cursor, filters) if cursor is not None else None

        sql = (
            "SELECT s.monitor, s.entity_id, s.metric, b.value, b.updates, "
            "b.updated_bucket, b.half_life_s FROM baselines b "
            "JOIN series s ON s.id = b.series_id WHERE 1=1"
        )
        params: list[object] = []
        for column, value in (
            ("s.monitor", monitor), ("s.entity_id", entity_id), ("s.metric", metric)
        ):
            if value is not None:
                sql += f" AND {column} = ?"  # noqa: S608 -- column names are fixed above
                params.append(value)
        if ready is not None:
            sql += " AND b.updates >= ?" if ready else " AND b.updates < ?"
            params.append(_BASELINE_MIN_UPDATES)
        if after is not None:
            sql += " AND (s.monitor, s.entity_id, s.metric) > (?, ?, ?)"
            params.extend(after)
        sql += " ORDER BY s.monitor, s.entity_id, s.metric LIMIT ?"
        params.append(limit + 1)
        rows = self._conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        records = tuple(self._baseline_record(row) for row in page_rows)
        next_cursor = None
        if has_more:
            last = records[-1]
            next_cursor = self._encode_baseline_cursor(
                (last.monitor, last.entity_id, last.metric), filters
            )
        return BaselinePage(baselines=records, next_cursor=next_cursor)

    def baseline_history(
        self,
        monitor: str,
        entity_id: str,
        metric: str,
        *,
        start: float,
        end: float,
    ) -> BaselineHistory | None:
        """Reverse retained EWMA inputs into native five-minute states (CA-05)."""
        row = self._conn.execute(
            "SELECT s.id AS series_id, s.monitor, s.entity_id, s.metric, b.value, "
            "b.updates, b.updated_bucket, b.half_life_s FROM baselines b "
            "JOIN series s ON s.id = b.series_id "
            "WHERE s.monitor = ? AND s.entity_id = ? AND s.metric = ?",
            (monitor, entity_id, metric),
        ).fetchone()
        if row is None:
            return None
        baseline = self._baseline_record(row)
        rollups = self._conn.execute(
            "SELECT bucket, avg FROM rollup5m WHERE series_id = ? AND bucket <= ? "
            "ORDER BY bucket DESC LIMIT ?",
            (row["series_id"], baseline.updated_at, baseline.updates),
        ).fetchall()

        states = [SeriesPoint(baseline.updated_at, baseline.level)]
        remaining = baseline.updates
        level = baseline.level
        alpha = 1.0 - 2.0 ** (-_ROLLUP5M_S / baseline.half_life_s)
        # The current bucket's rollup is required to reverse its update. The
        # next retained row supplies the timestamp of the predecessor state.
        if rollups and rollups[0]["bucket"] == baseline.updated_at:
            for index in range(len(rollups) - 1):
                if remaining <= 1:
                    break
                current_rollup = rollups[index]
                level = (level - alpha * current_rollup["avg"]) / (1.0 - alpha)
                remaining -= 1
                states.append(SeriesPoint(int(rollups[index + 1]["bucket"]), level))

        reached_seed = remaining == 1
        states.reverse()
        points = tuple(point for point in states if start <= point.ts <= end)
        first_bucket_in_range = math.ceil(start / _ROLLUP5M_S) * _ROLLUP5M_S
        history_truncated = first_bucket_in_range < states[0].ts and not reached_seed
        return BaselineHistory(
            baseline=baseline,
            points=points,
            history_truncated=history_truncated,
        )

    def _resolution(self, now: float, start: float, end: float) -> str:
        span = end - start
        if end > now - _RAW_RECENT_S and span <= _RAW_MAX_SPAN_S:
            return "raw"
        if span <= _ROLLUP5M_MAX_SPAN_S:
            return "5m"
        return "1h"

    def series_catalog(self, *, now: float, start: float, end: float) -> list[sqlite3.Row]:
        """Series with observations in the tier the requested range will query."""
        resolution = self._resolution(now, start, end)
        table, time_column = {
            "raw": ("samples", "ts"),
            "5m": ("rollup5m", "bucket"),
            "1h": ("rollup1h", "bucket"),
        }[resolution]
        return self._conn.execute(
            "SELECT s.monitor, s.entity_id, s.metric FROM series s "
            f"WHERE EXISTS (SELECT 1 FROM {table} d "  # noqa: S608
            f"WHERE d.series_id=s.id AND d.{time_column}>=? AND d.{time_column}<=?) "
            "ORDER BY s.monitor, s.entity_id, s.metric",
            (round(start), round(end)),
        ).fetchall()

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
        statistic: str = "avg",
        include_envelope: bool = False,
    ) -> list[SeriesResult]:
        """Read a tier-transparent series with optional rollup extrema (DM-17).

        Column selection precedes LTTB. Envelopes are sampled at the exact
        timestamps selected for the center line so browser code cannot
        accidentally align unrelated buckets.
        """
        if statistic not in {"avg", "min", "max", "last"}:
            raise ValueError("statistic must be avg, min, max, or last")
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
                envelope = {p.ts: (p.value, p.value) for p in points}
            else:
                table = "rollup5m" if resolution == "5m" else "rollup1h"
                rows = self._conn.execute(
                    f"SELECT bucket, {statistic} AS value, min, max FROM {table} "  # noqa: S608
                    "WHERE series_id=? AND bucket>=? AND bucket<=? ORDER BY bucket",
                    (sid, istart, iend),
                ).fetchall()
                points = [
                    SeriesPoint(ts=r["bucket"], value=r["value"])
                    for r in rows
                    if r["value"] is not None
                ]
                envelope = {
                    r["bucket"]: (r["min"], r["max"])
                    for r in rows if r["min"] is not None and r["max"] is not None
                }

            downsampled = len(points) > max_points
            if downsampled:
                points = lttb(points, max_points)
            lower = upper = None
            if include_envelope:
                lower = [SeriesPoint(p.ts, envelope[p.ts][0]) for p in points
                         if p.ts in envelope]
                upper = [SeriesPoint(p.ts, envelope[p.ts][1]) for p in points
                         if p.ts in envelope]

            results.append(
                SeriesResult(
                    monitor=monitor,
                    metric=metric,
                    entity_id=row["entity_id"],
                    resolution=resolution,
                    points=points,
                    statistic=statistic,
                    lower=lower,
                    upper=upper,
                    downsampled=downsampled,
                )
            )
        return results

    def trend(
        self,
        monitor: str,
        entity_id: str,
        profile,
        *,
        now: float,
        start: float,
        end: float,
        parameters: dict[str, float],
        max_points: int = 2000,
    ) -> dict:
        """Build generic declared panels without inventing semantics (CA-10).

        ``profile`` is intentionally structural rather than imported from the
        definitions package: the store layer stays independent of config I/O.
        Projection uses persisted pre-downsampling inputs and gaps remain gaps.
        """
        max_points = max(3, max_points)

        def one(
            metric: str, *, statistic: str = "avg", envelope: bool = False,
            working_points: int = 10_000,
        ):
            rows = self.series(
                monitor, metric, now=now, start=start, end=end,
                entity_id=entity_id, max_points=working_points,
                statistic=statistic, include_envelope=envelope,
            )
            return rows[0] if rows else None

        # Inputs stay at the validated 10k history ceiling until projection is
        # qualified. Independently downsampling rate/confidence/free first can
        # choose different timestamps and manufacture missing intersections.
        value = one(profile.value_metric, envelope=True, working_points=max_points)
        rate = one(profile.rate_metric)
        confidence = one(profile.confidence_metric) if profile.confidence_metric else None
        remaining = one(profile.remaining_metric, statistic="last") \
            if profile.remaining_metric else None
        rate_map = {p.ts: p.value for p in rate.points} if rate else {}
        confidence_map = {p.ts: p.value for p in confidence.points} if confidence else {}
        remaining_map = {p.ts: p.value for p in remaining.points} if remaining else {}
        confidence_threshold = (
            parameters[profile.confidence_threshold_param]
            if profile.confidence_threshold_param else None
        )
        projection = []
        projection_ts = set(rate_map) & set(remaining_map)
        if profile.confidence_metric:
            projection_ts &= set(confidence_map)
        for ts in sorted(projection_ts):
            qualified = rate_map[ts] > 0 and (
                confidence_threshold is None
                or confidence_map[ts] >= confidence_threshold
            )
            projection.append([
                ts, remaining_map[ts] / rate_map[ts] if qualified else None
            ])

        def cap_nullable(points: list[list[float | None]]) -> list[list[float | None]]:
            """Cap projection while retaining endpoints and gap boundaries."""
            if len(points) <= max_points:
                return points
            stride = math.ceil((len(points) - 2) / (max_points - 2))
            indexes = {0, len(points) - 1, *range(1, len(points) - 1, stride)}
            indexes.update(
                i for i in range(1, len(points))
                if (points[i - 1][1] is None) != (points[i][1] is None)
            )
            selected = sorted(indexes)
            if len(selected) > max_points:
                selected = selected[::math.ceil(len(selected) / max_points)][:max_points - 1]
                selected.append(len(points) - 1)
            return [points[i] for i in selected]

        projection = cap_nullable(projection)

        incident_sql = (
            "SELECT id,state,severity,opened_ts,last_change_ts,cleared_ts,grp "
            "FROM incidents WHERE monitor=? AND entity_id=? "
            "AND last_change_ts>=? AND opened_ts<=?"
        )
        incident_params: list[object] = [monitor, entity_id, round(start), round(end)]
        if profile.incident_group:
            incident_sql += " AND grp=?"
            incident_params.append(profile.incident_group)
        incident_sql += " ORDER BY opened_ts"
        incidents = []
        for row in self._conn.execute(incident_sql, incident_params):
            incidents.append(dict(row))

        def pairs(result: SeriesResult | None) -> list[list[float]]:
            if result is None:
                return []
            points = result.points
            if len(points) > max_points:
                points = lttb(points, max_points)
            return [[p.ts, p.value] for p in points]

        current_value = value.points[-1].value if value and value.points else None
        change_value = None
        if value and len(value.points) >= 2:
            change_value = value.points[-1].value - value.points[0].value
        latest_candidates = set(rate_map)
        if profile.confidence_metric:
            latest_candidates &= set(confidence_map)
        if profile.remaining_metric:
            latest_candidates &= set(remaining_map)
        latest_ts = max(latest_candidates, default=None)
        latest_qualified = (
            latest_ts is not None
            and rate_map[latest_ts] > 0
            and (
                confidence_threshold is None
                or confidence_map[latest_ts] >= confidence_threshold
            )
        )
        summary = {
            "current_value": current_value,
            "change_value": change_value,
            "current_rate": rate_map.get(latest_ts) if latest_ts else None,
            "confidence": confidence_map.get(latest_ts) if latest_ts else None,
            "projected_limit_ts": (
                latest_ts + remaining_map[latest_ts] / rate_map[latest_ts] * 3600
                if latest_qualified and profile.remaining_metric else None
            ),
            "projection_reason": None if latest_qualified else (
                "no reliable projection: growth is non-positive, irregular, or insufficient"
            ) if profile.remaining_metric else None,
        }
        resolution = next((x.resolution for x in
                           (value, rate, confidence, remaining) if x), "raw")
        value_thresholds = [
            {"parameter": name, "value": parameters[name]}
            for name in profile.value_threshold_params
        ]
        rate_thresholds = [
            {"parameter": name, "value": parameters[name]}
            for name in profile.rate_threshold_params
        ]
        return {
            "monitor": monitor,
            "profile": {"id": profile.id, "kind": profile.kind, "title": profile.title},
            "entity": entity_id,
            "range": {"start": start, "end": end},
            "resolution": resolution,
            "panels": {
                "value": {
                    "metric": profile.value_metric, "unit": profile.value_unit,
                    "points": pairs(value),
                    "lower": [] if value is None or value.lower is None else
                             [[p.ts, p.value] for p in value.lower],
                    "upper": [] if value is None or value.upper is None else
                             [[p.ts, p.value] for p in value.upper],
                    "thresholds": value_thresholds,
                },
                "rate": {
                    "metric": profile.rate_metric, "unit": profile.rate_unit,
                    "points": pairs(rate), "thresholds": rate_thresholds,
                },
                "confidence": ({
                    "metric": profile.confidence_metric, "unit": "fraction",
                    "points": pairs(confidence), "threshold": confidence_threshold,
                } if profile.confidence_metric else None),
                "projection": ({"unit": "hours", "points": projection}
                               if profile.remaining_metric else None),
            },
            "incidents": incidents,
            "summary": summary,
        }

    def disk_trend(
        self, entity_id: str, *, now: float, start: float, end: float,
        filling_frac: float = 0.85, max_points: int = 2000,
    ) -> dict:
        """Compatibility adapter for the pre-M7.1 disk JSON contract."""
        profile = SimpleNamespace(
            id="space-growth", kind="capacity", title="Disk capacity growth",
            value_metric="used_pct", value_unit="percent",
            rate_metric="fill_rate_bph", rate_unit="bytes/hour",
            confidence_metric="filling", confidence_threshold_param="filling_frac",
            remaining_metric="free_bytes", value_threshold_params=(),
            rate_threshold_params=(), incident_group=None,
        )
        generic = self.trend(
            "disk", entity_id, profile, now=now, start=start, end=end,
            parameters={"filling_frac": filling_frac}, max_points=max_points,
        )
        panels = generic["panels"]
        summary = generic["summary"]
        return {
            "entity": entity_id, "range": generic["range"],
            "resolution": generic["resolution"],
            "units": {"capacity": "%", "rate": "bytes/hour",
                      "confidence": "fraction", "projection": "hours"},
            "capacity": panels["value"], "rate": panels["rate"]["points"],
            "confidence": panels["confidence"]["points"],
            "projection": panels["projection"]["points"],
            "incidents": generic["incidents"],
            "summary": {
                "current_used_pct": summary["current_value"],
                "change_bytes": None,
                "fill_rate_bph": summary["current_rate"],
                "filling_confidence": summary["confidence"],
                "projected_full_ts": summary["projected_limit_ts"],
                "projection_reason": summary["projection_reason"],
            },
        }

    def entities(
        self,
        monitor: str,
        *,
        alive_only: bool = False,
        seen_since: float | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM entities WHERE monitor=?"
        params: list[object] = [monitor]
        if alive_only:
            sql += " AND gone_ts IS NULL"
        if seen_since is not None:
            sql += " AND last_seen >= ?"
            params.append(round(seen_since))
        sql += " ORDER BY entity_id"
        return self._conn.execute(sql, params).fetchall()

    def last_plugin_result(
        self, monitor: str, entity_id: str, *, now: float,
    ) -> dict | None:
        """MC-06/EC-05: coherent last external-check result for one entity.

        Caller passes the definition's current ``source_options.entity`` so a
        rename cannot surface a stale prior configuration. ``plugin_ok`` and
        ``duration_s`` are read at the same sample timestamp as
        ``plugin_state`` — incomplete storage returns None rather than a
        partial block (EC-05 persists all three on every completed run).
        """
        state = self._conn.execute(
            "SELECT s.ts, s.value FROM samples s "
            "JOIN series se ON se.id = s.series_id "
            "WHERE se.monitor=? AND se.entity_id=? AND se.metric='plugin_state' "
            "ORDER BY s.ts DESC LIMIT 1",
            (monitor, entity_id),
        ).fetchone()
        if state is None:
            return None
        ts = state["ts"]
        siblings = {
            row["metric"]: row["value"]
            for row in self._conn.execute(
                "SELECT se.metric, s.value FROM samples s "
                "JOIN series se ON se.id = s.series_id "
                "WHERE se.monitor=? AND se.entity_id=? "
                "AND se.metric IN ('plugin_ok', 'duration_s') AND s.ts=?",
                (monitor, entity_id, ts),
            )
        }
        if "plugin_ok" not in siblings or "duration_s" not in siblings:
            return None
        attrs_row = self._conn.execute(
            "SELECT attrs FROM entities WHERE monitor=? AND entity_id=?",
            (monitor, entity_id),
        ).fetchone()
        attrs = json.loads(attrs_row["attrs"] or "{}") if attrs_row else {}
        message = attrs.get("plugin_message", "")
        if not isinstance(message, str):
            message = "" if message is None else str(message)
        return {
            "entity_id": entity_id,
            "plugin_state": int(state["value"]),
            "plugin_ok": bool(siblings["plugin_ok"]),
            "plugin_message": message,
            "duration_s": float(siblings["duration_s"]),
            "sample_age_s": round(now - ts),
        }

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

    @staticmethod
    def _parse_detail(raw: str | None) -> dict:
        """Operators do open sqlite3 on this DB (CLAUDE.md documents it), so a
        hand-mangled detail blob must degrade to data, never crash a read
        surface."""
        try:
            parsed = json.loads(raw or "{}")
        except ValueError:
            return {"malformed": (raw or "")[:200]}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def incident_detail(self, incident_id: int) -> IncidentDetail | None:
        """DM-11/DM-12: one incident row plus ordered history (explain substrate)."""
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            return None
        history = self._conn.execute(
            "SELECT seq, ts, kind, detail FROM incident_history "
            "WHERE incident_id = ? ORDER BY seq",
            (incident_id,),
        ).fetchall()
        return IncidentDetail(
            incident=dict(row),
            history=tuple(
                IncidentHistoryEntry(
                    seq=entry["seq"],
                    ts=entry["ts"],
                    kind=entry["kind"],
                    detail=self._parse_detail(entry["detail"]),
                )
                for entry in history
            ),
        )

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


class SmallWrites:
    """Non-daemon write surface (PM-03): short transactions, busy_timeout
    handles contention with the daemon's tick commit. Kept separate from
    Query so read-only consumers can stay on read-only connections."""

    def __init__(self, conn):
        self._conn = conn

    def ack(self, incident_id: int, by: str, ts: float, note: str | None = None) -> bool:
        """Ack an open incident (IN-02: quiet, not resolved). Returns False
        if the incident wasn't open (already acked/cleared/unknown)."""
        cur = self._conn.execute(
            "UPDATE incidents SET state = 'acked', ack_by = ?, ack_ts = ? "
            "WHERE id = ? AND state = 'open'",
            (by, round(ts), incident_id),
        )
        if cur.rowcount:
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM incident_history "
                "WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()[0]
            detail = {"by": by} if note is None else {"by": by, "note": note}
            self._conn.execute(
                "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) "
                "VALUES (?, ?, ?, 'acked', ?)",
                (incident_id, seq, round(ts), json.dumps(detail)),
            )
        self._conn.commit()
        return bool(cur.rowcount)

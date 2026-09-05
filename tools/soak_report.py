#!/usr/bin/env python3
"""[TS-17] Emit a markdown soak-evidence report from a live or copied FTMON DB.

Reads stored self-monitor history (RB-02), incident/outbox state, and doctor
output — the same query path operators trust — rather than sampling from ps/top.

Each budget is measured as its requirement states it, which is rarely the most
convenient series to hand:

- RB-01 bounds CPU "averaged over 10 m", so percentiles are taken over
  10-minute means and never over the per-tick samples underneath them. One
  tick's spike is not a budget breach; reporting it as `max` invited exactly
  that reading, and on real soak data the two differ by more than three times.
- DM-05's target is *used pages*. Per RB-02 the physical file participates in
  no budget identity: WAL and freelist hold it near the ceiling long after
  retention has released the space, so a file-size percentile reports a pin the
  daemon is not holding. Both are shown, only one is judged.
- Percentiles read the stored tiers directly instead of `Query.series`, whose
  LTTB downsampling picks visually representative points for charts — right for
  a graph, wrong for a distribution.

A 30-day window outlives raw retention, so older stretches survive only as
5-minute and then hourly rollups. An hourly mean cannot express a 10-minute
average or a peak, so the coarse tail is summarized apart from the verdict
rather than being silently averaged into it.

A leg upgraded in place keeps the previous build's history in the same
database, so a fixed 30-day window blends two builds and reports the older one
(issue #178). `--since` scopes the window to the build under test, and the
window actually used is printed in the report: a reader should see the mismatch
on the page rather than infer it from a percentile that looks wrong.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ftmon.store.db import connect, migrate
from ftmon.store.doctor import inspect
from ftmon.store.query import Query

_RB_CPU_PCT = 1.0
_RB_RSS_MB = 100
_DM05_DB_MB = 200

# RB-01's averaging window, in seconds. The number is the requirement.
_CPU_WINDOW_S = 600

_MIB = 1024 * 1024

# (table, time column, value column, count column). Tiers that can still
# express a 10-minute average, finest first.
_FINE_TIERS = (
    ("samples", "ts", "value", None),
    ("rollup5m", "bucket", "avg", "cnt"),
)
# Retained longest and too coarse for either RB-01 quantity.
_COARSE_TIER = ("rollup1h", "bucket", "avg", "cnt")


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def _tier_points(
    conn: sqlite3.Connection,
    tier: tuple[str, str, str, str | None],
    metric: str,
    start: float,
    end: float,
) -> list[tuple[int, float, int]]:
    """`(ts, value, weight)` for one storage tier, oldest first."""
    table, tcol, vcol, ccol = tier
    weight = f"COALESCE(d.{ccol}, 1)" if ccol else "1"
    try:
        rows = conn.execute(
            f"SELECT d.{tcol} AS ts, d.{vcol} AS value, {weight} AS weight "  # noqa: S608
            f"FROM {table} d JOIN series s ON s.id = d.series_id "
            "WHERE s.monitor='self' AND s.entity_id='ftmon' AND s.metric=? "
            f"AND d.{tcol} >= ? AND d.{tcol} <= ? ORDER BY d.{tcol}",
            (metric, round(start), round(end)),
        ).fetchall()
    except sqlite3.OperationalError:
        # A database written by an older schema simply has less to say.
        return []
    return [(int(r["ts"]), float(r["value"]), int(r["weight"] or 1))
            for r in rows if r["value"] is not None]


def _merged_points(
    conn: sqlite3.Connection, metric: str, *, start: float, end: float
) -> tuple[list[tuple[int, float, int]], list[tuple[int, float, int]]]:
    """Window history as (fine, coarse), each tier covering only what finer ones lost.

    Retention trims from the old end, so the tiers partition the window rather
    than overlapping it; taking the finest available for each stretch avoids
    counting the same minute twice at two resolutions.
    """
    fine: list[tuple[int, float, int]] = []
    horizon = None
    for tier in _FINE_TIERS:
        points = _tier_points(conn, tier, metric, start, end)
        if horizon is not None:
            points = [p for p in points if p[0] < horizon]
        if points:
            horizon = points[0][0] if horizon is None else min(horizon, points[0][0])
            fine.extend(points)
    fine.sort()

    coarse = _tier_points(conn, _COARSE_TIER, metric, start, end)
    if fine:
        coarse = [p for p in coarse if p[0] < fine[0][0]]
    return fine, coarse


def _window_means(points: list[tuple[int, float, int]], window_s: int) -> list[float]:
    """Count-weighted means over fixed `window_s` buckets — RB-01's quantity."""
    buckets: dict[int, tuple[float, int]] = {}
    for ts, value, weight in points:
        key = ts // window_s
        total, count = buckets.get(key, (0.0, 0))
        buckets[key] = (total + value * weight, count + weight)
    return [total / count for total, count in buckets.values() if count]


def _span_hours(points: list[tuple[int, float, int]]) -> float:
    return (points[-1][0] - points[0][0]) / 3600 if len(points) > 1 else 0.0


def parse_since(text: str) -> float:
    """Epoch seconds from an ISO-8601 timestamp or a raw epoch value."""
    try:
        return float(text)
    except ValueError:
        pass
    stamp = datetime.fromisoformat(text)
    # A bare timestamp is the operator's local time; manifests carry an offset.
    return (stamp.astimezone() if stamp.tzinfo is None else stamp).timestamp()


def _stamp(epoch: float) -> str:
    """Local wall time, falling back to UTC where the platform refuses.

    Windows' localtime() rejects pre-epoch timestamps, which a synthetic window
    reaches whenever `now` is small: a fixture at now=1000 puts the default
    30-day start at -2,591,000. A report must not fail over the label on a
    timestamp it can still measure.
    """
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(epoch))
    except (OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(epoch, tz=UTC).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )


def build_report(
    db_path: Path, *, now: float | None = None, days: int = 30, since: float | None = None
) -> str:
    """Return markdown summarizing TS-17 gate evidence from *db_path*."""
    now = time.time() if now is None else now
    start = now - days * 86400 if since is None else since
    scope = (
        f"rolling {days} d"
        if since is None
        else "scoped to the build under test (--since)"
    )
    conn = connect(db_path)
    try:
        migrate(conn)
        q = Query(conn)
        status = q.status(now=now)
        doctor = inspect(conn, now=now)

        cpu_fine, cpu_coarse = _merged_points(conn, "cpu_pct", start=start, end=now)
        rss_fine, rss_coarse = _merged_points(conn, "rss_bytes", start=start, end=now)
        used_fine, used_coarse = _merged_points(conn, "db_used_bytes", start=start, end=now)
        file_fine, _ = _merged_points(conn, "db_bytes", start=start, end=now)

        cpu_means = _window_means(cpu_fine, _CPU_WINDOW_S)

        self_incidents = conn.execute(
            "SELECT id, state, severity, opened_ts, cleared_ts, clear_reason "
            "FROM incidents WHERE monitor = 'self' ORDER BY id"
        ).fetchall()
        unexplained_self = [
            row for row in self_incidents
            if row["state"] in ("open", "acked")
            or (row["clear_reason"] not in (None, "recovered", "entity_gone"))
        ]

        pending_deliveries = conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries WHERE delivered_ts IS NULL"
        ).fetchone()[0]
        total_deliveries = conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0]

        daemon_starts = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source = 'self' AND message LIKE 'daemon started%'"
        ).fetchone()[0]

        lines = [
            "# FTMON soak evidence report",
            "",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(now))}",
            f"- Database: `{db_path}`",
            f"- Last tick age: {status.get('last_tick_age_s')}",
            f"- DB used (doctor, DM-05 budget): {doctor['used_bytes']:,} bytes",
            f"- DB file on disk (no budget identity): {doctor['db_bytes']:,} bytes",
            f"- DB freelist: {doctor['freelist_bytes']:,} bytes",
            f"- Window: {_stamp(start)} → {_stamp(now)} "
            f"({(now - start) / 3600:.1f} h, {scope})",
            "",
            "## RB-01 / DM-05 budgets",
            "",
            "| Quantity | p50 | p95 | max | budget |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]

        def _row(label: str, values: list[float], budget: float | None,
                 unit: str, places: int) -> None:
            def _fmt(value: float | None) -> str:
                return "—" if value is None else f"{value:.{places}f} {unit}"

            target = "—" if budget is None else f"{budget:g} {unit}"
            lines.append(
                f"| {label} | {_fmt(_percentile(values, 50))} "
                f"| {_fmt(_percentile(values, 95))} "
                f"| {_fmt(max(values) if values else None)} | {target} |"
            )

        _row("cpu_pct (10 m avg)", cpu_means, _RB_CPU_PCT, "%", 2)
        _row("rss_mb", [v / _MIB for _, v, _ in rss_fine], _RB_RSS_MB, "MB", 1)
        _row("db_used_mb", [v / _MIB for _, v, _ in used_fine], _DM05_DB_MB, "MB", 1)
        _row("db_file_mb (non-normative)", [v / _MIB for _, v, _ in file_fine], None, "MB", 1)

        lines.extend([
            "",
            f"- CPU: {len(cpu_means)} ten-minute windows over "
            f"{_span_hours(cpu_fine):.1f} h of 60 s/5 m data. Percentiles are of those "
            "means, per RB-01; a single tick's spike is not a budget breach.",
            "- `db_file_mb` carries no budget: WAL and freelist keep the file near the "
            "ceiling after retention has released the pages (RB-02).",
        ])
        if not used_fine:
            lines.append(
                "- `db_used_bytes` is absent from this database — a build older than the "
                "DM-05 used-page metric. Storage cannot be judged from this window."
            )
        if cpu_coarse or rss_coarse or used_coarse:
            coarse_cpu = _percentile([v for _, v, _ in cpu_coarse], 95)
            lines.append(
                "- Older stretches survive only as hourly rollups, too coarse for a "
                "10-minute average or a peak, so they are excluded above. For trend "
                f"only: {len(cpu_coarse)} hourly CPU means, p95 "
                f"{'—' if coarse_cpu is None else f'{coarse_cpu:.2f} %'}, spanning "
                f"{_span_hours(cpu_coarse):.1f} h."
            )

        lines.extend([
            "",
            "## Daemon stability",
            "",
            f"- Daemon start events (self): {daemon_starts}",
            f"- Unexplained self incidents: {len(unexplained_self)}",
            "",
            "## Notification outbox",
            "",
            f"- Pending deliveries: {pending_deliveries}",
            f"- Total delivery rows: {total_deliveries}",
            "",
            "## Doctor",
            "",
            "```json",
            json.dumps(doctor, indent=2, sort_keys=True),
            "```",
            "",
            "## Self incidents",
            "",
        ])
        if not self_incidents:
            lines.append("_No self-monitor incidents recorded._")
        else:
            for row in self_incidents:
                lines.append(
                    f"- #{row['id']} {row['state']} sev{row['severity']} "
                    f"opened={row['opened_ts']} cleared={row['cleared_ts']} "
                    f"reason={row['clear_reason']!r}"
                )
        return "\n".join(lines) + "\n"
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Path to ftmon.db")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Write markdown report to this path (default: stdout)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Evidence window in days (default: 30, the TS-17 gate)",
    )
    parser.add_argument(
        "--since", type=parse_since, default=None,
        help="Window start as ISO-8601 or epoch seconds; overrides --days. Use the "
             "current leg's start so an in-place upgrade does not report the "
             "previous build's history (issue #178)",
    )
    args = parser.parse_args(argv)
    report = build_report(
        args.database.expanduser().resolve(), days=args.days, since=args.since
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

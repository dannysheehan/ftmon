#!/usr/bin/env python3
"""[TS-17] Emit a markdown soak-evidence report from a live or copied FTMON DB.

Reads stored self-monitor history (RB-02), incident/outbox state, and doctor
output — the same query path operators trust — rather than sampling from ps/top.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ftmon.store.db import connect, migrate
from ftmon.store.doctor import inspect
from ftmon.store.query import Query

_RB_CPU_PCT = 1.0
_RB_RSS_MB = 100
_RB_DB_MB = 200


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def _series_values(q: Query, metric: str, *, now: float, days: int = 30) -> list[float]:
    start = now - days * 86400
    results = q.series(
        monitor="self",
        metric=metric,
        entity_id="ftmon",
        start=start,
        end=now,
        now=now,
        max_points=50_000,
        statistic="avg",
    )
    values: list[float] = []
    for result in results:
        values.extend(p.value for p in result.points if p.value is not None)
    return values


def build_report(db_path: Path, *, now: float | None = None) -> str:
    """Return markdown summarizing TS-17 gate evidence from *db_path*."""
    now = time.time() if now is None else now
    conn = connect(db_path)
    try:
        migrate(conn)
        q = Query(conn)
        status = q.status(now=now)
        doctor = inspect(conn, now=now)

        cpu = _series_values(q, "cpu_pct", now=now)
        rss = [v / (1024 * 1024) for v in _series_values(q, "rss_bytes", now=now)]
        db = [v / (1024 * 1024) for v in _series_values(q, "db_bytes", now=now)]

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
            f"- DB size (doctor): {doctor['db_bytes']:,} bytes",
            "",
            "## RB-01 self-monitor percentiles (30 d window)",
            "",
            "| Metric | p50 | p95 | max | budget |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]

        def _row(label: str, values: list[float], budget: float, unit: str = "") -> None:
            suffix = f" {unit}".rstrip()
            p50 = _percentile(values, 50)
            p95 = _percentile(values, 95)
            mx = max(values) if values else None

            def _fmt(value: float | None) -> str:
                return "—" if value is None else f"{value:.3g}{suffix}"

            lines.append(
                f"| {label} | {_fmt(p50)} | {_fmt(p95)} | {_fmt(mx)} | {budget:g}{suffix} |"
            )

        _row("cpu_pct", cpu, _RB_CPU_PCT, "%")
        _row("rss_mb", rss, _RB_RSS_MB, "MB")
        _row("db_mb", db, _RB_DB_MB, "MB")

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
    args = parser.parse_args(argv)
    report = build_report(args.database.expanduser().resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

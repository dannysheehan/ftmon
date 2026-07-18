"""Build the deterministic synthetic public-demo database (UI-15/16).

The builder consumes only the packaged scenario and never reads operational
FTMON paths. This separation is intentional: a deployment typo must not turn a
real monitoring database into public demonstration input.
"""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import tempfile
from importlib.resources import files
from pathlib import Path

from ftmon.definitions import loader
from ftmon.store.db import connect, migrate
from ftmon.store.retention import Retention

SCENARIO_NAME = "demo-v1"
POINT_COUNT = 7 * 24 + 1
POINT_INTERVAL = 3600
REQUIRED_STATES = frozenset({"clear", "warning", "error", "disabled"})
_BASELINE_HALF_LIFE_S = 3 * 86400.0
_BASELINE_READY_UPDATES = 240


def _records() -> list[dict]:
    """Load JSONL rather than executable fixtures so releases are auditable."""
    resource = files("ftmon.scenarios").joinpath(f"{SCENARIO_NAME}.jsonl")
    return [json.loads(line) for line in resource.read_text().splitlines() if line]


def _insert_series(conn: sqlite3.Connection, record: dict, header: dict) -> None:
    cursor = conn.execute(
        "INSERT INTO series(monitor,entity_id,metric,durable) VALUES(?,?,?,?)",
        (
            record["monitor"], record["entity"], record["metric"],
            int(record["durable"]),
        ),
    )
    series_id = cursor.lastrowid
    rng = random.Random(f"{header['seed']}:{record['monitor']}:{record['metric']}")
    first_ts = header["now"] - (POINT_COUNT - 1) * POINT_INTERVAL
    gap = record.get("gap")
    points = []
    for index in range(POINT_COUNT):
        if gap and gap[0] <= index < gap[1]:
            continue
        value = record["start"] + record["step"] * index
        value += rng.uniform(-record["jitter"], record["jitter"])
        points.append((series_id, first_ts + index * POINT_INTERVAL, value))
    conn.executemany("INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)", points)
    conn.execute(
        "INSERT OR IGNORE INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES(?,?,?,?,?)",
        (
            record["monitor"], record["entity"], first_ts, header["now"],
            json.dumps({"host": header["host"], "synthetic": "true"}, sort_keys=True),
        ),
    )


def _populate(conn: sqlite3.Connection, records: list[dict]) -> None:
    header = records[0]
    if header.get("type") != "header" or header.get("scenario") != SCENARIO_NAME:
        raise ValueError("demo scenario has no valid version header")
    monitors = [record for record in records if record.get("type") == "monitor"]
    states = {record["state"] for record in monitors}
    if states != REQUIRED_STATES:
        raise ValueError("demo scenario does not cover every required monitor state")

    conn.executemany(
        "INSERT INTO meta(key,value) VALUES(?,?)",
        (
            ("demo_dataset", "1"),
            ("demo_scenario", header["scenario"]),
            ("demo_scenario_version", str(header["version"])),
            ("demo_seed", str(header["seed"])),
            ("demo_now_ts", str(header["now"])),
            # A fresh common status lets UI-14 truthfully exhibit the four
            # per-monitor health states instead of overriding all as unknown.
            ("last_tick_ts", str(header["now"])),
            ("demo_coverage", json.dumps(sorted(states))),
            ("demo_monitor_states", json.dumps(
                {record["name"]: record["state"] for record in monitors},
                sort_keys=True,
            )),
            # Staleness is a separately labelled synthetic affordance in WP30;
            # it must not falsify the actual daemon-health precedence above.
            ("demo_stale_example", "1"),
        ),
    )
    for monitor in monitors:
        source = files("ftmon.definitions").joinpath(
            f"builtins/{monitor['name']}.toml"
        ).read_text()
        if not monitor["enabled"]:
            source = source.replace("enabled = true", "enabled = false", 1)
        definition = loader.load_text(source, f"<synthetic-{monitor['name']}>")
        conn.execute(
            "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) VALUES(?,?,?,?)",
            (
                monitor["name"], header["now"], definition.content_hash,
                definition.normalized_toml,
            ),
        )
    for record in records:
        if record.get("type") == "series":
            _insert_series(conn, record, header)
        elif record.get("type") == "incident":
            cleared = record.get("cleared_offset")
            conn.execute(
                "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                "opened_ts,last_change_ts,cleared_ts,clear_reason,notify_count,occurrences) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["id"], record["monitor"], record["group"], record["entity"],
                    record["state"], record["severity"], f"demo-{record['group']}",
                    header["now"] + record["opened_offset"],
                    header["now"] + record["changed_offset"],
                    header["now"] + cleared if cleared is not None else None,
                    "condition_cleared" if cleared is not None else None, 1, 1,
                ),
            )

    # Verify semantic coverage before publication; the marker is meaningful
    # only if the database actually contains both lifecycle and trend examples.
    incident_states = {
        row[0] for row in conn.execute("SELECT DISTINCT state FROM incidents")
    }
    trend_metrics = {
        (row[0], row[1]) for row in conn.execute("SELECT monitor,metric FROM series")
    }
    gap_count = conn.execute(
        "SELECT COUNT(*) FROM (SELECT series_id FROM samples GROUP BY series_id "
        "HAVING COUNT(*) < ?)", (POINT_COUNT,)
    ).fetchone()[0]
    if not {"open", "cleared"} <= incident_states:
        raise ValueError("demo scenario lacks open or recovered incidents")
    required_trend_metrics = {
        ("disk", "used_pct"), ("disk", "fill_rate_bph"), ("disk", "filling"),
        ("leak", "rss_mb"), ("leak", "rss_slope_mbph"),
        ("leak", "rss_growth_confidence"),
    }
    if not required_trend_metrics <= trend_metrics or gap_count == 0:
        raise ValueError("demo scenario lacks growth trends or chart gaps")


def _seed_ready_baseline(conn: sqlite3.Connection) -> None:
    """Make one exact ready example while retaining ordinary learning rows.

    The compact scenario samples hourly, so its naturally learned baselines
    are all below CA-05's 240-update threshold. Extra native 5-minute rollups
    are synthetic evidence, not fabricated browser points; recomputing the EWMA
    over all stored rows keeps reverse reconstruction mathematically honest.
    """
    series_id = conn.execute(
        "SELECT id FROM series WHERE monitor='disk' AND metric='used_pct'"
    ).fetchone()[0]
    existing = conn.execute(
        "SELECT bucket,avg FROM rollup5m WHERE series_id=? ORDER BY bucket",
        (series_id,),
    ).fetchall()
    present = {row["bucket"] for row in existing}
    latest = existing[-1]
    candidates = range(latest["bucket"] - 300, existing[0]["bucket"], -300)
    missing = [bucket for bucket in candidates if bucket not in present]
    needed = _BASELINE_READY_UPDATES - len(existing)
    conn.executemany(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(?,?,?,?,?,?,1)",
        [
            (series_id, bucket, latest["avg"], latest["avg"], latest["avg"], latest["avg"])
            for bucket in missing[:needed]
        ],
    )
    rollups = conn.execute(
        "SELECT bucket,avg FROM rollup5m WHERE series_id=? ORDER BY bucket",
        (series_id,),
    ).fetchall()
    if len(rollups) != _BASELINE_READY_UPDATES:
        raise ValueError("demo scenario cannot seed a ready baseline")
    alpha = 1.0 - math.pow(2.0, -300 / _BASELINE_HALF_LIFE_S)
    level = rollups[0]["avg"]
    for row in rollups[1:]:
        level += alpha * (row["avg"] - level)
    conn.execute(
        "UPDATE baselines SET value=?,updates=?,updated_bucket=?,half_life_s=? "
        "WHERE series_id=?",
        (level, len(rollups), rollups[-1]["bucket"], _BASELINE_HALF_LIFE_S, series_id),
    )


def build(output: Path) -> Path:
    """Atomically replace *output* with a complete read-only demo DB (UI-16)."""
    output = Path(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()  # connect() must see a fresh path to set database pragmas.
    try:
        conn = connect(temporary)
        try:
            migrate(conn)
            _populate(conn, _records())
            conn.commit()
            demo_now = int(
                conn.execute(
                    "SELECT value FROM meta WHERE key='demo_now_ts'"
                ).fetchone()[0]
            )
            # Deployment builds are offline, so one intentionally unbounded
            # pass is preferable to publishing partially populated rollup tiers.
            Retention(
                conn, max_bucket_span_s=8 * 86400, delete_batch=100_000
            ).run(demo_now + 60)
            _seed_ready_baseline(conn)
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("VACUUM")
        finally:
            conn.close()
        os.chmod(temporary, 0o444)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return output

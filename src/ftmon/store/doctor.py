"""Database diagnostics and consistent backup support (CL-05, VC-03)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ftmon.paths import set_private_permissions
from ftmon.store.db import db_size_report

_DISPATCH_OVERDUE_S = 60.0


def _meta_float(meta: dict, key: str) -> float | None:
    try:
        return float(meta[key])
    except (KeyError, TypeError, ValueError):
        return None


def dispatch_health(
    conn: sqlite3.Connection, *, quiet: object | None = None,
    daemon_live: bool = False,
) -> dict:
    """Judge delivery by what the dispatcher does, not how it is configured.

    Issue #98 stayed silent precisely because doctor equated "channels
    validate" with "notifications arrive". Ages here are measured against
    `last_tick_ts`, not the caller's wall clock: both are written by the
    daemon's injected clock, and under a controlled clock those two number
    lines are unrelated (TS-03). `daemon_live` comes from the PM-02 lock rather
    than a timestamp, so the answer holds whatever clock the daemon runs on.
    """
    from ftmon.store.outbox import backlog

    meta = dict(conn.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'notify_dispatch_%' "
        "OR key = 'last_tick_ts'"
    ).fetchall())
    daemon_ts = _meta_float(meta, "last_tick_ts")
    heartbeat_ts = _meta_float(meta, "notify_dispatch_heartbeat_ts")
    state = meta.get("notify_dispatch_state", "unknown")
    mode = meta.get("notify_dispatch_mode", "unknown")
    counts = backlog(conn, daemon_ts or 0.0, quiet)
    oldest = float(counts["oldest_claimable_due_age_s"])
    problems: list[str] = []
    # A stopped daemon owes exactly the debt it owes, and nothing is draining
    # it because nothing is running. Failing on that would break the documented
    # "stop the daemon, then inspect the database" workflow for any non-empty
    # outbox, so both predicates are gated on a daemon that should be working.
    if daemon_live and mode == "background":
        if state == "dead":
            category = meta.get("notify_dispatch_last_error_category", "unknown")
            problems.append(f"notification dispatcher dead ({category})")
        elif state in ("unknown", "stopped"):
            problems.append(f"notification dispatcher not running (state={state})")
    if daemon_live and oldest > _DISPATCH_OVERDUE_S:
        problems.append(f"oldest claimable delivery is {oldest:.0f}s overdue")
    return {
        **counts, "state": state, "mode": mode, "daemon_live": daemon_live,
        "last_error_category": meta.get("notify_dispatch_last_error_category", ""),
        "heartbeat_age_s": (
            max(0.0, daemon_ts - heartbeat_ts)
            if daemon_ts is not None and heartbeat_ts is not None else None
        ),
        "problems": problems,
    }


def inspect(
    conn: sqlite3.Connection, *, now: float, deep: bool = False,
    quiet: object | None = None, daemon_live: bool = False,
) -> dict:
    """Run bounded health checks and return a stable, JSON-able report."""
    check = "integrity_check" if deep else "quick_check"
    integrity = [row[0] for row in conn.execute(f"PRAGMA {check}").fetchall()]
    checkpoint = tuple(conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )]
    row_counts = {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                  for name in tables}
    orphan_queries = {
        "samples": "SELECT COUNT(*) FROM samples x LEFT JOIN series p "
                   "ON p.id=x.series_id WHERE p.id IS NULL",
        "rollup5m": "SELECT COUNT(*) FROM rollup5m x LEFT JOIN series p "
                    "ON p.id=x.series_id WHERE p.id IS NULL",
        "rollup1h": "SELECT COUNT(*) FROM rollup1h x LEFT JOIN series p "
                    "ON p.id=x.series_id WHERE p.id IS NULL",
        "baselines": "SELECT COUNT(*) FROM baselines x LEFT JOIN series p "
                     "ON p.id=x.series_id WHERE p.id IS NULL",
        "incident_history": "SELECT COUNT(*) FROM incident_history x LEFT JOIN incidents p "
                            "ON p.id=x.incident_id WHERE p.id IS NULL",
        "notifications": "SELECT COUNT(*) FROM notifications x LEFT JOIN incidents p "
                         "ON p.id=x.incident_id WHERE p.id IS NULL",
        "notification_deliveries":
            "SELECT COUNT(*) FROM notification_deliveries x LEFT JOIN notifications p "
            "ON p.id=x.notification_id WHERE p.id IS NULL",
    }
    orphans = {name: conn.execute(sql).fetchone()[0] for name, sql in orphan_queries.items()}
    cursors = [{"source": row["source"], "age_s": max(0, now-row["updated_ts"])}
               for row in conn.execute("SELECT source,updated_ts FROM cursors ORDER BY source")]
    size = db_size_report(conn)
    # DM-16's ~270/≤400 figures describe *active* catalog pressure, not a cap
    # on total retained rows (which legitimately grow under process churn even
    # with the MD-09 reap running); report the two counts separately (CL-05,
    # issue #74) rather than folding them into the ok/fail signal.
    #
    # `gone_ts IS NULL` means "this process is still running", not "we are
    # keeping durable history for it": SA-05 track-all marks every sampled
    # entity seen, so this overstates pressure by the sampled-to-selected
    # ratio — an order of magnitude on a desktop. It is retained under a name
    # that says what it counts; DM-16 pressure comes from entities_persisted.
    entities_not_gone = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE gone_ts IS NULL"
    ).fetchone()[0]
    # The pipeline is the only thing that knows its selected set, so the
    # DM-16 figure is whatever the daemon last published (RB-02). None means
    # no daemon has run since the metric was introduced — reported as unknown
    # rather than silently falling back to the count DM-16 rejects.
    def _latest_self(metric: str) -> int | None:
        row = conn.execute(
            "SELECT s.value FROM series se JOIN samples s ON s.series_id = se.id "
            "WHERE se.monitor='self' AND se.metric=? ORDER BY s.ts DESC LIMIT 1",
            (metric,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    entities_persisted = _latest_self("entities_persisted")
    series_persisted = _latest_self("series_persisted")
    # Historical series belonging to a still-running entity. Like
    # entities_not_gone this is a *presence* measure, not DM-16 pressure: it
    # counts series that were ever written for a process that happens to still
    # exist, including ones nothing has written to in weeks. Named for what it
    # counts and reported without a budget comparison (#104 review).
    series_not_gone = conn.execute(
        "SELECT COUNT(DISTINCT sm.series_id) FROM samples sm "
        "JOIN series se ON se.id = sm.series_id "
        "JOIN entities en ON en.monitor = se.monitor AND en.entity_id = se.entity_id "
        "WHERE en.gone_ts IS NULL"
    ).fetchone()[0]
    dm16 = {
        "max_entities_persisted": 400,
        "entities_persisted": entities_persisted,
        "max_series_persisted": 270,
        "series_persisted": series_persisted,
        # Reported beside the budget figures, never as them (DM-16).
        "entities_not_gone": entities_not_gone,
        "series_not_gone": series_not_gone,
    }
    meta_rows = dict(conn.execute(
        "SELECT key, value FROM meta WHERE key IN "
        "('last_reap_ts', 'last_reap_count', 'last_degradation_ts')"
    ).fetchall())
    last_reap_ts = float(meta_rows["last_reap_ts"]) if "last_reap_ts" in meta_rows else None
    last_reap_count = (
        int(meta_rows["last_reap_count"]) if "last_reap_count" in meta_rows else None
    )
    last_reap_age_s = max(0, now - last_reap_ts) if last_reap_ts is not None else None
    last_degradation_ts = (
        float(meta_rows["last_degradation_ts"])
        if "last_degradation_ts" in meta_rows else None
    )
    last_degradation_age_s = (
        max(0, now - last_degradation_ts) if last_degradation_ts is not None else None
    )
    dispatch = dispatch_health(conn, quiet=quiet, daemon_live=daemon_live)
    return {"dispatch": dispatch,
            "check": check, "integrity": integrity, "checkpoint": checkpoint,
            # db_bytes is the file on disk; allocated is SQLite's logical
            # size. WAL mode lets them differ, so both are reported (#104).
            "db_bytes": size["file_bytes"] if size["file_bytes"] is not None
                        else size["allocated_bytes"],
            "allocated_bytes": size["allocated_bytes"],
            "used_bytes": size["used_bytes"],
            "freelist_pages": size["freelist_pages"],
            "freelist_bytes": size["freelist_bytes"],
            "freelist_fragment_pct": size["freelist_fragment_pct"],
            "tables": row_counts,
            "orphans": orphans, "cursors": cursors,
            "entities_not_gone": entities_not_gone,
            "entities_persisted": entities_persisted,
            "series_not_gone": series_not_gone,
            "series_persisted": series_persisted,
            "dm16": dm16,
            "last_reap_ts": last_reap_ts, "last_reap_count": last_reap_count,
            "last_reap_age_s": last_reap_age_s,
            "last_degradation_ts": last_degradation_ts,
            "last_degradation_age_s": last_degradation_age_s,
            "ok": (integrity == ["ok"] and not any(orphans.values())
                   and not dispatch["problems"])}


def backup(conn: sqlite3.Connection, destination: Path) -> None:
    """Create a consistent live snapshot using SQLite's backup API (VC-03)."""
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    set_private_permissions(destination.parent, 0o700)
    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")
    try:
        target = sqlite3.connect(destination)
        try:
            conn.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check: {result}")
        finally:
            target.close()
        set_private_permissions(destination, 0o600)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

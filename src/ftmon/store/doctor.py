"""Database diagnostics and consistent backup support (CL-05, VC-03)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ftmon.paths import set_private_permissions


def inspect(conn: sqlite3.Connection, *, now: float, deep: bool = False) -> dict:
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
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    # DM-16's ~270/≤400 figures describe *active* catalog pressure, not a cap
    # on total retained rows (which legitimately grow under process churn even
    # with the MD-09 reap running); report the two counts separately (CL-05,
    # issue #74) rather than folding them into the ok/fail signal.
    entities_alive = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE gone_ts IS NULL"
    ).fetchone()[0]
    # A raw sample alone isn't "active": a just-gone entity keeps its samples
    # until DM-04's window or MD-09's reap catches up, so counting bare
    # sample presence would double-count catalog pressure that's already on
    # its way out. Require the owning entity to still be alive (gone_ts NULL).
    series_active = conn.execute(
        "SELECT COUNT(DISTINCT sm.series_id) FROM samples sm "
        "JOIN series se ON se.id = sm.series_id "
        "JOIN entities en ON en.monitor = se.monitor AND en.entity_id = se.entity_id "
        "WHERE en.gone_ts IS NULL"
    ).fetchone()[0]
    dm16 = {
        "max_entities_active": 400,
        "entities_active": entities_alive,
        "max_series_active": 270,
        "series_active": series_active,
    }
    meta_rows = dict(conn.execute(
        "SELECT key, value FROM meta WHERE key IN ('last_reap_ts', 'last_reap_count')"
    ).fetchall())
    last_reap_ts = float(meta_rows["last_reap_ts"]) if "last_reap_ts" in meta_rows else None
    last_reap_count = (
        int(meta_rows["last_reap_count"]) if "last_reap_count" in meta_rows else None
    )
    last_reap_age_s = max(0, now - last_reap_ts) if last_reap_ts is not None else None
    return {"check": check, "integrity": integrity, "checkpoint": checkpoint,
            "db_bytes": page_count * page_size, "tables": row_counts,
            "orphans": orphans, "cursors": cursors,
            "entities_alive": entities_alive, "series_active": series_active,
            "dm16": dm16,
            "last_reap_ts": last_reap_ts, "last_reap_count": last_reap_count,
            "last_reap_age_s": last_reap_age_s,
            "ok": integrity == ["ok"] and not any(orphans.values())}


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

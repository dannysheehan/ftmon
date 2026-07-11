"""Database diagnostics and consistent backup support (CL-05, VC-03)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


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
    return {"check": check, "integrity": integrity, "checkpoint": checkpoint,
            "db_bytes": page_count * page_size, "tables": row_counts,
            "orphans": orphans, "cursors": cursors,
            "ok": integrity == ["ok"] and not any(orphans.values())}


def backup(conn: sqlite3.Connection, destination: Path) -> None:
    """Create a consistent live snapshot using SQLite's backup API (VC-03)."""
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        os.chmod(destination, 0o600)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

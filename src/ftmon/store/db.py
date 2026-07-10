"""Connection management and schema migrations (DESIGN.md section 8).

No direct clock reads here (TS-03) — this module only touches sqlite3/os/pathlib.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

__all__ = ["connect", "migrate"]

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the standard pragmas.

    readonly=True opens via a `file:...?mode=ro` URI and never creates
    anything. Otherwise the parent directory is created 0700 (SE-04) and,
    if the database file does not exist yet, `auto_vacuum=INCREMENTAL` is
    set before any table is created (DM-05) — this must happen on the very
    first connection to a fresh file, since auto_vacuum mode can only be
    changed on an otherwise-empty database.
    """
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        is_new = not db_path.exists()
        conn = sqlite3.connect(str(db_path))
        if is_new:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            os.chmod(db_path, 0o600)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migration_files() -> list[tuple[int, Path]]:
    files = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        num = int(path.stem.split("_", 1)[0])
        files.append((num, path))
    files.sort(key=lambda t: t[0])
    return files


def migrate(conn: sqlite3.Connection) -> int:
    """Apply migrations/*.sql in order, gated by PRAGMA user_version.

    Idempotent: calling this again when already at the latest version is a
    no-op and returns the same version. Returns the final user_version.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    version = current
    for num, path in _migration_files():
        if num <= current:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version = {num}")
        version = num
    conn.commit()
    return version

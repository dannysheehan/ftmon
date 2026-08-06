"""Connection management and schema migrations (DESIGN.md section 8).

No direct clock reads here (TS-03) — this module only touches sqlite3/os/pathlib.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ftmon.paths import set_private_permissions

__all__ = [
    "DB_BUDGET_BYTES", "connect", "db_size_report", "is_disconnected_error",
    "is_locked_error", "migrate",
]

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: DM-05's normative used-page budget. One definition, because retention
#: enforces against it while doctor, `ftmon status`, MCP and the self source
#: report distance to it — two constants that must agree is a latent drift.
DB_BUDGET_BYTES = 200 * 2**20


def db_size_report(cur) -> dict:
    """DM-05's used-vs-file arithmetic, computed in exactly one place.

    Accepts a connection or a cursor, because the enforcement path (retention's
    degradation loop) already holds a cursor while the reporting paths hold
    connections — and the whole point is that both ask the same question of the
    same code. Independent re-derivations of
    `(page_count - freelist_count) * page_size` are how a budget alarm came to
    watch file allocation while DM-05 governed used pages (issue #104).
    """
    page_count = cur.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = cur.execute("PRAGMA freelist_count").fetchone()[0]
    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    db_bytes = page_count * page_size
    freelist_bytes = freelist_count * page_size
    return {
        "db_bytes": db_bytes,
        "used_bytes": db_bytes - freelist_bytes,
        "freelist_pages": freelist_count,
        "freelist_bytes": freelist_bytes,
        "freelist_fragment_pct": freelist_count / page_count if page_count else 0.0,
    }


def is_locked_error(exc: BaseException) -> bool:
    """True when SQLite is reporting contention rather than a broken store.

    Both survive-the-lock paths (PM-10's tick commit and PM-12's dispatcher)
    ask this question, and they must answer it identically: two independent
    spellings of "is this SQLite telling us it is busy" is exactly how the two
    requirements would drift apart later. sqlite3 exposes contention only as
    OperationalError message text, so matching the message is the available
    test, not a shortcut.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def is_disconnected_error(exc: BaseException) -> bool:
    """True when the connection object itself is no longer usable.

    Distinct from contention: the fix is a new connection, not waiting. Seen
    after a suspend/resume cycle invalidates the file handle underneath a
    long-lived worker connection (issue #98).
    """
    if not isinstance(exc, sqlite3.OperationalError | sqlite3.ProgrammingError):
        return False
    return "closed" in str(exc).lower()


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
        set_private_permissions(db_path.parent, 0o700)
        is_new = not db_path.exists()
        conn = sqlite3.connect(str(db_path))
        if is_new:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            set_private_permissions(db_path, 0o600)

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

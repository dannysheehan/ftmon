"""CLI-level deterministic demo build coverage (UI-15/16)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys


def test_demo_build_cli_creates_immutable_marked_database_ui_15(tmp_path):
    """[UI-15][UI-16][TS-14] CLI builds without operational input."""
    output = tmp_path / "demo.db"
    result = subprocess.run(
        [sys.executable, "-m", "ftmon", "demo", "build", "--output", str(output)],
        check=False, capture_output=True, text=True,
        # Empty FTMON variables would expose accidental operational-path reads.
        env={key: value for key, value in os.environ.items() if not key.startswith("FTMON_")},
    )
    assert result.returncode == 0, result.stderr
    assert str(output) in result.stdout
    conn = sqlite3.connect(f"file:{output}?mode=ro&immutable=1", uri=True)
    try:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='demo_dataset'"
        ).fetchone()[0] == "1"
    finally:
        conn.close()

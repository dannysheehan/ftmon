"""[TS-16][XR-02] Live recipe install through the real daemon binary."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from ftmon.cli import main
from tests.e2e.harness import DaemonHarness

ROOT = Path(__file__).resolve().parents[2]
EXTRA_MONITORS = ROOT / "extra-monitors"
CHECK_DISK = Path("/usr/lib/nagios/plugins/check_disk")


@pytest.mark.skipif(
    not CHECK_DISK.is_file(),
    reason="monitoring-plugins check_disk not installed",
)
def test_root_disk_recipe_install_collects_samples_e2e(tmp_path, monkeypatch):
    """Recipe install registers authority, enables the monitor, and the daemon samples."""
    monkeypatch.setenv("FTMON_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("FTMON_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FTMON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FTMON_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(EXTRA_MONITORS))

    assert main(["recipe", "install", "root-disk"]) == 0
    monitor = tmp_path / "cfg" / "monitors" / "root_disk.toml"
    registry = tmp_path / "cfg" / "checks.toml"
    assert monitor.is_file()
    assert "enabled = true" in monitor.read_text()
    assert "root_disk" in registry.read_text()

    harness = DaemonHarness(tmp_path, {}, "firefox-leak-2mb-min")
    harness.env["FTMON_EXTRA_MONITORS"] = str(EXTRA_MONITORS)
    try:
        harness.start()
        harness.step_until(_has_root_disk_sample, max_steps=40, s=5.0)
    finally:
        harness.stop()


def _has_root_disk_sample() -> bool:
    db = os.environ.get("FTMON_DATA_DIR")
    if not db:
        return False
    path = Path(db) / "ftmon.db"
    if not path.is_file():
        return False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM samples s
            JOIN series sr ON s.series_id = sr.id
            WHERE sr.monitor = 'root_disk' AND sr.metric = 'used_bytes'
            """
        ).fetchone()
        return bool(row and row[0] > 0)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))

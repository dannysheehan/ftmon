"""Opt-in Linux integration journey for the packaged operational surface.

This tier is excluded by default because it intentionally talks to the user's
systemd and journal. It still isolates all FTMON state below a temporary root;
the only host-visible artifact is a uniquely named transient user service and
one uniquely tagged journal record, both removed during teardown (TS-08).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

import pytest


def _need_user_systemd():
    if sys.platform != "linux" or not shutil.which("systemd-run"):
        pytest.skip("Linux systemd user session required")
    probe = subprocess.run(
        ["systemctl", "--user", "is-system-running"], capture_output=True, text=True
    )
    if probe.returncode not in (0, 1):
        pytest.skip("no usable systemd --user session")


@pytest.mark.realsystem
def test_real_system_operational_journey_ts_08(tmp_path):
    """[TS-08] systemd, psutil, journal cursor, notify audit, CLI/web and doctor."""
    _need_user_systemd()
    if not shutil.which("logger") or not shutil.which("journalctl"):
        pytest.skip("logger and journalctl required")
    token = uuid.uuid4().hex
    unit = f"ftmon-test-{token}.service"
    env = {
        **os.environ,
        "FTMON_CONFIG_DIR": str(tmp_path / "config"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    subprocess.run([sys.executable, "-m", "ftmon", "init"], env=env, check=True)
    run_cmd = [
        "systemd-run", "--user", f"--unit={unit}", "--collect",
        *[f"--setenv={key}={env[key]}" for key in (
            "FTMON_CONFIG_DIR", "FTMON_DATA_DIR", "FTMON_STATE_DIR", "FTMON_RUNTIME_DIR"
        )],
        sys.executable, "-m", "ftmon", "daemon",
    ]
    web = None
    try:
        subprocess.run(run_cmd, check=True, capture_output=True, text=True)
        db_path = tmp_path / "data/ftmon.db"
        deadline = time.monotonic() + 150
        sample_ticks = set()
        while time.monotonic() < deadline and len(sample_ticks) < 3:
            if db_path.exists():
                conn = sqlite3.connect(db_path)
                row = conn.execute(
                    "SELECT value FROM meta WHERE key='last_tick_ts'"
                ).fetchone()
                if row:
                    sample_ticks.add(row[0])
                conn.close()
            time.sleep(5)
        assert len(sample_ticks) >= 3, "daemon did not complete three real cycles"

        marker = f"FTMON_TS08_{token}"
        subprocess.run(["logger", "-p", "user.err", "-t", "ftmon-ts08", marker], check=True)
        deadline = time.monotonic() + 30
        cursor_before = None
        while time.monotonic() < deadline:
            conn = sqlite3.connect(db_path)
            event = conn.execute(
                "SELECT 1 FROM events WHERE message LIKE ?", (f"%{marker}%",)
            ).fetchone()
            cursor = conn.execute(
                "SELECT cursor FROM cursors WHERE source='journald'"
            ).fetchone()
            conn.close()
            if event and cursor:
                cursor_before = cursor[0]
                break
            time.sleep(1)
        assert cursor_before is not None

        subprocess.run(["systemctl", "--user", "restart", unit], check=True)
        time.sleep(8)
        conn = sqlite3.connect(db_path)
        cursor_after = conn.execute(
            "SELECT cursor FROM cursors WHERE source='journald'"
        ).fetchone()[0]
        duplicate_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE message LIKE ?", (f"%{marker}%",)
        ).fetchone()[0]
        conn.close()
        assert cursor_after and duplicate_count == 1

        status = subprocess.run(
            [sys.executable, "-m", "ftmon", "status", "--json"],
            env=env, check=False, capture_output=True, text=True,
        )
        assert json.loads(status.stdout)["status"] == "ok"
        doctor = subprocess.run(
            [sys.executable, "-m", "ftmon", "doctor"],
            env=env, check=False, capture_output=True, text=True,
        )
        assert doctor.returncode == 0, doctor.stderr

        web = subprocess.Popen(
            [sys.executable, "-m", "ftmon", "web"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8420/", timeout=2) as response:
                    assert response.status == 200
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.25)

        audit = tmp_path / "state/notifications.jsonl"
        assert audit.exists() and marker in audit.read_text()
        if shutil.which("notify-send"):
            # Desktop availability is session-dependent; the durable audit is
            # authoritative and a failed popup must not fail monitoring.
            subprocess.run(["notify-send", "FTMON TS-08", token], check=False)
    finally:
        if web is not None:
            web.terminate()
            try:
                web.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web.kill()
        subprocess.run(["systemctl", "--user", "stop", unit], check=False)

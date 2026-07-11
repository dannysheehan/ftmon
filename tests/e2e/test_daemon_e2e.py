"""[TS-05][NO-04][IN-02][SA-07][PM-02] Tier-1 e2e: the real daemon binary,
controlled clock, scenario fixtures. The kill-9 test is the reason this tier
exists: the at-most-one-duplicate delivery bound cannot be shown in-process.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys

import pytest

from tests.e2e.harness import DaemonHarness
from tests.unit.test_engine import LEAKDEF


@pytest.fixture
def harness(tmp_path):
    h = DaemonHarness(tmp_path, {"leak": LEAKDEF}, "firefox-leak-2mb-min")
    yield h
    h.stop()


def _db(h):
    conn = sqlite3.connect(f"file:{h.paths.db_file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def test_leak_fire_and_clear_end_to_end(harness):
    """[TS-05] happy path through the real binary: scenario leak opens an
    incident (notification on disk), flattening recovers it, and the DB
    agrees with the notification file."""
    h = harness
    h.start()
    opened = h.step_until(
        lambda: any(n["kind"] == "open" for n in h.notifications()), max_steps=250)
    assert opened > 0
    h.step_until(
        lambda: any(n["kind"] == "recover" for n in h.notifications()), max_steps=600)
    kinds = [n["kind"] for n in h.notifications()]
    assert kinds[0] == "open" and kinds[-1] == "recover"
    assert kinds.count("open") == 1 and kinds.count("recover") == 1

    conn = _db(h)
    row = conn.execute("SELECT state, clear_reason FROM incidents").fetchone()
    assert row["state"] == "cleared" and row["clear_reason"] == "recovered"
    # NO-04: nothing owed — every outbox row delivered or deliberately stale
    assert conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE delivered_ts IS NULL AND stale = 0"
    ).fetchone()[0] == 0


def test_kill9_at_most_one_duplicate_notification(harness):
    """[NO-04][TS-05] SIGKILL after the open notification, restart, run on:
    at most one duplicate delivery, no committed notification lost, and the
    WAL database comes back uncorrupted."""
    h = harness
    h.start()
    h.step_until(lambda: any(n["kind"] == "open" for n in h.notifications()),
                 max_steps=250)
    h.kill9()

    h.start()  # same dirs: must rebuild the open incident, not re-fire it
    for _ in range(24):  # 2 sim-minutes for recover() + rebuilt state to settle
        h.step()

    notes = h.notifications()
    opens = [n for n in notes if n["kind"] == "open"]
    # The spec's honest bound is at-least-once with <= 1 duplicate: a crash
    # exactly between delivery and the delivered_ts stamp replays one row.
    assert 1 <= len(opens) <= 2
    if len(opens) == 2:
        assert opens[0]["incident_id"] == opens[1]["incident_id"]

    conn = _db(h)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE delivered_ts IS NULL AND stale = 0"
    ).fetchone()[0] == 0  # no committed transition silently lost
    assert conn.execute(
        "SELECT COUNT(*) FROM incidents WHERE state = 'open'").fetchone()[0] == 1


def test_single_instance_lock(harness):
    """[PM-02] a second daemon against the same runtime dir refuses to start."""
    h = harness
    h.start()
    h.step()  # ensure the first daemon holds the lock
    second = subprocess.run(
        [sys.executable, "-m", "ftmon", "daemon"],
        env=h.env, capture_output=True, text=True, timeout=30,
    )
    assert second.returncode == 1
    assert "already running" in second.stderr


def test_sigterm_stops_cleanly(harness):
    """Graceful shutdown: SIGTERM + one step lets the loop exit 0."""
    h = harness
    h.start()
    h.step()
    h.proc.send_signal(signal.SIGTERM)
    try:
        h._sock.sendall(b'{"op": "step", "s": 5}\n')
    except OSError:
        pass
    assert h.proc.wait(timeout=15) == 0
    assert "daemon stopped" in h.log.read_text()

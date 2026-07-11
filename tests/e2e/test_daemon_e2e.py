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


EVENTSDEF = """
schema = 1
[monitor]
name = "events"
description = "e2e events"
version = 1
enabled = true
platforms = ["linux"]
source = "events"
[[rule]]
id = "oom"
when = 'provider == "kernel" and contains(message, "Out of memory")'
severity = "critical"
cooldown = "5m"
clear_after = "30m"
message = "OOM killer fired: {message}"
"""


def test_episode_lifecycle_e2e(tmp_path):
    """[IN-08][TS-05][DM-15] episode open -> cooldown renotify -> quiet clear
    through the real binary, with the cursor persisted in the DB."""
    h = DaemonHarness(tmp_path, {"events": EVENTSDEF}, "oom-event-burst")
    try:
        h.start()
        h.step_until(
            lambda: any(n["kind"] == "open" for n in h.notifications()),
            max_steps=30)
        h.step_until(
            lambda: h.paths.db_file.exists() and _db(h).execute(
                "SELECT COUNT(*) FROM incidents WHERE state='cleared'"
            ).fetchone()[0] == 1,
            max_steps=520)
        kinds = [n["kind"] for n in h.notifications()]
        assert kinds.count("open") == 1
        assert set(kinds[1:]) <= {"renotify"}  # quiet clear is silent
        conn = _db(h)
        row = conn.execute("SELECT * FROM incidents").fetchone()
        assert row["clear_reason"] == "quiet_period"
        assert row["occurrences"] == 12
        assert conn.execute(
            "SELECT cursor FROM cursors WHERE source='journald'"
        ).fetchone()["cursor"] == "12"
    finally:
        h.stop()


def test_quiet_hours_digest_e2e(tmp_path):
    """[NO-03][TS-05] quiet hours through the real binary: the leak opens
    *inside* the quiet window (incidents are never suppressed, only
    delivery), its warning-level notifications are held, and the first
    thing ever delivered is the single digest once quiet ends."""
    # ControlledClock starts at wall 1_700_000_000 = 22:13:20 UTC; TZ=UTC
    # pins the daemon's local time so "22:00-22:45" means the same window.
    quiet_end_wall = 1_700_000_000.0 + 31 * 60 + 40  # 22:45:00
    h = DaemonHarness(tmp_path, {"leak": LEAKDEF}, "firefox-leak-2mb-min")
    h.env["TZ"] = "UTC"
    h.paths.config_file.write_text(
        '[quiet_hours]\nenabled = true\nstart = "22:00"\nend = "22:45"\n')
    try:
        h.start()
        # leak opens ~8 sim-minutes in (22:21, held); quiet ends 31m40s in.
        h.step_until(
            lambda: any(n["kind"] == "digest" for n in h.notifications()),
            max_steps=450)
        for _ in range(24):  # 2 more sim-minutes: post-quiet flushes run
            h.step()

        notes = h.notifications()
        kinds = [n["kind"] for n in notes]
        assert kinds[0] == "digest"  # nothing was delivered individually
        assert "held during quiet hours" in notes[0]["title"]
        assert "open" not in kinds  # the open went out inside the digest
        assert set(kinds[1:]) <= {"renotify", "recover"}  # quiet has ended

        conn = _db(h)
        row = conn.execute("SELECT opened_ts, state FROM incidents").fetchone()
        assert row["opened_ts"] < quiet_end_wall  # opened during quiet (NO-03)
        # held rows were digested, not dropped or left owing (NO-04)
        assert conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE delivered_ts IS NULL AND stale = 0"
        ).fetchone()[0] == 0
    finally:
        h.stop()


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


def test_action_runs_through_real_daemon_once_e2e_ac_02(tmp_path):
    """[AC-02][TS-05] A real daemon commits the incident before running its action."""
    action_def = LEAKDEF.replace(
        'message = "{entity} leaking"',
        'message = "{entity} leaking"\naction = "capture"',
    )
    h = DaemonHarness(tmp_path, {"leak": action_def}, "firefox-leak-2mb-min")
    script = h.paths.actions_dir / "capture"
    script.write_text("#!/bin/sh\nprintf '%s' \"$FTMON_INCIDENT_ID\" > action-ran\n")
    script.chmod(0o700)  # the test supplies the user-owned executable (AC-03)
    try:
        h.start()
        marker = h.paths.state_dir / "action-ran"
        h.step_until(marker.exists, max_steps=250)
        conn = _db(h)
        incident_id = conn.execute("SELECT id FROM incidents").fetchone()[0]
        assert marker.read_text() == str(incident_id)
        row = conn.execute(
            "SELECT kind,detail FROM incident_history WHERE kind='action_run'"
        ).fetchone()
        assert row is not None and '"exit_code": 0' in row["detail"]
    finally:
        h.stop()

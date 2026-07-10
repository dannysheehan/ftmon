"""[NO-01][NO-02][NO-04][IN-02][IN-04][DM-11][DM-12][DM-14] M2 integration:
leak fires -> outbox -> file notifier -> recovery; ack via SmallWrites;
outbox retry/stale semantics."""

from __future__ import annotations

import json

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import Notification
from ftmon.notify.base import NotifyError
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.outbox import Outbox
from ftmon.store.query import SmallWrites
from ftmon.store.writer import TickWriter
from tests.unit.test_engine import LEAKDEF, ScriptedSampler, grower


@pytest.fixture
def core_env(tmp_path):
    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    return paths


def notifications(paths):
    if not paths.notifications_file.exists():
        return []
    return [json.loads(line) for line in
            paths.notifications_file.read_text().splitlines()]


def tick_n(core, clock, n, step=60.0):
    for _ in range(n):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(step)


def test_leak_to_notification_to_recovery(core_env):
    """The whole M2 loop: sustained growth opens an incident and delivers an
    open notification; flattening clears it with one recovery notification;
    every step is in the DB (incident row, history, delivered outbox)."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)

    sampler = ScriptedSampler()
    for i in range(8):
        sampler.push(grower(i))  # +200 KB/min = 12 MB/h, well over 1 MB/h
    for _ in range(12):
        sampler.push(grower(7))  # growth stops: slope -> 0 -> FALSE
    core.samplers["process"] = sampler

    tick_n(core, clock, 8)
    notes = notifications(paths)
    assert [n["kind"] for n in notes] == ["open"]
    assert notes[0]["severity"] == 2 and "leaking" in notes[0]["body"]

    conn = connect(paths.db_file, readonly=True)
    inc_row = conn.execute("SELECT * FROM incidents").fetchone()
    assert inc_row["state"] == "open"
    assert inc_row["grp"] == "grow"  # ungrouped rule: group defaults to rule id (IN-03)
    # NO-04: the outbox row that produced the popup is marked delivered
    ob = conn.execute("SELECT delivered_ts FROM outbox").fetchall()
    assert all(r["delivered_ts"] is not None for r in ob)

    # The 15m slope window drains slowly after growth stops, so the rule
    # legitimately stays TRUE past the first 5m backoff: renotifies between
    # open and recover are correct IN-02 behavior, not noise.
    tick_n(core, clock, 25)
    kinds = [n["kind"] for n in notifications(paths)]
    assert kinds[0] == "open" and kinds[-1] == "recover"
    assert kinds.count("recover") == 1
    assert set(kinds[1:-1]) <= {"renotify"}
    assert "recovered after" in notifications(paths)[-1]["body"]
    conn2 = connect(paths.db_file, readonly=True)
    row = conn2.execute("SELECT state, clear_reason FROM incidents").fetchone()
    assert row["state"] == "cleared" and row["clear_reason"] == "recovered"
    history = conn2.execute(
        "SELECT kind FROM incident_history ORDER BY seq"
    ).fetchall()
    kinds = [h["kind"] for h in history]
    assert "open" in kinds and "clear" in kinds and "notified" in kinds


def test_ack_suppresses_renotify_and_restart_resumes_backoff(core_env):
    """[IN-02] ack quiets an incident across the daemon's 30s ack-refresh;
    a restarted daemon rebuilds the open incident instead of re-firing."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    sampler = ScriptedSampler()
    for i in range(200):
        sampler.push(grower(i))
    core.samplers["process"] = sampler
    tick_n(core, clock, 8)
    assert [n["kind"] for n in notifications(paths)] == ["open"]
    incident_id = connect(paths.db_file, readonly=True).execute(
        "SELECT id FROM incidents"
    ).fetchone()["id"]

    # ack through the same path the CLI uses (PM-03 small write)
    wconn = connect(paths.db_file)
    assert SmallWrites(wconn).ack(incident_id, by="test", ts=clock.now())
    wconn.close()

    tick_n(core, clock, 60)  # 1h of sustained firing while acked
    assert [n["kind"] for n in notifications(paths)] == ["open"]  # silence

    # restart: rebuilt core must not re-send the open notification (NO-04)
    core2 = DaemonCore(paths=paths, clock=clock)
    core2.samplers["process"] = sampler
    tick_n(core2, clock, 3)
    assert [n["kind"] for n in notifications(paths)] == ["open"]
    assert core2._istates  # incident state was rebuilt, not forgotten


# --- outbox unit behavior (NO-04) ---


class ListNotifier:
    name = "list"

    def __init__(self):
        self.delivered: list[Notification] = []

    def deliver(self, n):
        self.delivered.append(n)


class BrokenNotifier:
    name = "broken"

    def deliver(self, n):
        raise NotifyError("channel down")


def _outbox_db(tmp_path, rows):
    conn = connect(tmp_path / "ob.db")
    migrate(conn)
    w = TickWriter(conn)
    for incident_id, kind, sev, created in rows:
        w.add_outbox(incident_id, kind,
                     {"severity": sev, "title": "t", "body": "b"}, created)
    w.commit_tick()
    return conn


def test_outbox_failed_channel_retries_next_flush(tmp_path):
    """[NO-04] a down channel leaves rows undelivered; they deliver on the
    next flush once a channel accepts them (no loss)."""
    conn = _outbox_db(tmp_path, [(1, "open", 3, 1000)])
    broken = Outbox(conn, [BrokenNotifier()])
    assert broken.flush(now=1001) == 0
    ok = ListNotifier()
    working = Outbox(conn, [BrokenNotifier(), ok])  # one dead channel is fine
    assert working.flush(now=1002) == 1
    assert len(ok.delivered) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE delivered_ts IS NULL"
    ).fetchone()[0] == 0


def test_outbox_recover_stale_vs_must_deliver(tmp_path):
    """[NO-04] startup: old warning-level rows go stale silently; an old
    error-opening row is delivered with a (delayed) prefix."""
    conn = _outbox_db(
        tmp_path,
        [(1, "renotify", 2, 1000),  # old warning renotify -> stale
         (2, "open", 3, 1000),      # old error open -> must deliver
         (3, "open", 2, 9500)],     # recent -> normal delivery
    )
    ok = ListNotifier()
    delivered, stale = Outbox(conn, [ok]).recover(now=10000)
    assert (delivered, stale) == (2, 1)
    bodies = [n.body for n in ok.delivered]
    assert any(b.startswith("(delayed) ") for b in bodies)
    assert conn.execute("SELECT COUNT(*) FROM outbox WHERE stale=1").fetchone()[0] == 1

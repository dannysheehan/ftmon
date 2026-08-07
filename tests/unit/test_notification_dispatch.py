"""[DM-18][NO-04][NO-06][NO-07][PM-12][TS-13] Durable channel dispatch."""

import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from email.utils import format_datetime

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.outbox import DispatchWorker, Outbox
from ftmon.store.writer import TickWriter


class RecordingNotifier:
    def __init__(self, name: str, outcomes=()):
        self.name = name
        self.outcomes = list(outcomes)
        self.delivered: list[Notification] = []

    def deliver(self, note: Notification) -> DeliveryResult:
        self.delivered.append(note)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return DeliveryResult()


def _db(tmp_path, channels, severity=3, created=1_000):
    conn = connect(tmp_path / "dispatch.db")
    migrate(conn)
    writer = TickWriter(conn, delivery_channels=channels)
    writer.add_outbox(1, "open", {"severity": severity, "title": "t", "body": "b"}, created)
    writer.commit_tick()
    return conn


def test_fanout_is_frozen_by_channel_severity_and_delivered_independently(tmp_path):
    """[DM-18][NO-06] Only eligible channels exist; one failure cannot hide another."""
    conn = _db(tmp_path, {"ntfy": 2, "smtp": 4})
    file = RecordingNotifier("file")
    ntfy = RecordingNotifier("ntfy", [RetryableDelivery("connection")])
    assert Outbox(conn, [file, ntfy]).flush(1_000) == 1
    rows = conn.execute(
        "SELECT channel,state,attempt_count,next_attempt_ts FROM notification_deliveries "
        "ORDER BY channel"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("file", "delivered", 1, None),
        ("ntfy", "pending", 1, 1_030),
    ]


def test_retry_schedule_and_retry_after_never_shorten_delay(tmp_path):
    """[NO-07] Every retry tier is exact; Retry-After only lengthens it."""
    conn = _db(tmp_path, {"ntfy": 0})
    ntfy = RecordingNotifier("ntfy", [
        RetryableDelivery("busy", retry_after="10"),
        RetryableDelivery("busy", retry_after="300"),
        RetryableDelivery("busy"),
        RetryableDelivery("busy"),
        RetryableDelivery("busy"),
    ])
    outbox = Outbox(conn, [RecordingNotifier("file"), ntfy])
    expected = [(1_000, 1_030), (1_030, 1_330), (1_330, 1_930),
                (1_930, 5_530), (5_530, 27_130)]
    for attempt, (now, due) in enumerate(expected, 1):
        outbox.flush(now)
        row = conn.execute(
            "SELECT attempt_count,next_attempt_ts FROM notification_deliveries "
            "WHERE channel='ntfy'"
        ).fetchone()
        assert tuple(row) == (attempt, due)


def test_retry_after_http_date_and_remote_lifetime_cutoff(tmp_path):
    """[NO-07] HTTP-date hints parse, but remote debt never exceeds 24h."""
    hint = format_datetime(datetime.fromtimestamp(2_000, UTC), usegmt=True)
    assert Outbox._retry_after(hint, 1_000) == 2_000
    conn = _db(tmp_path, {"ntfy": 0}, created=1_000)
    ntfy = RecordingNotifier("ntfy", [RetryableDelivery("busy", retry_after="90000")])
    Outbox(
        conn, [RecordingNotifier("file"), ntfy]
    ).flush(1_000)
    row = conn.execute(
        "SELECT state,next_attempt_ts FROM notification_deliveries WHERE channel='ntfy'"
    ).fetchone()
    assert tuple(row) == ("failed", None)


def test_permanent_remote_failure_is_terminal_and_file_is_unaffected(tmp_path):
    """[NO-07] Permanent failure records only a safe category/status."""
    terminal = []
    conn = _db(tmp_path, {"webhook": 0})
    webhook = RecordingNotifier(
        "webhook", [PermanentDelivery("http client", status_code=401)]
    )
    Outbox(
        conn, [RecordingNotifier("file"), webhook],
        on_terminal=lambda channel, reason: terminal.append((channel, reason)),
    ).flush(1_000)
    row = conn.execute(
        "SELECT state,last_error FROM notification_deliveries WHERE channel='webhook'"
    ).fetchone()
    assert tuple(row) == ("failed", "http client (401)")
    assert terminal == [("webhook", "http client (401)")]
    assert conn.execute(
        "SELECT state FROM notification_deliveries WHERE channel='file'"
    ).fetchone()[0] == "delivered"


def test_unexpected_adapter_error_is_redacted_and_cannot_kill_dispatch(tmp_path):
    """[SE-05][TS-13] Unknown exceptions persist only a fixed safe category."""
    conn = _db(tmp_path, {"webhook": 0})
    webhook = RecordingNotifier("webhook", [RuntimeError("token=do-not-store")])
    Outbox(conn, [RecordingNotifier("file"), webhook]).flush(1_000)
    row = conn.execute(
        "SELECT state,last_error FROM notification_deliveries WHERE channel='webhook'"
    ).fetchone()
    assert tuple(row) == ("failed", "adapter_internal")


def test_startup_resets_sending_and_may_redeliver_once(tmp_path):
    """[NO-04] Crash-interrupted claims become pending before startup drain."""
    conn = _db(tmp_path, {})
    conn.execute(
        "UPDATE notification_deliveries SET state='sending',attempt_count=1"
    )
    conn.commit()
    file = RecordingNotifier("file")
    assert Outbox(conn, [file]).recover(1_001) == (1, 0)
    assert len(file.delivered) == 1
    row = conn.execute(
        "SELECT state,attempt_count FROM notification_deliveries"
    ).fetchone()
    assert tuple(row) == ("delivered", 2)


def test_file_keeps_retrying_beyond_remote_deadline(tmp_path):
    """[NO-04][NO-07] Mandatory audit debt is never discarded after 24h."""
    conn = _db(tmp_path, {}, created=1_000)
    file = RecordingNotifier("file", [RetryableDelivery("storage")])
    Outbox(conn, [file]).flush(100_000)
    row = conn.execute(
        "SELECT state,next_attempt_ts FROM notification_deliveries"
    ).fetchone()
    assert tuple(row) == ("pending", 100_030)


def test_changed_channel_config_reloads_without_restarting_daemon(
    tmp_path, monkeypatch
):
    """[NO-10] A validated channel snapshot changes only future fan-out."""
    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    monkeypatch.setenv("NTFY_TOKEN", "secret")
    paths = get_paths()
    paths.ensure()
    paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
    paths.config_file.chmod(0o600)
    file = RecordingNotifier("file")
    ntfy = RecordingNotifier("ntfy")
    core = DaemonCore(
        paths=paths, clock=FakeClock(wall=1000, mono=1000),
        notifiers=[file, ntfy],
    )
    try:
        assert core.writer._delivery_channels == {"file": 0}
        previous = paths.config_file.stat().st_mtime_ns
        paths.config_file.write_text(
            "[notify.desktop]\nenabled=false\n"
            "[notify.ntfy]\nenabled=true\ntopic='host'\n"
            "token_env='NTFY_TOKEN'\n"
        )
        os.utime(paths.config_file, ns=(previous + 1_000_000, previous + 1_000_000))
        core._reload_channels()
        assert core.writer._delivery_channels == {"file": 0, "ntfy": 2}

        # A malformed subsequent edit keeps the last known-good channel set;
        # default desktop behavior must not replace it during a hand edit.
        previous = paths.config_file.stat().st_mtime_ns
        paths.config_file.write_text("not valid toml [[[")
        os.utime(paths.config_file, ns=(previous + 1_000_000, previous + 1_000_000))
        core._reload_channels()
        assert core.writer._delivery_channels == {"file": 0, "ntfy": 2}
    finally:
        core.conn.close()


def test_quiet_rows_become_one_durable_digest_for_all_owed_channels(tmp_path):
    """[NO-03][NO-06] Quiet decisions precede identical channel fan-out."""
    from ftmon.config import QuietHours

    midnight = 1_700_000_000 - (1_700_000_000 % 86400)
    night = midnight + 23 * 3600
    morning = midnight + 86400 + 9 * 3600
    conn = connect(tmp_path / "quiet.db")
    migrate(conn)
    writer = TickWriter(conn, delivery_channels={"ntfy": 2})
    writer.add_outbox(1, "open", {"severity": 2, "title": "disk", "body": "full"}, night)
    writer.add_outbox(
        2, "renotify", {"severity": 2, "title": "memory", "body": "growing"}, night + 60
    )
    writer.commit_tick()
    file, ntfy = RecordingNotifier("file"), RecordingNotifier("ntfy")
    outbox = Outbox(
        conn, [file, ntfy], quiet=QuietHours(22 * 60, 8 * 60, tz=UTC)
    )
    assert outbox.flush(night + 120) == 0
    assert outbox.flush(morning) == 2
    assert [note.kind for note in file.delivered] == ["digest"]
    assert [note.kind for note in ntfy.delivered] == ["digest"]
    assert conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE kind='digest'"
    ).fetchone()[0] == 1


def test_background_worker_uses_its_own_connection(tmp_path):
    """[NO-04][TS-13] Production dispatch wakes without using the tick connection."""
    db_path = tmp_path / "worker.db"
    conn = connect(db_path)
    migrate(conn)
    writer = TickWriter(conn)
    writer.add_outbox(1, "open", {"severity": 3, "title": "t", "body": "b"}, 1_000)
    writer.commit_tick()
    delivered = threading.Event()

    class SignallingFile(RecordingNotifier):
        def deliver(self, note):
            result = super().deliver(note)
            delivered.set()
            return result

    worker = DispatchWorker(db_path, [SignallingFile("file")], lambda: 1_000)
    worker.start()
    try:
        worker.wake()
        assert delivered.wait(2)
    finally:
        worker.stop()
    assert conn.execute(
        "SELECT state FROM notification_deliveries WHERE channel='file'"
    ).fetchone()[0] == "delivered"


def _wait(predicate, timeout=8.0):
    """Poll a worker-thread side effect without sleeping on a fixed guess."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _pending_delivery(tmp_path, name="pm12.db", severity=3, created=1_000):
    db_path = tmp_path / name
    conn = connect(db_path)
    migrate(conn)
    writer = TickWriter(conn)
    writer.add_outbox(1, "open", {"severity": severity, "title": "t", "body": "b"}, created)
    writer.commit_tick()
    return db_path, conn


def _state(conn, key="notify_dispatch_state"):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def test_worker_survives_real_lock_past_busy_timeout_pm_12(tmp_path):
    """[PM-12][NO-04] The issue #98 repro: lock > busy_timeout, then drain.

    Deliberately a real BEGIN IMMEDIATE rather than an injected exception —
    this is the exact path that killed the live thread, and only the driver
    itself produces the timeout message the classifier keys on.
    """
    db_path, conn = _pending_delivery(tmp_path)
    delivered = threading.Event()

    class SignallingFile(RecordingNotifier):
        def deliver(self, note):
            result = super().deliver(note)
            delivered.set()
            return result

    blocker = connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("UPDATE meta SET value=value WHERE key='none'")
    notifier = SignallingFile("file")
    worker = DispatchWorker(db_path, [notifier], lambda: 1_000)
    worker.start()
    try:
        worker.wake()
        # busy_timeout is 5 s; before the fix the thread was gone by now.
        assert _wait(lambda: not worker.alive(), timeout=7.0) is False
        assert worker.alive()
        blocker.rollback()
        blocker.close()
        worker.wake()
        assert delivered.wait(5)
    finally:
        worker.stop()
    assert conn.execute(
        "SELECT state FROM notification_deliveries WHERE channel='file'"
    ).fetchone()[0] == "delivered"
    assert len(notifier.delivered) == 1
    conn.close()


def test_daemon_core_starts_while_main_connection_is_locked_pm_12(tmp_path, monkeypatch):
    """[PM-12] Startup reset moved to the worker, so a main-DB lock cannot abort it.

    Regression for the removed `outbox.reset_inflight()` on the daemon's own
    connection: a lock there killed startup outright, before any recovery
    boundary existed.
    """
    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    paths = get_paths()
    paths.ensure()
    paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
    paths.config_file.chmod(0o600)
    seed = connect(paths.db_file)
    migrate(seed)
    writer = TickWriter(seed)
    writer.add_outbox(1, "open", {"severity": 3, "title": "t", "body": "b"}, 1_000)
    writer.commit_tick()
    seed.execute("UPDATE notification_deliveries SET state='sending'")
    seed.commit()
    delivered = threading.Event()

    class SignallingFile(RecordingNotifier):
        def deliver(self, note):
            result = super().deliver(note)
            delivered.set()
            return result

    blocker = connect(paths.db_file)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("UPDATE meta SET value=value WHERE key='none'")
    core = DaemonCore(
        paths=paths, clock=FakeClock(wall=2_000, mono=2_000),
        notifiers=[SignallingFile("file")], background_dispatch=True,
    )
    try:
        assert core.dispatch_worker is not None
        blocker.rollback()
        blocker.close()
        core.dispatch_worker.wake()
        # reset_inflight must have reclaimed the interrupted `sending` row.
        assert delivered.wait(8)
    finally:
        if core.dispatch_worker is not None:
            core.dispatch_worker.stop()
        core.conn.close()
    assert seed.execute(
        "SELECT state FROM notification_deliveries WHERE channel='file'"
    ).fetchone()[0] == "delivered"
    seed.close()


def test_worker_recovers_when_startup_reset_fails_pm_12(tmp_path, monkeypatch):
    """[PM-12] A fault in connect/migrate/reset recovers and leaks no connection."""
    db_path, conn = _pending_delivery(tmp_path)
    calls = []
    real_reset = Outbox.reset_inflight

    def flaky_reset(self):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_reset(self)

    monkeypatch.setattr(Outbox, "reset_inflight", flaky_reset)
    delivered = threading.Event()

    class SignallingFile(RecordingNotifier):
        def deliver(self, note):
            result = super().deliver(note)
            delivered.set()
            return result

    categories = []
    worker = DispatchWorker(
        db_path, [SignallingFile("file")], lambda: 1_000,
        on_store_error=categories.append,
    )
    worker.start()
    try:
        assert delivered.wait(8)
        assert categories == ["store_locked"]
    finally:
        worker.stop()
    assert len(calls) == 2
    assert _state(conn) == "stopped"
    conn.close()


def test_ack_failure_after_successful_send_redelivers_at_most_once_pm_12_no_04(
    tmp_path, monkeypatch
):
    """[PM-12][NO-04] A lock while recording success costs one duplicate, not the thread."""
    db_path, conn = _pending_delivery(tmp_path)
    marks = []
    real_mark = Outbox._mark_delivered

    def flaky_mark(self, row, now):
        marks.append(1)
        if len(marks) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_mark(self, row, now)

    monkeypatch.setattr(Outbox, "_mark_delivered", flaky_mark)
    notifier = RecordingNotifier("file")
    worker = DispatchWorker(db_path, [notifier], lambda: 1_000)
    worker.start()
    try:
        assert _wait(lambda: conn.execute(
            "SELECT state FROM notification_deliveries WHERE channel='file'"
        ).fetchone()[0] == "delivered")
    finally:
        worker.stop()
    # The documented duplicate window is exactly one redelivery, not a loop.
    assert len(notifier.delivered) == 2
    conn.close()


def test_large_wall_clock_jump_keeps_draining_pm_12(tmp_path):
    """[PM-12] Sleep/resume: debt owed before the jump still drains after it."""
    db_path, conn = _pending_delivery(tmp_path)
    clock = FakeClock(wall=1_000, mono=1_000)
    notifier = RecordingNotifier("file")
    worker = DispatchWorker(db_path, [notifier], clock.now)
    clock.advance(14 * 3600)  # the observed suspend window
    worker.start()
    try:
        assert _wait(lambda: len(notifier.delivered) == 1)
    finally:
        worker.stop()
    assert conn.execute(
        "SELECT state FROM notification_deliveries WHERE channel='file'"
    ).fetchone()[0] == "delivered"
    conn.close()


def test_corruption_is_fatal_and_publishes_dead_state_pm_12(tmp_path, monkeypatch):
    """[PM-12] A fault retrying cannot fix ends the thread visibly, not silently."""
    db_path, conn = _pending_delivery(tmp_path)

    def corrupt(self, now):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(Outbox, "flush", corrupt)
    fatal = []
    worker = DispatchWorker(
        db_path, [RecordingNotifier("file")], lambda: 1_000, on_fatal=fatal.append,
    )
    worker.start()
    try:
        assert _wait(lambda: not worker.alive())
    finally:
        worker.stop()
    assert fatal == ["store_corrupt"]
    assert _state(conn) == "dead"
    assert _state(conn, "notify_dispatch_last_error_category") == "store_corrupt"
    conn.close()


def test_stop_interrupts_recovery_backoff_pm_12(tmp_path, monkeypatch):
    """[PM-12] Backoff waits on the wakeup condition, so stop() stays responsive."""
    db_path, conn = _pending_delivery(tmp_path)

    def always_locked(self, now):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Outbox, "flush", always_locked)
    categories = []
    worker = DispatchWorker(
        db_path, [RecordingNotifier("file")], lambda: 1_000,
        on_store_error=categories.append,
    )
    worker.start()
    try:
        assert _wait(lambda: len(categories) >= 2)
        started = time.monotonic()
        worker.stop()
        assert time.monotonic() - started < 5.0
    finally:
        worker.stop()
    assert not worker.alive()
    assert set(categories) == {"store_locked"}
    conn.close()


def test_channels_stay_independent_across_recovery_dm_18_no_07(tmp_path, monkeypatch):
    """[DM-18][NO-07][PM-12] Recovery does not couple one channel's fate to another."""
    db_path = tmp_path / "independent.db"
    conn = connect(db_path)
    migrate(conn)
    writer = TickWriter(conn, delivery_channels={"ntfy": 2})
    writer.add_outbox(1, "open", {"severity": 3, "title": "t", "body": "b"}, 1_000)
    writer.commit_tick()
    flushes = []
    real_flush = Outbox.flush

    def flaky_flush(self, now):
        flushes.append(1)
        if len(flushes) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_flush(self, now)

    monkeypatch.setattr(Outbox, "flush", flaky_flush)
    file = RecordingNotifier("file")
    ntfy = RecordingNotifier("ntfy", [RetryableDelivery("connection")])
    worker = DispatchWorker(db_path, [file, ntfy], lambda: 1_000)
    worker.start()
    try:
        assert _wait(lambda: len(file.delivered) == 1)
    finally:
        worker.stop()
    rows = dict(conn.execute(
        "SELECT channel, state FROM notification_deliveries ORDER BY channel"
    ).fetchall())
    assert rows == {"file": "delivered", "ntfy": "pending"}
    conn.close()


def test_self_source_exposes_backlog_and_worker_liveness_no_10(tmp_path, monkeypatch):
    """[NO-10][PM-12] The self entity carries delivery health, not just channel config."""
    from ftmon.selfmon import SelfSampler, SelfStats
    from ftmon.sources.base import SOURCE_DECLS

    stats = SelfStats()
    stats.notify_pending_total = 4
    stats.notify_due_claimable = 3
    stats.notify_quiet_held = 1
    stats.notify_failed = 2
    stats.notify_oldest_claimable_due_age_s = 900.0
    stats.notify_worker_alive = 0.0
    stats.count("notify_store_errors")
    metrics = SelfSampler(stats).sample(1_000, 0.0, {}).entities[0].metrics
    assert metrics["notify_pending_total"] == 4
    assert metrics["notify_due_claimable"] == 3
    assert metrics["notify_quiet_held"] == 1
    assert metrics["notify_failed"] == 2
    assert metrics["notify_oldest_claimable_due_age_s"] == 900.0
    assert metrics["notify_worker_alive"] == 0.0
    assert metrics["notify_store_errors"] == 1
    # Undeclared metrics never project, so the decl is part of the contract.
    declared = {m.name for m in SOURCE_DECLS["self"].metrics}
    assert {name for name in metrics if name.startswith("notify_")} <= declared


def test_daemon_tick_folds_delivery_debt_into_self_stats_no_10(tmp_path, monkeypatch):
    """[NO-10] Backlog gauges are sampled on the daemon's own connection."""
    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    paths = get_paths()
    paths.ensure()
    paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
    paths.config_file.chmod(0o600)
    core = DaemonCore(
        paths=paths, clock=FakeClock(wall=5_000, mono=5_000),
        notifiers=[RecordingNotifier("file")],
    )
    try:
        core.writer.add_outbox(
            1, "open", {"severity": 3, "title": "t", "body": "b"}, 1_000
        )
        core.writer.commit_tick()
        core._sample_outbox_backlog(5_000)
        assert core.stats.notify_pending_total == 1
        assert core.stats.notify_due_claimable == 1
        assert core.stats.notify_oldest_claimable_due_age_s == 4_000
        assert core.stats.notify_worker_alive == 1.0
    finally:
        core.conn.close()

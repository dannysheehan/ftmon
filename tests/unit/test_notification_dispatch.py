"""[DM-18][NO-04][NO-06][NO-07][TS-13] Durable channel dispatch."""

import os
import threading
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

"""Durable, independent notification delivery (DM-18, NO-04..07).

The incident writer freezes one row for every eligible channel.  This module
then claims exactly one row in a short transaction, calls the adapter with no
SQLite transaction open, and records that channel's outcome independently.
The split is intentional: slow or broken networks must never extend the
daemon's sampling transaction or conceal successful local audit delivery.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Sequence
from email.utils import parsedate_to_datetime

from ftmon.config import QuietHours
from ftmon.model import Notification, severity_name
from ftmon.notify.base import Notifier, PermanentDelivery, RetryableDelivery

_QUIET_MAX_SEV = 2
_BODY_MAX = 200
_ERROR_MAX = 512
_REMOTE_LIFETIME = 86_400
_RETRY_DELAYS = (30, 120, 600, 3_600, 21_600)
_BACKOFF_START_S = 0.5
_BACKOFF_MAX_S = 5.0
_HEARTBEAT_EVERY_S = 30.0
_MAX_DISCONNECT_RETRIES = 5

#: Dispatcher states published to ``meta`` for `ftmon doctor` (PM-12). A
#: worker that has not yet opened its connection publishes nothing at all,
#: which doctor reads as unknown — indistinguishable from, and as broken as,
#: a worker still blocked on a startup lock.
DISPATCH_STATES = ("running", "recovering", "stopped", "dead")


class _Fatal(Exception):
    """Carries an already-decided category out of the connect path."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _classify(exc: BaseException) -> tuple[str, bool]:
    """Map a store fault to a fixed category and whether retrying can help.

    The category is a closed vocabulary rather than exception text because it
    reaches SQLite and the daemon log, where a message could carry a path or
    receiver-controlled content (SE-04). Retryable means "a different
    connection or a later moment plausibly succeeds" — contention and a
    severed handle. Corruption, a broken migration, and a failing device are
    not improved by waiting, so they end the thread visibly instead.
    """
    from ftmon.store.db import is_disconnected_error, is_locked_error

    if isinstance(exc, _Fatal):
        return exc.category, False
    if is_locked_error(exc):
        return "store_locked", True
    if is_disconnected_error(exc):
        return "store_disconnect", True
    if isinstance(exc, sqlite3.OperationalError):
        return "store_io", False
    if isinstance(exc, sqlite3.DatabaseError):
        return "store_corrupt", False
    return "store_bug", False


def backlog(
    conn: sqlite3.Connection, now: float, quiet: QuietHours | None = None
) -> dict[str, object]:
    """Quiet-aware view of durable delivery debt (NO-10), for doctor and self.

    Splitting `quiet_held` out of the pending total is what stops an overnight
    quiet window from reading as a stuck outbox: those rows are real debt but
    are deliberately unclaimable until `_materialize_digest` replaces them, so
    folding them into "overdue" would fire a nightly false alarm. The oldest
    age is likewise measured over claimable rows only — it answers "is anything
    draining?", which is the question a dead dispatcher makes urgent.
    """
    pending = conn.execute(
        "SELECT d.next_attempt_ts AS due_ts, n.created_ts, n.severity "
        "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
        "WHERE d.state='pending'"
    ).fetchall()
    def is_held(row: sqlite3.Row) -> bool:
        return (
            quiet is not None and int(row["severity"]) <= _QUIET_MAX_SEV
            and quiet.active(float(row["created_ts"]))
        )

    held = [row for row in pending if is_held(row)]
    due = [
        row for row in pending
        if not is_held(row) and row["due_ts"] is not None and float(row["due_ts"]) <= now
    ]
    oldest = max((now - float(row["due_ts"]) for row in due), default=0.0)
    failed_by_channel = {
        str(row["channel"]): int(row["n"]) for row in conn.execute(
            "SELECT channel, COUNT(*) AS n FROM notification_deliveries "
            "WHERE state='failed' GROUP BY channel ORDER BY channel"
        )
    }
    return {
        "pending_total": len(pending),
        "due_claimable": len(due),
        "quiet_held": len(held),
        "failed": sum(failed_by_channel.values()),
        "failed_by_channel": failed_by_channel,
        "oldest_claimable_due_age_s": max(0.0, oldest),
    }


def _close_quietly(conn: sqlite3.Connection | None) -> None:
    """Close a connection that may already be broken; always returns None."""
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001,S110 — the fault we are recovering from
            pass
    return None


class Outbox:
    """Synchronous dispatcher core.

    ``flush`` is deliberately deterministic for controlled-clock and unit
    tests.  A production worker can call the same method after a condition
    wake-up; there is only one claim at a time, so delivery ordering and crash
    recovery do not depend on thread scheduling.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        notifiers: Sequence[Notifier],
        quiet: QuietHours | None = None,
        on_terminal: Callable[[str, str], None] | None = None,
    ) -> None:
        self._conn = conn
        self._quiet = quiet
        self._on_terminal = on_terminal or (lambda _channel, _reason: None)
        self._notifiers = list(notifiers)
        self._by_channel: dict[str, Notifier] = {}
        self._legacy_file_chain: list[Notifier] = []
        for notifier in notifiers:
            name = notifier.name
            if name in self._by_channel:
                raise ValueError(f"duplicate notifier channel: {name}")
            self._by_channel[name] = notifier
        # Compatibility for injected pre-M8 test notifiers. Production always
        # supplies the explicitly named FileNotifier and never uses this path.
        if "file" not in self._by_channel:
            self._legacy_file_chain = list(notifiers)

    def recover(self, now: float) -> tuple[int, int]:
        """Reset crash-interrupted claims, then synchronously drain due work.

        A send may have completed before the crash, hence retrying a ``sending``
        row is the sole documented duplicate window rather than silent loss.
        The second tuple item remains for the pre-M8 caller API; stale dropping
        was removed because NO-07 now defines the terminal policy explicitly.
        """
        self.reset_inflight()
        return self.flush(now), 0

    def reset_inflight(self) -> None:
        self._conn.execute(
            "UPDATE notification_deliveries SET state='pending' WHERE state='sending'"
        )
        self._conn.commit()

    def flush(self, now: float) -> int:
        """Attempt every due delivery once, oldest notification first."""
        self._materialize_digest(now)
        completed = 0
        # The bound prevents a permanently failing file delivery, which is due
        # again only in the future, from causing a busy loop in this invocation.
        due_count = self._conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries "
            "WHERE state='pending' AND next_attempt_ts <= ?", (round(now),)
        ).fetchone()[0]
        for _ in range(due_count):
            row = self._claim_one(now)
            if row is None:
                break
            notifier = self._by_channel.get(str(row["channel"]))
            if notifier is None and row["channel"] == "file" and self._legacy_file_chain:
                outcome = self._deliver_legacy_file(row)
            elif notifier is None:
                outcome = (False, True, "channel unavailable", None)
            else:
                outcome = self._deliver(notifier, row, now)
            success, permanent, error, retry_after = outcome
            if success:
                self._mark_delivered(row, now)
                completed += 1
            else:
                self._mark_failed_or_retry(row, now, permanent, error, retry_after)
        return completed

    def _claim_one(self, now: float) -> sqlite3.Row | None:
        """Claim oldest due row in a transaction containing no adapter I/O."""
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            candidates = cur.execute(
                "SELECT n.*, d.channel, d.attempt_count FROM notifications n "
                "JOIN notification_deliveries d ON d.notification_id=n.id "
                "WHERE d.state='pending' AND d.next_attempt_ts <= ? "
                "ORDER BY n.created_ts, n.id, CASE d.channel WHEN 'file' THEN 0 ELSE 1 END, "
                "d.channel", (round(now),)
            ).fetchall()
            # Held rows are durable pending debt, but are not claimable until
            # the quiet window ends and _materialize_digest replaces them.
            row = next((candidate for candidate in candidates if not self._held(
                float(candidate["created_ts"]), int(candidate["severity"])
            )), None)
            if row is not None:
                cur.execute(
                    "UPDATE notification_deliveries SET state='sending', "
                    "attempt_count=attempt_count+1 WHERE notification_id=? AND channel=?",
                    (row["id"], row["channel"]),
                )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()


        return row

    @staticmethod
    def _notification(row: sqlite3.Row) -> Notification:
        return Notification(
            incident_id=int(row["incident_id"]), kind=str(row["kind"]),
            severity=int(row["severity"]), title=str(row["title"]),
            body=str(row["body"]), created_ts=float(row["created_ts"]),
            monitor=str(row["monitor"]), entity_id=str(row["entity_id"]),
        )

    def _deliver(
        self, notifier: Notifier, row: sqlite3.Row, now: float
    ) -> tuple[bool, bool, str, float | None]:
        try:
            notifier.deliver(self._notification(row))
        except (RetryableDelivery, PermanentDelivery) as exc:
            permanent = isinstance(exc, PermanentDelivery)
            category = exc.category
            status = exc.status_code
            if status is not None:
                category = f"{category} ({status})"
            return False, permanent, category[:_ERROR_MAX], self._retry_after(exc.retry_after, now)
        except Exception:
            # Adapter bugs or unexpected library errors must not kill the only
            # dispatcher thread. The fixed category records no exception text,
            # which could contain a credential or receiver-controlled content.
            return False, True, "adapter_internal", None
        return True, False, "", None  # DeliveryResult (or legacy None) is success

    @staticmethod
    def _retry_after(value: str | None, now: float) -> float | None:
        """Parse the two standard Retry-After forms; malformed hints vanish."""
        if value is None:
            return None
        try:
            seconds = int(value)
        except ValueError:
            try:
                return parsedate_to_datetime(value).timestamp()
            except (TypeError, ValueError, OverflowError):
                return None
        return now + seconds if seconds >= 0 else None

    def _deliver_legacy_file(self, row: sqlite3.Row) -> tuple[bool, bool, str, float | None]:
        for notifier in self._legacy_file_chain:
            outcome = self._deliver(notifier, row, float(row["created_ts"]))
            if outcome[0]:
                return outcome
        return False, False, "delivery error", None

    def _mark_delivered(self, row: sqlite3.Row, now: float) -> None:
        self._conn.execute(
            "UPDATE notification_deliveries SET state='delivered', delivered_ts=?, "
            "next_attempt_ts=NULL, last_error=NULL WHERE notification_id=? AND channel=?",
            (round(now), row["id"], row["channel"]),
        )
        self._conn.commit()

    def _mark_failed_or_retry(
        self, row: sqlite3.Row, now: float, permanent: bool, error: str,
        retry_after: float | None,
    ) -> None:
        channel = str(row["channel"])
        attempts = int(row["attempt_count"]) + 1  # claim increment is not in returned row
        deadline = int(row["created_ts"]) + _REMOTE_LIFETIME
        delay = _RETRY_DELAYS[min(attempts - 1, len(_RETRY_DELAYS) - 1)]
        next_attempt = round(now + delay)
        if retry_after is not None:
            next_attempt = max(next_attempt, round(retry_after))
        terminal = permanent or (channel != "file" and next_attempt > deadline)
        if terminal:
            self._conn.execute(
                "UPDATE notification_deliveries SET state='failed', next_attempt_ts=NULL, "
                "last_error=? WHERE notification_id=? AND channel=?",
                (error[:_ERROR_MAX], row["id"], channel),
            )
            self._conn.commit()
            self._on_terminal(channel, error[:_ERROR_MAX])
            return
        if channel != "file":
            next_attempt = min(next_attempt, deadline)
        self._conn.execute(
            "UPDATE notification_deliveries SET state='pending', next_attempt_ts=?, "
            "last_error=? WHERE notification_id=? AND channel=?",
            (next_attempt, error[:_ERROR_MAX], row["id"], channel),
        )
        self._conn.commit()

    def _held(self, created_ts: float, severity: int) -> bool:
        return (
            self._quiet is not None and severity <= _QUIET_MAX_SEV
            and self._quiet.active(created_ts)
        )

    def _materialize_digest(self, now: float) -> None:
        """Replace quiet-held obligations with one durable fan-out digest.

        Creating the digest deliveries and completing the individual held rows
        happens atomically. Thus a crash can resend the digest but can never
        expose the individual notifications or lose the durable digest debt.
        """
        if self._quiet is None or self._quiet.active(now):
            return
        held = self._conn.execute(
            "SELECT DISTINCT n.* FROM notifications n JOIN notification_deliveries d "
            "ON d.notification_id=n.id WHERE d.state='pending' ORDER BY n.created_ts, n.id"
        ).fetchall()
        held = [r for r in held if self._held(float(r["created_ts"]), int(r["severity"]))]
        if not held:
            return
        ids = [int(r["id"]) for r in held]
        marks = ",".join("?" for _ in ids)
        channels = self._conn.execute(
            f"SELECT DISTINCT channel FROM notification_deliveries "  # noqa: S608
            f"WHERE state='pending' AND notification_id IN ({marks}) ORDER BY channel", ids,
        ).fetchall()
        top = max(int(row["severity"]) for row in held)
        summary = "; ".join(str(row["title"]) for row in held)
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            next_id = cur.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM notifications"
            ).fetchone()[0]
            cur.execute(
                "INSERT INTO notifications(id,incident_id,kind,severity,title,body,monitor,"
                "entity_id,created_ts) VALUES (?,0,'digest',?,?,?,'','',?)",
                (next_id, top, f"ftmon: {len(held)} notification(s) held during quiet hours",
                 f"worst: {severity_name(top)} — {summary}"[:_BODY_MAX], round(now)),
            )
            cur.executemany(
                "INSERT INTO notification_deliveries(notification_id,channel,state,"
                "next_attempt_ts) VALUES (?,?,'pending',?)",
                [(next_id, row["channel"], round(now)) for row in channels],
            )
            cur.execute(
                f"UPDATE notification_deliveries SET state='delivered', delivered_ts=?, "  # noqa: S608
                f"next_attempt_ts=NULL, last_error=NULL WHERE state='pending' "
                f"AND notification_id IN ({marks})", (round(now), *ids),
            )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()


class DispatchWorker:
    """One background dispatcher with a lost-wakeup-safe one-second poll.

    PM-12: a store fault here must not kill the only delivery path.  Python
    never surfaces a thread's exception to the daemon loop, so an escaping
    ``OperationalError`` used to leave sampling healthy, ``wake()`` addressing a
    dead thread, and committed deliveries pending forever (issue #98).  Every
    store access therefore sits inside one recovery boundary, and liveness is
    published to ``meta`` because thread death is otherwise unobservable.
    """

    def __init__(
        self, db_file, notifiers: Sequence[Notifier], clock: Callable[[], float],
        quiet: QuietHours | None = None,
        on_terminal: Callable[[str, str], None] | None = None,
        on_store_error: Callable[[str], None] | None = None,
        on_fatal: Callable[[str], None] | None = None,
    ) -> None:
        self._db_file = db_file
        self._notifiers = tuple(notifiers)
        self._clock = clock
        self._quiet = quiet
        self._on_terminal = on_terminal
        self._on_store_error = on_store_error or (lambda _category: None)
        self._on_fatal = on_fatal or (lambda _category: None)
        self._wake = threading.Condition()
        self._stop = False
        self._last_heartbeat = 0.0
        self._thread = threading.Thread(target=self._run, name="ftmon-notify", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def wake(self) -> None:
        with self._wake:
            self._wake.notify()

    def reconfigure(
        self, notifiers: Sequence[Notifier], quiet: QuietHours | None
    ) -> None:
        """Apply a validated channel snapshot without starting a second worker.

        Updating under the same condition used for wakeups means a config reload
        cannot race by running two adapters for one claimed row. The current
        attempt finishes under its old snapshot; all later claims use the new
        one, which is the only safe boundary available around external I/O.
        """
        with self._wake:
            self._notifiers = tuple(notifiers)
            self._quiet = quiet
            self._wake.notify()

    def stop(self) -> None:
        with self._wake:
            self._stop = True
            self._wake.notify()
        self._thread.join(timeout=11)

    def _run(self) -> None:
        conn: sqlite3.Connection | None = None
        backoff = _BACKOFF_START_S
        disconnects = 0
        try:
            while True:
                try:
                    if conn is None:
                        conn = self._open()
                        self._publish(conn, "running", force=True)
                        backoff, disconnects = _BACKOFF_START_S, 0
                    if not self._drain(conn):
                        self._publish(conn, "stopped", force=True)
                        return
                except Exception as exc:  # noqa: BLE001 — classified below
                    category, retryable = _classify(exc)
                    if category == "store_disconnect":
                        disconnects += 1
                        # A connection we ourselves closed reads exactly like
                        # one the OS invalidated. Retrying that forever would
                        # hide a bug in this loop, so give up after a few.
                        retryable = disconnects <= _MAX_DISCONNECT_RETRIES
                    conn = _close_quietly(conn)
                    if not retryable:
                        self._die(category)
                        return
                    self._on_store_error(category)
                    self._report(category, "recovering")
                    if not self._pause(backoff):
                        self._report(category, "stopped")
                        return
                    backoff = min(backoff * 2, _BACKOFF_MAX_S)
        finally:
            _close_quietly(conn)

    def _open(self) -> sqlite3.Connection:
        """Connect, migrate, and reclaim interrupted claims as one unit.

        Every failure closes the connection before propagating: the pre-#98
        code ran this outside any ``try``, so a startup lock both killed the
        thread and leaked its handle until process exit.
        """
        from ftmon.store.db import connect, migrate

        # Connection construction inside the thread gives SQLite one clear
        # owner instead of weakening its same-thread safety check.
        conn = connect(self._db_file)
        try:
            try:
                migrate(conn)
            except Exception as exc:
                if _classify(exc)[1]:
                    raise
                raise _Fatal("store_migrate") from exc
            # PM-12/NO-04: reclaiming `sending` after a fault is the same
            # bounded duplicate window a restart already documents.
            Outbox(conn, self._notifiers, quiet=self._quiet).reset_inflight()
        except BaseException:
            _close_quietly(conn)
            raise
        return conn

    def _drain(self, conn: sqlite3.Connection) -> bool:
        """Flush due work until stop is requested; False means stop."""
        while True:
            with self._wake:
                if self._stop:
                    return False
                notifiers, quiet = self._notifiers, self._quiet
            dispatcher = Outbox(
                conn, notifiers, quiet=quiet, on_terminal=self._on_terminal
            )
            completed = dispatcher.flush(self._clock())
            # Forced only when something actually happened: an unconditional
            # write here would put a 1 Hz writer against the tick's
            # BEGIN IMMEDIATE and manufacture the PM-10 contention this
            # class exists to survive (DESIGN 10.7).
            self._publish(conn, "running", force=completed > 0)
            with self._wake:
                if self._stop:
                    return False
                self._wake.wait(timeout=1.0)

    def _pause(self, seconds: float) -> bool:
        """Interruptible backoff; False when stop was requested meanwhile.

        Waiting on the wakeup condition rather than sleeping keeps ``stop()``
        and ``reconfigure()`` responsive during recovery.
        """
        with self._wake:
            if self._stop:
                return False
            self._wake.wait(timeout=seconds)
            return not self._stop

    def _die(self, category: str) -> None:
        """Publish a durable dead state, then let the thread end.

        Retrying corruption or a failed migration forever would burn a core
        without ever delivering; the honest outcome is to stop and be visibly
        broken to `ftmon doctor` (PM-12).
        """
        self._report(category, "dead")
        self._on_fatal(category)

    def _report(self, category: str, state: str) -> None:
        """Record dispatcher state on a connection opened just for this.

        The working connection is gone by now — that is why we are here — and
        a diagnostic write must not become a second failure, so every error is
        swallowed. Losing the record degrades to doctor's missing-state
        predicate, which already fails.
        """
        from ftmon.store.db import connect

        conn = None
        try:
            conn = connect(self._db_file)
            self._publish(conn, state, category=category, force=True)
        except Exception:  # noqa: BLE001,S110 — best-effort diagnostics only
            pass
        finally:
            _close_quietly(conn)

    def _publish(
        self, conn: sqlite3.Connection, state: str, *,
        category: str | None = None, force: bool = False,
    ) -> None:
        now = self._clock()
        if not force and now - self._last_heartbeat < _HEARTBEAT_EVERY_S:
            return
        rows = [("notify_dispatch_state", state),
                ("notify_dispatch_heartbeat_ts", repr(now))]
        if category is not None:
            rows += [("notify_dispatch_last_error_category", category),
                     ("notify_dispatch_last_error_ts", repr(now))]
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", rows,
        )
        conn.commit()
        self._last_heartbeat = now

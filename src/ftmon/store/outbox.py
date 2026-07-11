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
    """One background dispatcher with a lost-wakeup-safe one-second poll."""

    def __init__(
        self, db_file, notifiers: Sequence[Notifier], clock: Callable[[], float],
        quiet: QuietHours | None = None,
        on_terminal: Callable[[str, str], None] | None = None,
    ) -> None:
        self._db_file = db_file
        self._notifiers = tuple(notifiers)
        self._clock = clock
        self._quiet = quiet
        self._on_terminal = on_terminal
        self._wake = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="ftmon-notify", daemon=True)

    def start(self) -> None:
        self._thread.start()

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
        from ftmon.store.db import connect, migrate

        # Connection construction inside the thread gives SQLite one clear
        # owner instead of weakening its same-thread safety check.
        conn = connect(self._db_file)
        migrate(conn)
        Outbox(conn, self._notifiers, quiet=self._quiet).reset_inflight()
        try:
            while True:
                with self._wake:
                    notifiers, quiet = self._notifiers, self._quiet
                dispatcher = Outbox(
                    conn, notifiers, quiet=quiet, on_terminal=self._on_terminal
                )
                dispatcher.flush(self._clock())
                with self._wake:
                    if self._stop:
                        return
                    self._wake.wait(timeout=1.0)
        finally:
            conn.close()

"""Outbox delivery: the at-least-once half of NO-04.

The row is committed with the incident transition (writer.add_outbox);
this module owns what happens after commit:

- `flush(now)` delivers undelivered rows and stamps delivered_ts in a small
  follow-up transaction. A crash between delivery and the stamp duplicates
  at most the one in-flight notification — that bound is the spec's honest
  guarantee, tested by TS-05's kill-9 case.
- `recover(now)` runs once at daemon startup: rows older than 10 minutes
  are stamped stale instead of fired (a wall of ancient popups after a
  reboot helps nobody) — EXCEPT incident-opening notifications of severity
  error+ which deliver with a "(delayed)" prefix; those are the ones a user
  must not miss (NO-04).

A failing notifier leaves the row undelivered for the next flush; one dead
channel (e.g. no desktop session) must not lose the audit-file copy, so
delivery counts as success if at least one notifier accepts it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from ftmon.model import Notification
from ftmon.notify.base import Notifier, NotifyError

_STALE_AFTER_S = 600.0


class Outbox:
    def __init__(self, conn: sqlite3.Connection, notifiers: Sequence[Notifier]):
        self._conn = conn
        self._notifiers = list(notifiers)

    def flush(self, now: float) -> int:
        """Deliver all undelivered, non-stale rows. Returns delivered count."""
        rows = self._conn.execute(
            "SELECT id, incident_id, kind, body, created_ts FROM outbox "
            "WHERE delivered_ts IS NULL AND stale = 0 ORDER BY id"
        ).fetchall()
        delivered = 0
        for row in rows:
            if self._deliver(row, prefix=""):
                self._mark_delivered(row["id"], now)
                delivered += 1
        return delivered

    def recover(self, now: float) -> tuple[int, int]:
        """Startup pass (NO-04). Returns (delivered_delayed, marked_stale)."""
        rows = self._conn.execute(
            "SELECT id, incident_id, kind, body, created_ts FROM outbox "
            "WHERE delivered_ts IS NULL AND stale = 0 ORDER BY id"
        ).fetchall()
        delayed = stale = 0
        for row in rows:
            age = now - row["created_ts"]
            body = json.loads(row["body"])
            must_deliver = row["kind"] == "open" and body.get("severity", 0) >= 3
            if age <= _STALE_AFTER_S or must_deliver:
                if self._deliver(row, prefix="(delayed) " if age > _STALE_AFTER_S else ""):
                    self._mark_delivered(row["id"], now)
                    delayed += 1
            else:
                self._conn.execute("UPDATE outbox SET stale = 1 WHERE id = ?", (row["id"],))
                self._conn.commit()
                stale += 1
        return delayed, stale

    def _deliver(self, row: sqlite3.Row, prefix: str) -> bool:
        body = json.loads(row["body"])
        n = Notification(
            incident_id=row["incident_id"],
            kind=row["kind"],
            severity=int(body.get("severity", 0)),
            title=str(body.get("title", "ftmon")),
            body=prefix + str(body.get("body", "")),
            created_ts=float(row["created_ts"]),
        )
        ok = False
        for notifier in self._notifiers:
            try:
                notifier.deliver(n)
                ok = True
            except NotifyError:
                continue  # per-channel failure; success = any channel took it
        return ok

    def _mark_delivered(self, outbox_id: int, now: float) -> None:
        self._conn.execute(
            "UPDATE outbox SET delivered_ts = ? WHERE id = ?", (round(now), outbox_id)
        )
        self._conn.commit()

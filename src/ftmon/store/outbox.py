"""Outbox delivery: the at-least-once half of NO-04, plus quiet hours (NO-03).

The row is committed with the incident transition (writer.add_outbox);
this module owns what happens after commit:

- `flush(now)` delivers undelivered rows and stamps delivered_ts in a small
  follow-up transaction. A crash between delivery and the stamp duplicates
  at most the one in-flight notification — that bound is the spec's honest
  guarantee, tested by TS-05's kill-9 case.
- Quiet hours (NO-03) live entirely here because they are a *delivery*
  concern: incidents open/escalate/clear regardless. During quiet hours,
  warning-and-below rows are simply left undelivered (held); error+ rows
  pass through. Once quiet ends, held rows go out as one digest
  notification and are stamped delivered — held rows are identified by
  "created while quiet was active", which also covers rows that waited
  across a daemon restart or a whole skipped day.
- `recover(now)` runs once at daemon startup: rows older than 10 minutes
  are stamped stale instead of fired (a wall of ancient popups after a
  reboot helps nobody) — EXCEPT incident-opening notifications of severity
  error+ which deliver with a "(delayed)" prefix, and quiet-held rows,
  which must survive to be digested rather than silently staled.

A failing notifier leaves the row undelivered for the next flush; one dead
channel (e.g. no desktop session) must not lose the audit-file copy, so
delivery counts as success if at least one notifier accepts it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from ftmon.config import QuietHours
from ftmon.model import Notification, severity_name
from ftmon.notify.base import Notifier, NotifyError

_STALE_AFTER_S = 600.0
_QUIET_MAX_SEV = 2  # NO-03: warning-and-below held; error+ always through
_BODY_MAX = 200  # NO-01


class Outbox:
    def __init__(
        self,
        conn: sqlite3.Connection,
        notifiers: Sequence[Notifier],
        quiet: QuietHours | None = None,
    ):
        self._conn = conn
        self._notifiers = list(notifiers)
        self._quiet = quiet

    def _held(self, row: sqlite3.Row, severity: int) -> bool:
        return (
            self._quiet is not None
            and severity <= _QUIET_MAX_SEV
            and self._quiet.active(row["created_ts"])
        )

    def flush(self, now: float) -> int:
        """Deliver all undelivered, non-stale rows. Returns delivered count
        (a digest counts as one)."""
        rows = self._conn.execute(
            "SELECT id, incident_id, kind, body, created_ts FROM outbox "
            "WHERE delivered_ts IS NULL AND stale = 0 ORDER BY id"
        ).fetchall()
        delivered = 0
        quiet_now = self._quiet is not None and self._quiet.active(now)
        digestable: list[sqlite3.Row] = []
        for row in rows:
            body = json.loads(row["body"])
            if self._held(row, int(body.get("severity", 0))):
                if quiet_now:
                    continue  # hold: quiet is still on
                digestable.append(row)
                continue
            if self._deliver(row, prefix=""):
                self._mark_delivered(row["id"], now)
                delivered += 1
        if digestable and self._deliver_digest(digestable, now):
            for row in digestable:
                self._mark_delivered(row["id"], now)
            delivered += 1
        return delivered

    def _deliver_digest(self, rows: list[sqlite3.Row], now: float) -> bool:
        bodies = [json.loads(r["body"]) for r in rows]
        top = max(int(b.get("severity", 0)) for b in bodies)
        summary = "; ".join(str(b.get("title", "ftmon")) for b in bodies)
        n = Notification(
            incident_id=0,  # a digest spans incidents; detail is in each one's history
            kind="digest",
            severity=top,
            title=f"ftmon: {len(rows)} notification(s) held during quiet hours",
            body=f"worst: {severity_name(top)} — {summary}"[:_BODY_MAX],
            created_ts=now,
        )
        ok = False
        for notifier in self._notifiers:
            try:
                notifier.deliver(n)
                ok = True
            except NotifyError:
                continue
        return ok

    def recover(self, now: float) -> tuple[int, int]:
        """Startup pass (NO-04). Returns (delivered, marked_stale)."""
        rows = self._conn.execute(
            "SELECT id, incident_id, kind, body, created_ts FROM outbox "
            "WHERE delivered_ts IS NULL AND stale = 0 ORDER BY id"
        ).fetchall()
        delivered = stale = 0
        for row in rows:
            age = now - row["created_ts"]
            body = json.loads(row["body"])
            if self._held(row, int(body.get("severity", 0))):
                continue  # NO-03: flush() digests these; staling would lose them
            must_deliver = row["kind"] == "open" and body.get("severity", 0) >= 3
            if age <= _STALE_AFTER_S or must_deliver:
                if self._deliver(row, prefix="(delayed) " if age > _STALE_AFTER_S else ""):
                    self._mark_delivered(row["id"], now)
                    delivered += 1
            else:
                self._conn.execute("UPDATE outbox SET stale = 1 WHERE id = ?", (row["id"],))
                self._conn.commit()
                stale += 1
        return delivered, stale

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

"""TickWriter: the daemon's single bulk writer (DESIGN.md section 2/8, PM-03).

Exactly one write transaction is committed per tick. Calls that need to
hand back a value the same tick (`series_id`, `add_event`) allocate that
value deterministically in-process (from a cached/queried next-id counter)
so they never have to touch disk before `commit_tick()`; everything is
executed with `executemany()` and committed together in `commit_tick()`.

No direct clock reads here (TS-03) — all timestamps are passed in.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Mapping

from ftmon.model import EventRecord

__all__ = ["TickWriter"]

_ATTRS_CAP = 4096


def _cap_attrs(attrs: Mapping[str, str]) -> str:
    """DM-03: JSON-encode attrs, capped at 4096 bytes.

    If the plain encoding is oversize, add a `"truncated":"true"` marker and
    drop the entries with the longest values, one at a time, until the
    result fits.
    """
    d: dict[str, str] = dict(attrs)
    encoded = json.dumps(d, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) <= _ATTRS_CAP:
        return encoded

    d["truncated"] = "true"
    while True:
        encoded = json.dumps(d, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) <= _ATTRS_CAP:
            return encoded
        droppable = [k for k in d if k != "truncated"]
        if not droppable:
            # Pathological: even the bare marker doesn't fit. Truncate raw
            # bytes as a last resort rather than raise.
            raw = encoded.encode("utf-8")[:_ATTRS_CAP]
            return raw.decode("utf-8", errors="ignore")
        longest = max(droppable, key=lambda k: len(d[k]))
        del d[longest]


class TickWriter:
    def __init__(
        self,
        conn: sqlite3.Connection,
        on_reject: Callable[[str], None] = lambda _reason: None,
        delivery_channels: Mapping[str, int] | None = None,
    ) -> None:
        self._conn = conn
        self._on_reject = on_reject
        # File is an invariant, not configuration: it is the durable local
        # audit proof required by NO-04.  Optional channels are frozen here so
        # a later config edit cannot mutate delivery obligations already
        # committed with an incident transition (DM-18).
        self._delivery_channels = {"file": 0}
        if delivery_channels is not None:
            self._delivery_channels.update(delivery_channels)

        self._series_cache: dict[tuple[str, str, str], int] = {}
        self._next_series_id: int | None = None
        self._next_event_id: int | None = None

        self._pending_series: list[tuple[int, str, str, str, int]] = []
        self._pending_samples: list[tuple[int, int, float]] = []
        self._pending_entities: list[tuple[str, str, int, int, int | None, str]] = []
        self._pending_events: list[tuple] = []
        self._pending_cursors: dict[str, tuple[str, int]] = {}
        self._pending_meta: dict[str, str] = {}
        self._pending_monitor_loads: list[tuple[str, int, str, str]] = []
        # incidents keyed by id so open+update within one tick collapses to
        # the final row (the state machine may open and escalate same-tick)
        self._pending_incidents: dict[int, tuple] = {}
        self._pending_history: list[tuple[int, int, int, str, str]] = []
        self._pending_outbox: list[tuple[int, int, str, int, str, str, str, str, int]] = []
        self._next_incident_id: int | None = None
        self._next_outbox_id: int | None = None
        self._history_seq: dict[int, int] = {}

    def set_delivery_channels(self, channels: Mapping[str, int]) -> None:
        """Replace optional fan-out policy before notifications are buffered.

        Daemon composition resolves channel readiness after constructing the
        writer. Refusing a mid-tick change keeps one notification's frozen set
        internally consistent across a configuration reload.
        """
        if self._pending_outbox:
            raise RuntimeError("cannot change delivery channels with pending notifications")
        self._delivery_channels = {"file": 0, **channels}

    # -- id allocation -----------------------------------------------

    def series_id(self, monitor: str, entity_id: str, metric: str, durable: bool) -> int:
        key = (monitor, entity_id, metric)
        cached = self._series_cache.get(key)
        if cached is not None:
            return cached

        row = self._conn.execute(
            "SELECT id FROM series WHERE monitor=? AND entity_id=? AND metric=?",
            key,
        ).fetchone()
        if row is not None:
            self._series_cache[key] = row["id"]
            return row["id"]

        if self._next_series_id is None:
            (max_id,) = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM series").fetchone()
            self._next_series_id = max_id + 1
        new_id = self._next_series_id
        self._next_series_id += 1

        self._pending_series.append((new_id, monitor, entity_id, metric, int(durable)))
        self._series_cache[key] = new_id
        return new_id

    def _alloc_event_id(self) -> int:
        if self._next_event_id is None:
            (max_id,) = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            self._next_event_id = max_id + 1
        new_id = self._next_event_id
        self._next_event_id += 1
        return new_id

    # -- buffered writes -----------------------------------------------

    def add_sample(self, series_id: int, ts: float, value: float) -> None:
        """DM-01: NaN/inf values are rejected (counted), never stored."""
        if math.isnan(value) or math.isinf(value):
            self._on_reject("samples_rejected")
            return
        self._pending_samples.append((series_id, round(ts), value))

    def upsert_entity(
        self,
        monitor: str,
        entity_id: str,
        ts: float,
        attrs: dict[str, str],
        gone_ts: float | None = None,
    ) -> None:
        """DM-03: first_seen only takes effect on the initial insert (the
        UPSERT below never assigns to it on conflict); last_seen and gone_ts
        always reflect this call's values."""
        attrs_json = _cap_attrs(attrs)
        self._pending_entities.append(
            (
                monitor,
                entity_id,
                round(ts),
                round(ts),
                round(gone_ts) if gone_ts is not None else None,
                attrs_json,
            )
        )

    def add_event(self, ev: EventRecord) -> int:
        event_id = self._alloc_event_id()
        self._pending_events.append(
            (
                event_id,
                round(ev.ts),
                round(ev.ingest_ts),
                ev.source,
                ev.provider,
                ev.event_id,
                ev.severity,
                ev.message,
                json.dumps(dict(ev.attrs), ensure_ascii=False, sort_keys=True),
            )
        )
        return event_id

    def set_cursor(self, source: str, cursor: str, ts: float) -> None:
        """DM-15."""
        self._pending_cursors[source] = (cursor, round(ts))

    # -- incidents / outbox (DM-11..14, NO-04) --------------------------

    def alloc_incident_id(self) -> int:
        """Ids are allocated in-process (same rationale as series/events):
        the effect executor needs the id inside the tick, before commit."""
        if self._next_incident_id is None:
            (max_id,) = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM incidents"
            ).fetchone()
            self._next_incident_id = max_id + 1
        new_id = self._next_incident_id
        self._next_incident_id += 1
        return new_id

    def upsert_incident(
        self,
        incident_id: int,
        monitor: str,
        group: str,
        entity_id: str,
        *,
        state: str,
        severity: int,
        owning_rule: str,
        opened_ts: float,
        last_change_ts: float,
        cleared_ts: float | None,
        clear_reason: str | None,
        ack_by: str | None,
        ack_ts: float | None,
        notify_count: int,
        occurrences: int,
        flapping: bool,
    ) -> None:
        """Last write per (id) in a tick wins — see _pending_incidents."""
        self._pending_incidents[incident_id] = (
            incident_id, monitor, group, entity_id, state, severity, owning_rule,
            round(opened_ts), round(last_change_ts),
            round(cleared_ts) if cleared_ts is not None else None,
            clear_reason, ack_by,
            round(ack_ts) if ack_ts is not None else None,
            notify_count, occurrences, int(flapping),
        )

    def add_incident_history(self, incident_id: int, ts: float, kind: str,
                             detail: Mapping) -> None:
        """DM-12; the DM-13 cap (500 entries, oldest summarized) is enforced
        by retention, not per-write."""
        seq = self._history_seq.get(incident_id)
        if seq is None:
            (max_seq,) = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM incident_history WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
            seq = max_seq
        seq += 1
        self._history_seq[incident_id] = seq
        self._pending_history.append(
            (incident_id, seq, round(ts), kind,
             json.dumps(dict(detail), ensure_ascii=False, sort_keys=True))
        )

    def add_outbox(self, incident_id: int, kind: str, body: Mapping, created_ts: float) -> int:
        """NO-04: committed in the same transaction as the incident
        transition that caused it; delivery happens post-commit."""
        # The dispatcher may atomically materialize a quiet-hours digest on
        # its own connection between ticks. Refreshing the floor prevents its
        # durable notification id from colliding with this writer's cache.
        (max_id,) = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM notifications"
        ).fetchone()
        self._next_outbox_id = max(max_id + 1, self._next_outbox_id or 1)
        new_id = self._next_outbox_id
        self._next_outbox_id += 1
        rendered = dict(body)
        pending_incident = self._pending_incidents.get(incident_id)
        if pending_incident is not None:
            monitor, entity_id = pending_incident[1], pending_incident[3]
        else:
            incident = self._conn.execute(
                "SELECT monitor, entity_id FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            # Tests and import tools may enqueue against an absent incident;
            # retain that historical tolerance without weakening atomicity.
            monitor = incident["monitor"] if incident is not None else ""
            entity_id = incident["entity_id"] if incident is not None else ""
        self._pending_outbox.append(
            (new_id, incident_id, kind, int(rendered.get("severity", 0)),
             str(rendered.get("title", "ftmon")), str(rendered.get("body", "")),
             monitor, entity_id, round(created_ts))
        )
        return new_id

    def set_meta(self, key: str, value: str) -> None:
        self._pending_meta[key] = value

    def record_monitor_load(
        self, monitor: str, loaded_ts: float, content_hash: str, normalized: str
    ) -> None:
        """PM-07: keep the last 20 loads per monitor."""
        self._pending_monitor_loads.append((monitor, round(loaded_ts), content_hash, normalized))

    # -- commit -----------------------------------------------

    def commit_tick(self) -> None:
        """Execute everything buffered since the last commit_tick() and
        commit it as a single transaction (PM-03)."""
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            if self._pending_series:
                cur.executemany(
                    "INSERT INTO series(id, monitor, entity_id, metric, durable) "
                    "VALUES (?, ?, ?, ?, ?)",
                    self._pending_series,
                )
            if self._pending_samples:
                cur.executemany(
                    "INSERT OR REPLACE INTO samples(series_id, ts, value) VALUES (?, ?, ?)",
                    self._pending_samples,
                )
            if self._pending_entities:
                cur.executemany(
                    """
                    INSERT INTO entities(monitor, entity_id, first_seen, last_seen, gone_ts, attrs)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(monitor, entity_id) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        gone_ts = excluded.gone_ts,
                        attrs = excluded.attrs
                    """,
                    self._pending_entities,
                )
            if self._pending_events:
                cur.executemany(
                    """
                    INSERT INTO events(id, ts, ingest_ts, source, provider, event_id,
                                        severity, message, attrs)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._pending_events,
                )
            if self._pending_cursors:
                cur.executemany(
                    """
                    INSERT INTO cursors(source, cursor, updated_ts) VALUES (?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        cursor = excluded.cursor, updated_ts = excluded.updated_ts
                    """,
                    [
                        (source, cursor, ts)
                        for source, (cursor, ts) in self._pending_cursors.items()
                    ],
                )
            if self._pending_meta:
                cur.executemany(
                    """
                    INSERT INTO meta(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    list(self._pending_meta.items()),
                )
            if self._pending_incidents:
                cur.executemany(
                    """
                    INSERT INTO incidents(id, monitor, grp, entity_id, state, severity,
                        owning_rule, opened_ts, last_change_ts, cleared_ts, clear_reason,
                        ack_by, ack_ts, notify_count, occurrences, flapping)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        state = excluded.state, severity = excluded.severity,
                        owning_rule = excluded.owning_rule,
                        last_change_ts = excluded.last_change_ts,
                        cleared_ts = excluded.cleared_ts,
                        clear_reason = excluded.clear_reason,
                        ack_by = excluded.ack_by, ack_ts = excluded.ack_ts,
                        notify_count = excluded.notify_count,
                        occurrences = excluded.occurrences,
                        flapping = excluded.flapping
                    """,
                    list(self._pending_incidents.values()),
                )
            if self._pending_history:
                cur.executemany(
                    "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) "
                    "VALUES (?, ?, ?, ?, ?)",
                    self._pending_history,
                )
            if self._pending_outbox:
                cur.executemany(
                    "INSERT INTO notifications(id, incident_id, kind, severity, title, body, "
                    "monitor, entity_id, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._pending_outbox,
                )
                deliveries = [
                    (row[0], channel, row[8])
                    for row in self._pending_outbox
                    for channel, min_severity in self._delivery_channels.items()
                    if row[3] >= min_severity
                ]
                # Fan-out eligibility is deliberately evaluated while the
                # notification is still buffered: the complete immutable set
                # is therefore committed in the incident transaction.
                cur.executemany(
                    "INSERT INTO notification_deliveries(notification_id, channel, state, "
                    "next_attempt_ts) VALUES (?, ?, 'pending', ?)",
                    deliveries,
                )
            if self._pending_monitor_loads:
                cur.executemany(
                    "INSERT OR REPLACE INTO monitor_loads(monitor, loaded_ts, hash, normalized) "
                    "VALUES (?, ?, ?, ?)",
                    self._pending_monitor_loads,
                )
                touched = {row[0] for row in self._pending_monitor_loads}
                for monitor in touched:
                    cur.execute(
                        """
                        DELETE FROM monitor_loads WHERE (monitor, loaded_ts) IN (
                            SELECT monitor, loaded_ts FROM (
                                SELECT monitor, loaded_ts,
                                       ROW_NUMBER() OVER (
                                           PARTITION BY monitor ORDER BY loaded_ts DESC
                                       ) AS rn
                                FROM monitor_loads
                                WHERE monitor = ?
                            )
                            WHERE rn > 20
                        )
                        """,
                        (monitor,),
                    )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        finally:
            self._pending_series.clear()
            self._pending_samples.clear()
            self._pending_entities.clear()
            self._pending_events.clear()
            self._pending_cursors.clear()
            self._pending_meta.clear()
            self._pending_monitor_loads.clear()
            self._pending_incidents.clear()
            self._pending_history.clear()
            self._pending_outbox.clear()

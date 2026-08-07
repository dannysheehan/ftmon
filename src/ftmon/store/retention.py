"""Rollups, retention, degradation, and baselines (DM-04, DM-05, CA-05).

Runs inside the daemon after each tick commit as one short transaction of
its own. Everything is *incremental*: rollup progress lives in ``meta``
cursors (``rollup5m_cursor``, ``rollup1h_cursor``) so a pass never rescans
history, and each pass is bounded (bucket span + delete batch caps) to keep
the DM-04 promise of ≤ 1 s of retention work per cycle even on a database
that has been offline for weeks — catch-up happens over many ticks, not one.

Baselines (CA-05) are updated here, not in the sampling path, deliberately:
the spec defines the baseline as a function of 5-minute rollups (one EW-mean
step per rollup with fixed Δt = 300 s), so producing the rollup and stepping
the baseline in the same pass is the only ordering that cannot double-count
or skip a bucket.

Degradation (DM-05) uses *used* pages (page_count − freelist) so space that
incremental_vacuum has already reclaimed-in-place does not retrigger prunes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from ftmon.store.db import DB_BUDGET_BYTES

__all__ = ["Retention", "BaselineLookup"]

# CA-05: fixed-Δt EW mean; α = 1 − 2^(−Δt/half_life), Δt = one 5-min rollup.
ROLLUP5M_S = 300
ROLLUP1H_S = 3600
BASELINE_HALF_LIFE_S = 3 * 86400.0
BASELINE_MIN_UPDATES = 240  # ~24h of actual data — counted updates, not elapsed time

_DAY = 86400

# DM-13: incident_history is capped per incident; on overflow the oldest
# batch collapses into one summary entry rather than growing unboundedly.
HISTORY_CAP = 500
HISTORY_TRIM = 100

# MD-09: rows *visited* per reap pass, not rows deleted -- bounds the pass
# the same way regardless of how much of the catalog currently qualifies.
REAP_SCAN = 2000


class Retention:
    """One instance per daemon; ``run(now)`` is the whole public surface.

    Keep-windows and the size budget are constructor parameters so tests can
    shrink them to seconds/kilobytes; the defaults are the spec values.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        budget_bytes: int = DB_BUDGET_BYTES,  # DM-05
        raw_keep_s: int = 48 * 3600,  # DM-04
        r5m_keep_s: int = 30 * _DAY,
        r1h_keep_durable_s: int = 400 * _DAY,
        r1h_keep_process_s: int = 90 * _DAY,
        events_keep_s: int = 30 * _DAY,  # DM-09
        half_life_s: float = BASELINE_HALF_LIFE_S,
        # Catch-up bound: one pass never rolls more than an hour of buckets.
        # 12 buckets x ~800 series stays well inside DM-04's 1 s/cycle even
        # on the worst realistic desktop; a 48 h backlog clears in ~48
        # retention passes (~48 min at the daemon's cadence), by design.
        max_bucket_span_s: int = 3600,
        delete_batch: int = 5000,  # prune rows per table per pass
        reap_scan: int = REAP_SCAN,  # entities visited per catalog-reap pass
    ) -> None:
        self._conn = conn
        self._budget = budget_bytes
        self._raw_keep = raw_keep_s
        self._r5m_keep = r5m_keep_s
        self._r1h_keep_durable = r1h_keep_durable_s
        self._r1h_keep_process = r1h_keep_process_s
        self._events_keep = events_keep_s
        if half_life_s <= 0:
            raise ValueError("half_life_s must be positive")
        self._half_life_s = float(half_life_s)
        self._alpha = 1.0 - 2.0 ** (-ROLLUP5M_S / half_life_s)
        self._span = max_bucket_span_s
        self._batch = delete_batch
        self._reap_scan = reap_scan
        self.baselines_updated = 0  # daemon invalidates the lookup cache when > 0
        self.entities_reaped = 0  # daemon emits a self-event when > 0
        self.reaped_keys: list[tuple[str, str]] = []  # (monitor, entity_id);
        # daemon evicts the writer's series-id cache and the baseline lookup
        # cache for each -- reap runs on its own connection/transaction, so
        # nothing else notices these rows are gone (finding: issue #74 review)

    def run(self, now: float) -> list[str]:
        """One bounded retention pass. Returns human-readable notes for any
        DM-05 degradation steps taken (the daemon records them as self-events;
        normal rollup/prune activity is silent)."""
        self.baselines_updated = 0
        self.entities_reaped = 0
        self.reaped_keys = []
        notes: list[str] = []
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            self._rollup_5m(cur, now)
            self._rollup_1h(cur, now)
            self._prune_normal(cur, now)
            self._reap_catalog(cur, now)
            self._cap_incident_history(cur)
            notes = self._degrade_if_over_budget(cur, now)
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        # Reclaim freed pages outside the transaction, a bounded chunk per
        # pass (DM-05: incremental_vacuum after prune batches, never a stall).
        self._conn.execute("PRAGMA incremental_vacuum(200)")
        return notes

    # -- rollups (DM-04) -------------------------------------------------

    def _meta_int(self, cur: sqlite3.Cursor, key: str, default: int) -> int:
        row = cur.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return int(row["value"]) if row is not None else default

    def _meta_str(self, cur: sqlite3.Cursor, key: str, default: str) -> str:
        row = cur.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def _set_meta(self, cur: sqlite3.Cursor, key: str, value: int | str) -> None:
        cur.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def _rollup_5m(self, cur: sqlite3.Cursor, now: float) -> None:
        """Roll complete 5-minute buckets of raw samples, then step each
        touched series' baseline once per new bucket (CA-05)."""
        # A bucket is rollable only once it can no longer receive samples:
        # strictly below the current bucket, minus slack for a late writer.
        watermark = (int(now - 30) // ROLLUP5M_S) * ROLLUP5M_S
        start = self._meta_int(cur, "rollup5m_cursor", 0)
        if start == 0:
            row = cur.execute("SELECT MIN(ts) FROM samples").fetchone()
            if row[0] is None:
                return
            start = (int(row[0]) // ROLLUP5M_S) * ROLLUP5M_S
        end = min(watermark, start + self._span)  # bounded catch-up
        if end <= start:
            return
        # SQLite guarantee (documented since 3.7.11): with a bare column next
        # to max(ts), the bare value comes from the max-ts row — that is the
        # bucket's `last`.
        rows = cur.execute(
            """
            SELECT series_id, (ts / ?) * ? AS bucket,
                   AVG(value) AS avg, MIN(value) AS min, MAX(value) AS max,
                   MAX(ts), value AS last, COUNT(*) AS cnt
            FROM samples WHERE ts >= ? AND ts < ?
            GROUP BY series_id, bucket
            """,
            (ROLLUP5M_S, ROLLUP5M_S, start, end),
        ).fetchall()
        if rows:
            cur.executemany(
                "INSERT OR REPLACE INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["series_id"], r["bucket"], r["avg"], r["min"], r["max"],
                  r["last"], r["cnt"]) for r in rows],
            )
            self._update_baselines(cur, rows)
        self._set_meta(cur, "rollup5m_cursor", end)

    def _update_baselines(self, cur: sqlite3.Cursor, rollup_rows: list) -> None:
        """CA-05: b ← b + α·(rollup_avg − b), one update per 5-min rollup.
        The updated_bucket guard makes replays (crash between commit and
        cursor advance is impossible here — same txn — but INSERT OR REPLACE
        of a re-rolled bucket is not) idempotent per bucket."""
        # Buckets must apply in time order per series or the EW mean would
        # weight history wrongly during catch-up.
        ordered = sorted(rollup_rows, key=lambda r: (r["series_id"], r["bucket"]))
        for r in ordered:
            row = cur.execute(
                "SELECT value, updates, updated_bucket, half_life_s "
                "FROM baselines WHERE series_id = ?",
                (r["series_id"],),
            ).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO baselines(series_id, value, updates, updated_bucket, "
                    "half_life_s) VALUES (?, ?, 1, ?, ?)",
                    (r["series_id"], r["avg"], r["bucket"], self._half_life_s),
                )
                self.baselines_updated += 1
            elif r["bucket"] > row["updated_bucket"]:
                if row["half_life_s"] != self._half_life_s:
                    # Changing alpha makes older states impossible to reverse
                    # without a history table. Treat the first bucket under the
                    # new policy as a fresh seed, matching an operator reset.
                    cur.execute(
                        "UPDATE baselines SET value = ?, updates = 1, "
                        "updated_bucket = ?, half_life_s = ? WHERE series_id = ?",
                        (r["avg"], r["bucket"], self._half_life_s, r["series_id"]),
                    )
                else:
                    new_value = row["value"] + self._alpha * (r["avg"] - row["value"])
                    cur.execute(
                        "UPDATE baselines SET value = ?, updates = ?, updated_bucket = ? "
                        "WHERE series_id = ?",
                        (new_value, row["updates"] + 1, r["bucket"], r["series_id"]),
                    )
                self.baselines_updated += 1

    def _rollup_1h(self, cur: sqlite3.Cursor, now: float) -> None:
        """Hourly rollups aggregate the 5-minute rollups (never raw samples —
        raw may already be pruned for old hours). Only hours fully covered by
        the 5-minute cursor are rolled."""
        r5_cursor = self._meta_int(cur, "rollup5m_cursor", 0)
        watermark = (r5_cursor // ROLLUP1H_S) * ROLLUP1H_S
        start = self._meta_int(cur, "rollup1h_cursor", 0)
        if start == 0:
            row = cur.execute("SELECT MIN(bucket) FROM rollup5m").fetchone()
            if row[0] is None:
                return
            start = (int(row[0]) // ROLLUP1H_S) * ROLLUP1H_S
        end = min(watermark, start + self._span)
        if end <= start:
            return
        rows = cur.execute(
            """
            SELECT series_id, (bucket / ?) * ? AS hbucket,
                   SUM(avg * cnt) / SUM(cnt) AS avg,  -- cnt-weighted, not avg-of-avgs
                   MIN(min) AS min, MAX(max) AS max,
                   MAX(bucket), last AS last, SUM(cnt) AS cnt
            FROM rollup5m WHERE bucket >= ? AND bucket < ?
            GROUP BY series_id, hbucket
            """,
            (ROLLUP1H_S, ROLLUP1H_S, start, end),
        ).fetchall()
        if rows:
            cur.executemany(
                "INSERT OR REPLACE INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["series_id"], r["hbucket"], r["avg"], r["min"], r["max"],
                  r["last"], r["cnt"]) for r in rows],
            )
        self._set_meta(cur, "rollup1h_cursor", end)

    # -- pruning (DM-04 windows) ------------------------------------------

    def _delete_batch(self, cur: sqlite3.Cursor, sql: str, params: tuple) -> int:
        """Bounded DELETE via a row-limited subselect (sqlite3 builds ship
        without DELETE ... LIMIT). Returns rows deleted."""
        cur.execute(sql, (*params, self._batch))
        return cur.rowcount

    def _prune_normal(self, cur: sqlite3.Cursor, now: float) -> None:
        self._delete_batch(
            cur,
            "DELETE FROM samples WHERE (series_id, ts) IN "
            "(SELECT series_id, ts FROM samples WHERE ts < ? LIMIT ?)",
            (int(now) - self._raw_keep,),
        )
        self._delete_batch(
            cur,
            "DELETE FROM rollup5m WHERE (series_id, bucket) IN "
            "(SELECT series_id, bucket FROM rollup5m WHERE bucket < ? LIMIT ?)",
            (int(now) - self._r5m_keep,),
        )
        # DM-04 retention split: durable series (system/disk/self/watchlist)
        # keep hourly history ~13 months; churny process series keep 90 d.
        for durable, keep in ((1, self._r1h_keep_durable), (0, self._r1h_keep_process)):
            self._delete_batch(
                cur,
                "DELETE FROM rollup1h WHERE (series_id, bucket) IN "
                "(SELECT r.series_id, r.bucket FROM rollup1h r "
                " JOIN series s ON s.id = r.series_id "
                " WHERE s.durable = ? AND r.bucket < ? LIMIT ?)",
                (durable, int(now) - keep),
            )
        self._delete_batch(
            cur,
            "DELETE FROM events WHERE id IN "
            "(SELECT id FROM events WHERE ts < ? LIMIT ?)",
            (int(now) - self._events_keep,),
        )

    # -- catalog reap (MD-09) ----------------------------------------------

    def _reap_catalog(self, cur: sqlite3.Cursor, now: float) -> None:
        """A `gone` entity (and its series/baselines) reaps once none of its
        series retain any samples/rollup5m/rollup1h row and no non-cleared
        incident references it -- reap never removes data still inside its
        DM-04 window, so unlike _degrade_if_over_budget this runs every pass
        regardless of budget state.

        Watchlist/synthetic entities are structurally excluded: CA-08's
        gone-detection (engine/pipeline.py) refreshes their last_seen every
        tick since they are always present in the snapshot, so gone_ts stays
        NULL for them forever and they never match the WHERE clause below --
        no source-kind column is needed.

        Cursor-bounded (`reap_cursor_monitor`/`_entity` in meta), same
        catch-up shape as the rollup cursors above but keyed on the entities
        PK instead of a time bucket: REAP_SCAN caps rows *visited* by the
        outer scan, not rows deleted, so one pass costs the same whether
        every visited entity qualifies or none do.
        """
        cm = self._meta_str(cur, "reap_cursor_monitor", "")
        ce = self._meta_str(cur, "reap_cursor_entity", "")
        rows = cur.execute(
            """
            WITH candidates AS (
                SELECT monitor, entity_id, gone_ts FROM entities
                WHERE (monitor, entity_id) > (?, ?)
                ORDER BY monitor, entity_id
                LIMIT ?
            )
            SELECT c.monitor, c.entity_id,
                   CASE WHEN c.gone_ts IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM incidents i
                             WHERE i.monitor = c.monitor AND i.entity_id = c.entity_id
                               AND i.state != 'cleared')
                         AND NOT EXISTS (
                             SELECT 1 FROM series s
                             WHERE s.monitor = c.monitor AND s.entity_id = c.entity_id
                               AND (EXISTS (SELECT 1 FROM samples WHERE series_id = s.id)
                                 OR EXISTS (SELECT 1 FROM rollup5m WHERE series_id = s.id)
                                 OR EXISTS (SELECT 1 FROM rollup1h WHERE series_id = s.id)))
                        THEN 1 ELSE 0 END AS reap
            FROM candidates c
            ORDER BY c.monitor, c.entity_id
            """,
            (cm, ce, self._reap_scan),
        ).fetchall()
        if not rows:
            self._set_meta(cur, "reap_cursor_monitor", "")
            self._set_meta(cur, "reap_cursor_entity", "")
            self._set_meta(cur, "last_reap_ts", int(now))
            self._set_meta(cur, "last_reap_count", 0)
            return
        qualifying = [(r["monitor"], r["entity_id"]) for r in rows if r["reap"]]
        if qualifying:
            # Same delete order as writer.py's forget_entity (CA-07 exemption
            # path) -- samples/rollup5m/rollup1h are already empty by the
            # qualifying condition above; deleting them too costs nothing and
            # keeps the two reap paths identical in shape.
            for table in ("baselines", "samples", "rollup5m", "rollup1h"):
                cur.executemany(
                    f"DELETE FROM {table} WHERE series_id IN "
                    "(SELECT id FROM series WHERE monitor = ? AND entity_id = ?)",
                    qualifying,
                )
            cur.executemany(
                "DELETE FROM entities WHERE monitor = ? AND entity_id = ?", qualifying
            )
            cur.executemany(
                "DELETE FROM series WHERE monitor = ? AND entity_id = ?", qualifying
            )
        self.entities_reaped = len(qualifying)
        self.reaped_keys = qualifying
        # Fewer rows than the scan bound means the outer scan hit end of
        # table this pass -- wrap immediately rather than re-querying an
        # empty tail on the next pass.
        if len(rows) < self._reap_scan:
            self._set_meta(cur, "reap_cursor_monitor", "")
            self._set_meta(cur, "reap_cursor_entity", "")
        else:
            last = rows[-1]
            self._set_meta(cur, "reap_cursor_monitor", last["monitor"])
            self._set_meta(cur, "reap_cursor_entity", last["entity_id"])
        self._set_meta(cur, "last_reap_ts", int(now))
        self._set_meta(cur, "last_reap_count", len(qualifying))

    # -- history cap (DM-13) ----------------------------------------------

    def _cap_incident_history(self, cur: sqlite3.Cursor) -> None:
        """Each incident's history is capped at HISTORY_CAP rows; on overflow
        the oldest HISTORY_TRIM collapse into one `summary` entry (count,
        time range, severity range — DM-13's exact wording). One
        summarization step per incident per pass: bounded work, same
        catch-up-over-many-ticks shape as the rollup passes above rather
        than a single pass trying to clear an arbitrarily large backlog."""
        overflowing = cur.execute(
            "SELECT incident_id FROM incident_history "
            "GROUP BY incident_id HAVING COUNT(*) > ?",
            (HISTORY_CAP,),
        ).fetchall()
        for row in overflowing:
            incident_id = row["incident_id"]
            oldest = cur.execute(
                "SELECT seq, ts, detail FROM incident_history "
                "WHERE incident_id = ? ORDER BY seq LIMIT ?",
                (incident_id, HISTORY_TRIM),
            ).fetchall()
            seqs = [r["seq"] for r in oldest]
            severities = []
            for r in oldest:
                try:
                    parsed = json.loads(r["detail"] or "{}")
                except ValueError:
                    continue
                severity = parsed.get("severity") if isinstance(parsed, dict) else None
                if isinstance(severity, int):
                    severities.append(severity)
            detail = {
                "replaced": len(seqs),
                "from_ts": oldest[0]["ts"],
                "to_ts": oldest[-1]["ts"],
                "severity_min": min(severities) if severities else None,
                "severity_max": max(severities) if severities else None,
            }
            cur.execute(
                "DELETE FROM incident_history WHERE incident_id = ? AND seq IN ("
                + ",".join("?" * len(seqs)) + ")",
                (incident_id, *seqs),
            )
            cur.execute(
                "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) "
                "VALUES (?, ?, ?, 'summary', ?)",
                (incident_id, seqs[0], oldest[-1]["ts"], json.dumps(detail, sort_keys=True)),
            )

    # -- degradation (DM-05) ----------------------------------------------

    def _used_bytes(self, cur: sqlite3.Cursor) -> int:
        # Shared with every reporting surface (issue #104): enforcement and
        # reporting deriving used pages separately is how a budget alarm came
        # to watch a quantity DM-05 does not govern.
        from ftmon.store.db import db_size_report

        return int(db_size_report(cur)["used_bytes"])

    def _degrade_if_over_budget(self, cur: sqlite3.Cursor, now: float) -> list[str]:
        """DM-05: fixed order, oldest-and-coarsest-last, never incidents.
        Each step prunes one batch then re-measures — over budget by a lot
        means several passes over several ticks, keeping each tick bounded."""
        notes: list[str] = []
        steps: tuple[tuple[str, str, tuple], ...] = (
            ("raw samples beyond 24h",
             "DELETE FROM samples WHERE (series_id, ts) IN "
             "(SELECT series_id, ts FROM samples WHERE ts < ? LIMIT ?)",
             (int(now) - _DAY,)),
            ("events beyond 7d",
             "DELETE FROM events WHERE id IN "
             "(SELECT id FROM events WHERE ts < ? LIMIT ?)",
             (int(now) - 7 * _DAY,)),
            ("oldest 5-min rollups",
             "DELETE FROM rollup5m WHERE (series_id, bucket) IN "
             "(SELECT series_id, bucket FROM rollup5m ORDER BY bucket LIMIT ?)",
             ()),
            ("oldest 1-h rollups",
             "DELETE FROM rollup1h WHERE (series_id, bucket) IN "
             "(SELECT series_id, bucket FROM rollup1h ORDER BY bucket LIMIT ?)",
             ()),
        )
        for label, sql, params in steps:
            if self._used_bytes(cur) <= self._budget:
                return notes
            deleted = self._delete_batch(cur, sql, params)
            if deleted:
                notes.append(f"db over budget: pruned {deleted} rows ({label})")
                # CL-05 exposes the last real data-dropping degradation. Set
                # this when the note is created: the next loop iteration may
                # return early once this batch has restored the budget.
                self._set_meta(cur, "last_degradation_ts", int(now))
        return notes


class BaselineLookup:
    """Read side of CA-05, shaped for EntityCtx.baseline_lookup.

    Caches by (monitor, entity_id, metric): rules may call baseline() every
    cycle for hundreds of entities but values only change when a retention
    pass writes rollups — the daemon calls invalidate() exactly then.
    Below-coverage baselines return None (EX-06 keeps the rule UNKNOWN, so a
    learning baseline is silent rather than wrong).
    """

    def __init__(self, conn: sqlite3.Connection, min_updates: int = BASELINE_MIN_UPDATES):
        self._conn = conn
        self._min_updates = min_updates
        self._cache: dict[tuple[str, str, str], float | None] = {}

    def __call__(self, monitor: str, entity_id: str, metric: str) -> float | None:
        key = (monitor, entity_id, metric)
        if key in self._cache:
            return self._cache[key]
        row = self._conn.execute(
            "SELECT b.value, b.updates FROM baselines b "
            "JOIN series s ON s.id = b.series_id "
            "WHERE s.monitor = ? AND s.entity_id = ? AND s.metric = ?",
            key,
        ).fetchone()
        value = row["value"] if row is not None and row["updates"] >= self._min_updates else None
        self._cache[key] = value
        return value

    def invalidate(self) -> None:
        self._cache.clear()


def reset_baselines(
    conn: sqlite3.Connection, monitor: str, entity_id: str | None = None,
    commit: Callable[[], None] | None = None,
) -> int:
    """CA-06: forget learned baselines so they relearn from scratch.
    Returns the number of baselines cleared."""
    sql = ("DELETE FROM baselines WHERE series_id IN "
           "(SELECT id FROM series WHERE monitor = ?")
    params: tuple = (monitor,)
    if entity_id is not None:
        sql += " AND entity_id = ?"
        params = (monitor, entity_id)
    cur = conn.execute(sql + ")", params)
    (commit or conn.commit)()
    return cur.rowcount

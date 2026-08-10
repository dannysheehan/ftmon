"""[DM-04][DM-05][DM-13][CA-05][CA-06][MD-09] Rollups, retention windows,
degradation order, incident-history cap, EW-mean baselines, and catalog
reap — golden-value tests against a real SQLite db."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.sources.fixtures import fixture_samplers, scenario
from ftmon.store.db import connect, migrate
from ftmon.store.doctor import inspect as doctor_inspect
from ftmon.store.query import Query
from ftmon.store.retention import (
    BaselineLookup,
    Retention,
    reset_baselines,
)
from tests.unit.test_engine import LEAKDEF, ScriptedSampler
from tests.unit.test_m2_integration import core_env  # noqa: F401

T0 = 1_700_000_100  # deliberately not bucket-aligned
REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "r.db")
    migrate(c)
    yield c
    c.close()


def add_series(conn, sid, monitor="m", entity="e", metric="x", durable=1):
    conn.execute(
        "INSERT INTO series(id, monitor, entity_id, metric, durable) VALUES (?,?,?,?,?)",
        (sid, monitor, entity, metric, durable),
    )


def add_samples(conn, sid, pairs):
    conn.executemany(
        "INSERT INTO samples(series_id, ts, value) VALUES (?,?,?)",
        [(sid, ts, v) for ts, v in pairs],
    )
    conn.commit()


def add_entity(conn, monitor="m", entity="e", *, gone_ts=None, first_seen=1, last_seen=1):
    conn.execute(
        "INSERT INTO entities(monitor, entity_id, first_seen, last_seen, gone_ts, attrs) "
        "VALUES (?,?,?,?,?,?)",
        (monitor, entity, first_seen, last_seen, gone_ts, "{}"),
    )


class TestRollup5m:
    def test_golden_bucket(self, conn):
        """[DM-04] one complete 5-min bucket rolls to exact avg/min/max/last/cnt."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 10.0), (b + 60, 30.0), (b + 240, 20.0)])
        Retention(conn).run(now=b + 300 + 60)
        row = conn.execute("SELECT * FROM rollup5m").fetchone()
        assert (row["series_id"], row["bucket"]) == (1, b)
        assert row["avg"] == pytest.approx(20.0)
        assert (row["min"], row["max"]) == (10.0, 30.0)
        assert row["last"] == 20.0  # value at max ts, not max value
        assert row["cnt"] == 3

    def test_incomplete_bucket_not_rolled_then_rolled(self, conn):
        """[DM-04] the current (still-fillable) bucket is left alone until a
        later pass; the cursor makes the second pass pick it up exactly once."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 1.0), (b + 310, 2.0)])
        r = Retention(conn)
        r.run(now=b + 400)  # bucket b complete; bucket b+300 still open
        assert conn.execute("SELECT COUNT(*) FROM rollup5m").fetchone()[0] == 1
        r.run(now=b + 700)
        buckets = [x["bucket"] for x in conn.execute(
            "SELECT bucket FROM rollup5m ORDER BY bucket")]
        assert buckets == [b, b + 300]

    def test_catch_up_is_bounded_per_pass(self, conn):
        """[DM-04] a database that was offline for days catches up over
        multiple passes (max_bucket_span_s), not one giant scan."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b + i * 300, float(i)) for i in range(24)])  # 2h
        r = Retention(conn, max_bucket_span_s=1800)
        now = b + 24 * 300 + 60
        r.run(now)
        assert conn.execute("SELECT COUNT(*) FROM rollup5m").fetchone()[0] == 6
        r.run(now)
        assert conn.execute("SELECT COUNT(*) FROM rollup5m").fetchone()[0] == 12


class TestRollup1h:
    def test_weighted_average_and_last(self, conn):
        """[DM-04] hourly avg is cnt-weighted over the 5-min rollups; last is
        the latest bucket's last."""
        add_series(conn, 1)
        h = ((T0 // 3600) + 1) * 3600
        # two 5m buckets: avg 10 with cnt 1, avg 20 with cnt 3 -> weighted 17.5
        conn.executemany(
            "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (?,?,?,?,?,?,?)",
            [(1, h, 10.0, 5.0, 15.0, 11.0, 1),
             (1, h + 300, 20.0, 18.0, 25.0, 22.0, 3)],
        )
        conn.execute("INSERT INTO meta(key, value) VALUES ('rollup5m_cursor', ?)",
                     (str(h + 3600),))
        conn.commit()
        Retention(conn).run(now=h + 3600 + 120)
        row = conn.execute("SELECT * FROM rollup1h").fetchone()
        assert row["bucket"] == h
        assert row["avg"] == pytest.approx(17.5)
        assert (row["min"], row["max"], row["last"], row["cnt"]) == (5.0, 25.0, 22.0, 4)

    def test_only_hours_covered_by_5m_cursor(self, conn):
        """[DM-04] an hour is rolled only once the 5-min cursor has passed
        its end — never from coverage that could still grow. Once the cursor
        does pass, the hour rolls from whatever 5m rows exist (sparse data is
        real data; an idle series is not an error)."""
        add_series(conn, 1)
        h = ((T0 // 3600) + 1) * 3600
        conn.execute(
            "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (1, ?, 10.0, 10.0, 10.0, 10.0, 1)", (h,))
        conn.execute("INSERT INTO meta(key, value) VALUES ('rollup5m_cursor', ?)",
                     (str(h + 1800),))  # 5m coverage stops mid-hour
        conn.commit()
        r = Retention(conn)
        r.run(now=h + 1830)  # 5m cursor cannot advance yet -> hour not rollable
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 0
        r.run(now=h + 3660)  # cursor passes the hour boundary -> hour rolls
        row = conn.execute("SELECT * FROM rollup1h").fetchone()
        assert (row["bucket"], row["cnt"]) == (h, 1)


class TestBaseline:
    def test_ew_mean_golden(self, conn):
        """[CA-05] b <- b + alpha*(avg - b); half_life == bucket width gives
        alpha = 0.5: seed 10, then 20 -> 15, then 30 -> 22.5."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 10.0), (b + 300, 20.0), (b + 600, 30.0)])
        Retention(conn, half_life_s=300.0).run(now=b + 900 + 60)
        row = conn.execute("SELECT * FROM baselines").fetchone()
        assert row["value"] == pytest.approx(22.5)
        assert row["updates"] == 3
        assert row["updated_bucket"] == b + 600
        assert row["half_life_s"] == 300.0

    def test_half_life_change_reseeds_baseline(self, conn):
        """[CA-05] a new alpha starts a new reversible baseline lifetime."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 10.0), (b + 300, 20.0)])
        Retention(conn, half_life_s=300.0).run(now=b + 700)
        conn.execute("DELETE FROM meta WHERE key = 'rollup5m_cursor'")
        conn.execute("DELETE FROM samples")
        add_samples(conn, 1, [(b + 600, 80.0)])

        Retention(conn, half_life_s=600.0).run(now=b + 1000)

        row = conn.execute("SELECT * FROM baselines").fetchone()
        assert row["value"] == 80.0
        assert row["updates"] == 1
        assert row["updated_bucket"] == b + 600
        assert row["half_life_s"] == 600.0

    def test_rerolled_bucket_does_not_double_count(self, conn):
        """[CA-05] the updated_bucket guard: re-rolling an already-applied
        bucket (cursor reset, crash replay) must not step the mean again."""
        add_series(conn, 1)
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 10.0)])
        r = Retention(conn, half_life_s=300.0)
        r.run(now=b + 400)
        conn.execute("DELETE FROM meta WHERE key = 'rollup5m_cursor'")
        conn.commit()
        r.run(now=b + 400)  # replays the same bucket
        row = conn.execute("SELECT * FROM baselines").fetchone()
        assert (row["value"], row["updates"]) == (10.0, 1)

    def test_lookup_coverage_gate_and_cache(self, conn):
        """[CA-05] baseline() is None below 240 updates — counted updates,
        not elapsed time; invalidate() picks up new values."""
        add_series(conn, 1, metric="rss_bytes")
        conn.execute(
            "INSERT INTO baselines(series_id, value, updates, updated_bucket) "
            "VALUES (1, 42.0, 239, 0)")
        conn.commit()
        look = BaselineLookup(conn)
        assert look("m", "e", "rss_bytes") is None  # one update short
        conn.execute("UPDATE baselines SET updates = 240")
        conn.commit()
        assert look("m", "e", "rss_bytes") is None  # cached miss until invalidated
        look.invalidate()
        assert look("m", "e", "rss_bytes") == 42.0
        assert look("m", "e", "nothere") is None

    def test_reset_baselines_scopes(self, conn):
        """[CA-06] reset clears a whole monitor or one entity of it."""
        add_series(conn, 1, entity="e1")
        add_series(conn, 2, entity="e2")
        add_series(conn, 3, monitor="other")
        conn.executemany(
            "INSERT INTO baselines(series_id, value, updates, updated_bucket) "
            "VALUES (?, 1.0, 300, 0)", [(1,), (2,), (3,)])
        conn.commit()
        assert reset_baselines(conn, "m", "e1") == 1
        assert reset_baselines(conn, "m") == 1  # e2 remains, now cleared
        assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 1  # other

    def test_query_reconstructs_forward_ewma_without_reversing_seed(self, conn):
        """[CA-05] retained 5m inputs exactly recover each native EWMA state."""
        add_series(conn, 1, metric="rss_bytes")
        b = (T0 // 300) * 300
        add_samples(conn, 1, [(b, 10.0), (b + 300, 20.0), (b + 600, 30.0)])
        Retention(conn, half_life_s=300.0).run(now=b + 1000)

        history = Query(conn).baseline_history(
            "m", "e", "rss_bytes", start=b, end=b + 600
        )

        assert history is not None
        assert [(point.ts, point.value) for point in history.points] == pytest.approx([
            (b, 10.0), (b + 300, 15.0), (b + 600, 22.5)
        ])
        assert history.history_truncated is False
        assert history.baseline.half_life_s == 300.0

    def test_query_history_truncation_is_range_relative_and_uses_ceil(self, conn):
        """[CA-05] missing lifetime inputs matter only before the first bucket in view."""
        add_series(conn, 1, metric="rss_bytes")
        b = (T0 // 300) * 300
        conn.executemany(
            "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
            "VALUES (1,?,?,1,1,1,1)",
            [(b + 300, 20.0), (b + 600, 30.0)],
        )
        conn.execute(
            "INSERT INTO baselines(series_id,value,updates,updated_bucket,half_life_s) "
            "VALUES (1,22.5,3,?,300)",
            (b + 600,),
        )
        conn.commit()
        query = Query(conn)

        short = query.baseline_history(
            "m", "e", "rss_bytes", start=b + 2, end=b + 600
        )
        assert short is not None
        assert short.history_truncated is False
        long = query.baseline_history("m", "e", "rss_bytes", start=b, end=b + 600)
        assert long is not None
        assert long.history_truncated is True

    def test_query_lists_baselines_with_bound_filter_cursor(self, conn):
        """[CA-05] baseline discovery is deterministic, bounded, and stateless."""
        for sid, monitor, entity, metric, updates in (
            (1, "a", "e1", "cpu", 1),
            (2, "a", "e2", "rss", 240),
            (3, "b", "e1", "disk", 300),
        ):
            add_series(conn, sid, monitor=monitor, entity=entity, metric=metric)
            conn.execute(
                "INSERT INTO baselines(series_id,value,updates,updated_bucket) "
                "VALUES (?,10,?,300)",
                (sid, updates),
            )
        conn.commit()
        query = Query(conn)

        first = query.list_baselines(limit=2)
        assert [(row.monitor, row.entity_id, row.metric) for row in first.baselines] == [
            ("a", "e1", "cpu"), ("a", "e2", "rss")
        ]
        assert first.baselines[0].coverage == pytest.approx(1 / 240)
        assert first.baselines[1].ready is True
        assert first.next_cursor is not None
        second = query.list_baselines(limit=2, cursor=first.next_cursor)
        assert [(row.monitor, row.entity_id, row.metric) for row in second.baselines] == [
            ("b", "e1", "disk")
        ]
        assert second.next_cursor is None
        with pytest.raises(ValueError, match="cursor"):
            query.list_baselines(monitor="a", limit=2, cursor=first.next_cursor)
        with pytest.raises(ValueError, match="cursor"):
            query.list_baselines(cursor="not-a-cursor")
        with pytest.raises(ValueError, match="limit"):
            query.list_baselines(limit=0)


class TestPruneAndDegrade:
    def test_normal_retention_windows(self, conn):
        """[DM-04] raw 48h; 5m 30d durable / 7d process; 1h 400d/90d; events 30d.

        The 5-minute tier gained the durable/process split in issue #102 for
        the same reason the hourly tier has had it since v0.3 -- process churn
        makes one window infeasible -- so both sides are exercised here.
        """
        now = T0 + 500 * 86400
        add_series(conn, 1, durable=1)
        add_series(conn, 2, monitor="leak", entity="p", durable=0)
        add_samples(conn, 1, [(now - 49 * 3600, 1.0), (now - 3600, 2.0)])
        conn.executemany(
            "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (?,?,1,1,1,1,1)",
            [(1, now - 31 * 86400),   # durable, past 30d -> pruned
             (1, now - 8 * 86400),    # durable, inside 30d -> kept
             (1, now - 86400),
             (2, now - 8 * 86400),    # process, past 7d -> pruned
             (2, now - 3 * 86400)])   # process, inside 7d -> kept
        conn.executemany(
            "INSERT INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (?,?,1,1,1,1,1)",
            [(1, now - 200 * 86400),  # durable, inside 400d -> kept
             (2, now - 200 * 86400),  # process, past 90d -> pruned
             (2, now - 10 * 86400)])
        conn.execute(
            "INSERT INTO events(id, ts, ingest_ts, source, provider, event_id, "
            "severity, message, attrs) VALUES (1, ?, ?, 's', 'p', NULL, 1, 'old', '{}')",
            (now - 31 * 86400, now - 31 * 86400))
        conn.commit()
        notes = Retention(conn).run(now=now)
        assert notes == []  # normal windows are silent; notes are DM-05 only
        assert [r["ts"] for r in conn.execute("SELECT ts FROM samples")] == [now - 3600]
        kept_5m = {(r["series_id"], r["bucket"]) for r in
                   conn.execute("SELECT series_id, bucket FROM rollup5m")}
        assert (1, now - 31 * 86400) not in kept_5m  # durable past 30d
        assert (1, now - 8 * 86400) in kept_5m       # durable inside 30d
        # The split: the same 8-day bucket is kept for a durable series and
        # pruned for a process one. Without it, half of rollup5m on a real
        # desktop is 5-minute detail for processes that no longer exist.
        assert (2, now - 8 * 86400) not in kept_5m
        assert (2, now - 3 * 86400) in kept_5m
        kept_1h = {(r["series_id"], r["bucket"]) for r in
                   conn.execute("SELECT series_id, bucket FROM rollup1h")}
        assert (1, now - 200 * 86400) in kept_1h
        assert (2, now - 200 * 86400) not in kept_1h
        assert (2, now - 10 * 86400) in kept_1h
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    def test_degradation_order_and_incidents_survive(self, conn):
        """[DM-05] over budget: raw>24h, then events>7d, then 5m, then 1h —
        with a self-note per step; incidents are never pruned."""
        now = T0 + 40 * 86400
        add_series(conn, 1)
        add_samples(conn, 1, [(now - 30 * 3600, 1.0)])  # >24h but <48h
        conn.execute(
            "INSERT INTO events(id, ts, ingest_ts, source, provider, event_id, "
            "severity, message, attrs) VALUES (1, ?, ?, 's', 'p', NULL, 1, 'e', '{}')",
            (now - 8 * 86400, now - 8 * 86400))  # >7d but <30d
        conn.execute(
            "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (1, ?, 1, 1, 1, 1, 1)", (now - 600,))
        conn.execute(
            "INSERT INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (1, ?, 1, 1, 1, 1, 1)", (now - 7200,))
        conn.execute(
            "INSERT INTO incidents(id, monitor, grp, entity_id, state, severity, "
            "owning_rule, opened_ts, last_change_ts, notify_count, occurrences) "
            "VALUES (1, 'm', 'g', 'e', 'cleared', 2, 'r', ?, ?, 1, 1)",
            (T0, T0))
        conn.commit()
        notes = Retention(conn, budget_bytes=0).run(now=now)  # always over budget
        assert [("raw" in n, "events" in n, "5-min" in n, "1-h" in n) for n in notes] == [
            (True, False, False, False),
            (False, True, False, False),
            (False, False, True, False),
            (False, False, False, True),
        ]
        for table in ("samples", "events", "rollup5m", "rollup1h"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
        assert conn.execute(
            "SELECT value FROM meta WHERE key = 'last_degradation_ts'"
        ).fetchone()["value"] == str(now)

    def test_degradation_timestamp_survives_early_budget_return(self, conn):
        """[DM-05] a successful first degradation batch records its timestamp
        before the next step sees the restored budget and returns early."""
        now = T0 + 2 * 86400
        add_series(conn, 1)
        add_samples(conn, 1, [(now - 30 * 3600, 1.0)])  # degradable, not normal-pruned
        retention = Retention(conn)
        measurements = iter((retention._budget + 1, retention._budget))
        retention._used_bytes = lambda _cur: next(measurements)

        notes = retention.run(now=now)

        assert len(notes) == 1 and "raw samples" in notes[0]
        assert conn.execute(
            "SELECT value FROM meta WHERE key = 'last_degradation_ts'"
        ).fetchone()["value"] == str(now)

    def test_under_budget_never_degrades(self, conn):
        """[DM-05] a healthy database takes no degradation steps at all."""
        add_series(conn, 1)
        add_samples(conn, 1, [(T0, 1.0)])
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('last_degradation_ts', '123')"
        )
        conn.commit()
        assert Retention(conn).run(now=T0 + 60) == []
        assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
        assert conn.execute(
            "SELECT value FROM meta WHERE key = 'last_degradation_ts'"
        ).fetchone()["value"] == "123"


def add_incident(conn, incident_id=1):
    conn.execute(
        "INSERT INTO incidents(id, monitor, grp, entity_id, state, severity, "
        "owning_rule, opened_ts, last_change_ts, notify_count, occurrences) "
        "VALUES (?, 'm', 'g', 'e', 'open', 2, 'r', ?, ?, 0, 1)",
        (incident_id, T0, T0),
    )


def add_history_rows(conn, incident_id, count, *, start_seq=1, severities=None):
    """Rows with no severity look like 'acked'/'notified' entries; entries
    listed in `severities` (seq -> severity) look like open/escalate/downgrade."""
    severities = severities or {}
    rows = []
    for i in range(count):
        seq = start_seq + i
        detail = {"severity": severities[seq]} if seq in severities else {"by": "x"}
        rows.append((incident_id, seq, T0 + seq, "note", json.dumps(detail)))
    conn.executemany(
        "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


class TestHistoryCap:
    def test_dm13_overflow_collapses_oldest_batch_into_one_summary(self, conn):
        """[DM-13] > 500 rows: the oldest 100 collapse into one summary entry
        carrying count, time range, and severity range; newer rows survive
        untouched, seq ordering intact."""
        add_incident(conn, 1)
        add_history_rows(conn, 1, 510, severities={10: 1, 50: 3, 90: 2})
        Retention(conn).run(now=T0 + 1000)

        rows = conn.execute(
            "SELECT seq, ts, kind, detail FROM incident_history "
            "WHERE incident_id = 1 ORDER BY seq"
        ).fetchall()
        assert len(rows) == 411  # 510 - 100 + 1 summary
        summary = rows[0]
        assert (summary["seq"], summary["kind"]) == (1, "summary")
        assert summary["ts"] == T0 + 100  # to_ts of the collapsed batch
        detail = json.loads(summary["detail"])
        assert detail == {
            "replaced": 100,
            "from_ts": T0 + 1,
            "to_ts": T0 + 100,
            "severity_min": 1,
            "severity_max": 3,
        }
        assert [r["seq"] for r in rows[1:5]] == [101, 102, 103, 104]

    def test_dm13_under_cap_is_untouched(self, conn):
        """[DM-13] exactly at the cap: no summarization, nothing silently lost."""
        add_incident(conn, 1)
        add_history_rows(conn, 1, 500)
        Retention(conn).run(now=T0 + 1000)
        assert conn.execute(
            "SELECT COUNT(*) FROM incident_history WHERE incident_id = 1"
        ).fetchone()[0] == 500
        assert conn.execute(
            "SELECT COUNT(*) FROM incident_history WHERE kind = 'summary'"
        ).fetchone()[0] == 0

    def test_dm13_large_backlog_catches_up_over_multiple_passes(self, conn):
        """[DM-13] one summarization step per incident per pass — same
        bounded catch-up shape as the rollup passes, not one giant collapse."""
        add_incident(conn, 1)
        add_history_rows(conn, 1, 700)
        r = Retention(conn)
        r.run(now=T0 + 1000)
        after_one = conn.execute(
            "SELECT COUNT(*) FROM incident_history WHERE incident_id = 1"
        ).fetchone()[0]
        assert after_one == 601  # 700 - 100 + 1, still over the 500 cap
        r.run(now=T0 + 1000)
        after_two = conn.execute(
            "SELECT COUNT(*) FROM incident_history WHERE incident_id = 1"
        ).fetchone()[0]
        assert after_two == 502  # converges toward the cap over successive passes

    def test_dm13_no_severity_in_batch_yields_null_range(self, conn):
        """[DM-13] a collapsed batch with no severity-bearing entries (e.g.
        all acks/notifications) reports a null range rather than a bogus 0."""
        add_incident(conn, 1)
        add_history_rows(conn, 1, 510)  # no severities anywhere
        Retention(conn).run(now=T0 + 1000)
        summary = conn.execute(
            "SELECT detail FROM incident_history WHERE incident_id = 1 AND seq = 1"
        ).fetchone()
        detail = json.loads(summary["detail"])
        assert (detail["severity_min"], detail["severity_max"]) == (None, None)

    def test_dm13_is_silent_like_other_normal_pruning(self, conn):
        """[DM-13] cap enforcement is normal housekeeping, not a DM-05
        degradation step — it must not appear in the notes list."""
        add_incident(conn, 1)
        add_history_rows(conn, 1, 510)
        notes = Retention(conn).run(now=T0 + 1000)
        assert notes == []


class TestCatalogReap:
    def test_reaps_gone_entity_with_no_observations(self, conn):
        """[MD-09][DM-04] a gone entity whose series retains no samples,
        rollup5m, or rollup1h row reaps in full: the entities row, its
        series row, and its baseline all disappear in the same pass, and
        entities_reaped counts it."""
        add_series(conn, 1)
        add_entity(conn, gone_ts=T0 - 100, first_seen=T0 - 10_000, last_seen=T0 - 200)
        conn.execute(
            "INSERT INTO baselines(series_id, value, updates, updated_bucket, half_life_s) "
            "VALUES (1, 5.0, 300, 0, 259200)"
        )
        conn.commit()

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 0
        assert r.entities_reaped == 1

    def test_protected_by_recent_rollup1h_row(self, conn):
        """[MD-09][DM-04] a gone entity whose series still has a rollup1h
        row inside the retention window is not reaped — reap only removes
        catalog rows holding nothing DM-04 still promises to keep."""
        add_series(conn, 1, durable=1)
        add_entity(conn, gone_ts=T0 - 100, last_seen=T0 - 200)
        conn.execute(
            "INSERT INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (1, ?, 1, 1, 1, 1, 1)",
            (T0 - 3600,),
        )
        conn.commit()

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 1
        assert r.entities_reaped == 0

    def test_protected_by_open_incident(self, conn):
        """[MD-09] a gone entity with zero retained observations but a live
        (non-cleared) incident referencing it must not be reaped — an
        operator resolving that incident still needs the entity row."""
        add_series(conn, 1)
        add_entity(conn, gone_ts=T0 - 100, last_seen=T0 - 200)
        add_incident(conn, 1)  # monitor 'm', entity 'e', state 'open'
        conn.commit()

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
        assert r.entities_reaped == 0

    def test_gone_ts_null_entity_never_reaped(self, conn):
        """[MD-09] entities whose gone_ts is NULL — CA-08's gone-detection
        never sets it for watchlist/synthetic entities re-emitted every
        tick — are structurally excluded from reap and survive arbitrarily
        many passes, with or without a data-bearing series."""
        add_series(conn, 1)
        add_entity(conn, gone_ts=None)
        conn.commit()

        r = Retention(conn)
        for i in range(5):
            r.run(now=T0 + i * 100)
            assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
            assert r.entities_reaped == 0

    def test_reap_scan_bounds_rows_visited_per_pass(self, conn):
        """[MD-09] reap_scan bounds a pass by rows *visited*, not rows
        deleted: with a backlog bigger than the scan window, one run()
        reaps at most reap_scan entities and leaves the cursor advanced
        (not wrapped) so the next pass resumes past this one instead of
        rescanning it."""
        for i in range(10):
            eid = f"e{i:02d}"
            add_series(conn, i + 1, entity=eid)
            add_entity(conn, entity=eid, gone_ts=T0 - 100, last_seen=T0 - 200)
        conn.commit()

        r = Retention(conn, reap_scan=3)
        r.run(now=T0)

        assert r.entities_reaped == 3
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 7
        cm = conn.execute("SELECT value FROM meta WHERE key = 'reap_cursor_monitor'").fetchone()
        ce = conn.execute("SELECT value FROM meta WHERE key = 'reap_cursor_entity'").fetchone()
        assert (cm["value"], ce["value"]) == ("m", "e02")

    def test_reap_cursor_wraps_and_clears_full_backlog(self, conn):
        """[MD-09] driving run() repeatedly over a backlog bigger than
        reap_scan eventually reaps everything, and the cursor returns to
        ("", "") once caught up — the same bounded catch-up-over-many-passes
        shape as the rollup cursors, not one unbounded pass."""
        for i in range(10):
            eid = f"e{i:02d}"
            add_series(conn, i + 1, entity=eid)
            add_entity(conn, entity=eid, gone_ts=T0 - 100, last_seen=T0 - 200)
        conn.commit()

        r = Retention(conn, reap_scan=3)
        total_reaped = 0
        for _ in range(4):  # 3 + 3 + 3 + 1 == 10, and the short last pass wraps
            r.run(now=T0)
            total_reaped += r.entities_reaped
        assert total_reaped == 10
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
        cm = conn.execute("SELECT value FROM meta WHERE key = 'reap_cursor_monitor'").fetchone()
        ce = conn.execute("SELECT value FROM meta WHERE key = 'reap_cursor_entity'").fetchone()
        assert (cm["value"], ce["value"]) == ("", "")

        r.run(now=T0)  # one more pass over the now-empty backlog: a no-op
        assert r.entities_reaped == 0
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


class TestCatalogReapIntegration:
    def test_proc_churn_backlog_reaps_and_bounds_db_growth(self, core_env):  # noqa: F811
        """[MD-09][DM-04][DM-05] proc-churn-300 driven through the real
        DaemonCore: SA-05 top-N selection means only a fraction of each
        minute's ~290 churned identities ever get a series, but CA-08's
        gone-tracking (pipeline._track_gone) still writes an `entities` row
        for every identity once it drops out of the snapshot — without
        catalog reap that is thousands of permanent rows over the scenario's
        20-minute run. The default REAP_SCAN (2000) comfortably outpaces
        this fixture's per-wave backlog (~270 entities) and reaps it away
        within the same tick it appears, so a small reap_scan is used here
        to make a real backlog visibly form, cycle the cursor, and drain —
        the same shape a slower or busier real host would see. Meanwhile
        the always-selected 'stable' entities' full observation history is
        untouched, and DB used-bytes plateaus instead of growing with the
        ~5800 churned identities the scenario generates."""
        paths = core_env
        (paths.monitors_dir / "leak.toml").write_text(
            LEAKDEF.replace("[parameters]", "[source_options]\ntop_n = 15\n[parameters]")
        )
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="linux")
        core.samplers.update(fixture_samplers(scenario("proc-churn-300")))
        core.retention._reap_scan = 50  # shrink so a backlog visibly accumulates/drains

        entity_counts: list[int] = []
        series_counts: list[int] = []
        used_bytes: list[int] = []
        cursor_wrapped_mid_backlog = False
        for _ in range(40):
            core.on_tick(clock.now(), clock.monotonic(), 0.0)
            clock.advance(60.0)
            ro = connect(paths.db_file, readonly=True)
            entities = ro.execute(
                "SELECT COUNT(*) FROM entities WHERE monitor = 'leak'"
            ).fetchone()[0]
            entity_counts.append(entities)
            series_counts.append(
                ro.execute("SELECT COUNT(*) FROM series WHERE monitor = 'leak'").fetchone()[0]
            )
            (pages,) = ro.execute("PRAGMA page_count").fetchone()
            (free,) = ro.execute("PRAGMA freelist_count").fetchone()
            (size,) = ro.execute("PRAGMA page_size").fetchone()
            used_bytes.append((pages - free) * size)
            cursor_monitor = ro.execute(
                "SELECT value FROM meta WHERE key = 'reap_cursor_monitor'"
            ).fetchone()
            # (b): the cursor completing a full lap while a real backlog is
            # still outstanding is direct evidence of a finished scan cycle,
            # not just the trivial empty-table wrap seen before any backlog
            # has formed.
            if cursor_monitor is not None and cursor_monitor["value"] == "" and entities > 1000:
                cursor_wrapped_mid_backlog = True
            ro.close()

        # (a): a real backlog accumulates -- far more gone entities than a
        # single reap_scan=50 pass could ever clear in one tick.
        peak = max(entity_counts)
        assert peak > 1000
        assert cursor_wrapped_mid_backlog
        # (c): once churn stops generating new gone waves, reap catches up --
        # a measurable drop from the peak, not a monotonic pile-up.
        assert entity_counts[-1] < peak * 0.9
        # series only ever exist for entities actually selected/persisted;
        # that population stays bounded and stops growing once the
        # scenario's churn ends, regardless of the ~5800 identities seen.
        assert max(series_counts) < 1000
        assert series_counts[-1] == series_counts[-10]  # plateaued, not still growing

        conn = connect(paths.db_file, readonly=True)
        assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        # (d): the always-selected 'stable' entities' observations are
        # untouched -- two metrics (rss_bytes, cpu_pct) every one of the 40
        # ticks, never brushed by reap despite it running every pass.
        stable = conn.execute(
            "SELECT s.entity_id, COUNT(*) AS c FROM series s "
            "JOIN samples sm ON sm.series_id = s.id "
            "WHERE s.monitor = 'leak' AND s.entity_id LIKE 'stable%' "
            "GROUP BY s.entity_id"
        ).fetchall()
        assert len(stable) == 10
        assert all(row["c"] == 80 for row in stable)  # 2 metrics * 40 ticks

        # (e): the *row-level* headroom claim -- reaped catalog rows stay
        # reaped, not just capped -- is (a)/(c)/the series-count assertions
        # above; entity_counts[-1] < peak * 0.9 alone is a genuine ~250-row
        # drop, not noise. used_bytes is checked only as a weaker "does not
        # grow unbounded" guard on top of that: this fixture's backlog is
        # almost entirely bare entities rows (SA-05 top-N means most churned
        # identities never get a series/sample at all), so at page
        # granularity (4 KiB) incremental_vacuum's reclaim from a few
        # thousand such rows is a handful of pages -- real but too small a
        # fraction of a ~1 MB test database to assert a specific percentage
        # drop without being flaky. A budget-pressure scenario large enough
        # to move used_bytes by a measurable margin is DM-05 territory, not
        # this test's job.
        peak_idx = entity_counts.index(peak)
        post_peak = used_bytes[peak_idx:]
        assert max(post_peak) <= used_bytes[peak_idx] * 1.05  # no unbounded growth


class TestReapCacheInvalidation:
    def test_reap_evicts_caches_so_returning_identity_gets_fresh_state(self, core_env):  # noqa: F811
        """[MD-09] Reap runs on retention's own connection/transaction, so
        nothing else notices its deletes unless told. Two long-lived
        in-process caches would otherwise go stale when a reaped identity's
        entity_id is later reused by a new, unrelated observation of "the
        same" name: TickWriter._series_cache (one instance per daemon
        lifetime) would keep handing out a series id the `series` table no
        longer has a row for, producing orphan samples; BaselineLookup's
        cache would keep answering with a baseline learned by the entity
        that no longer exists. daemon.py's _run_retention must evict both
        whenever entities_reaped > 0 -- not only when baselines_updated > 0,
        since reap can delete a mature baseline row without recomputing one
        in the same pass."""
        paths = core_env
        eid = "e1:1:1"
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="linux")
        sampler = ScriptedSampler()
        sampler.push((eid, {"name": "e1"}, {"rss_bytes": 1e6, "cpu_pct": 1.0}))
        for _ in range(6):
            sampler.push()  # absent long enough to cross gone_grace_s (300s)
        sampler.push((eid, {"name": "e1"}, {"rss_bytes": 2e6, "cpu_pct": 1.0}))
        core.samplers["process"] = sampler
        # Shrink retention windows to seconds so this identity's samples/
        # rollups age out within the test's ~10 simulated minutes instead of
        # DM-04's real 48h/30d/90d windows -- same technique the churn
        # integration test above uses for _reap_scan.
        core.retention._raw_keep = 1
        core.retention._r5m_keep_durable = 1
        core.retention._r5m_keep_process = 1
        core.retention._r1h_keep_durable = 1
        core.retention._r1h_keep_process = 1

        core.on_tick(clock.now(), clock.monotonic(), 0.0)  # tick 0: e1 present
        clock.advance(60.0)

        original_series_id = core.conn.execute(
            "SELECT id FROM series WHERE monitor='leak' AND entity_id=? AND metric='rss_bytes'",
            (eid,),
        ).fetchone()["id"]
        # Seed a mature baseline directly (bypassing the real ~24h/240-update
        # ramp) so BaselineLookup has a concrete cached value to later lose.
        core.conn.execute(
            "INSERT INTO baselines(series_id, value, updates, updated_bucket, half_life_s) "
            "VALUES (?, 500000.0, 300, 0, 259200)",
            (original_series_id,),
        )
        core.conn.commit()
        assert core.baselines("leak", eid, "rss_bytes") == 500000.0  # now cached

        reaped = False
        for _ in range(6):  # ticks 1-6: absent -> gone_ts set + reaped same pass
            core.on_tick(clock.now(), clock.monotonic(), 0.0)
            clock.advance(60.0)
            if core.retention.entities_reaped:
                reaped = True
        assert reaped, "setup never reached reap -- this test would be vacuous"
        assert core.conn.execute(
            "SELECT COUNT(*) FROM series WHERE id = ?", (original_series_id,)
        ).fetchone()[0] == 0

        # The stale answer is the bug under test: without eviction this
        # would still return 500000.0 even though the row backing it is gone.
        assert core.baselines("leak", eid, "rss_bytes") is None

        core.on_tick(clock.now(), clock.monotonic(), 0.0)  # tick 7: e1 reappears
        clock.advance(60.0)
        core.on_tick(clock.now(), clock.monotonic(), 0.0)  # settle

        new_series_id = core.conn.execute(
            "SELECT id FROM series WHERE monitor='leak' AND entity_id=? AND metric='rss_bytes'",
            (eid,),
        ).fetchone()["id"]
        assert new_series_id != original_series_id, (
            "writer handed out a stale cached series id for a returning identity"
        )
        report = doctor_inspect(core.conn, now=clock.now())
        assert report["orphans"]["samples"] == 0


class TestDegradationVisibility:
    """[DM-05] Issue #102: durable degradation must be a state, not a stream."""

    def _core(self, tmp_path, monkeypatch):
        for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
            monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
        from ftmon.paths import get_paths

        paths = get_paths()
        paths.ensure()
        paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
        paths.config_file.chmod(0o600)
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        return DaemonCore(paths=paths, clock=clock, platform="linux"), clock

    def test_degrading_gauge_tracks_the_last_pass_dm_05(self, tmp_path, monkeypatch):
        """[DM-05][RB-02] A 0/1 gauge, so rules window it rather than the
        daemon hard-coding what "persistently degrading" means."""
        core, clock = self._core(tmp_path, monkeypatch)
        try:
            assert core.stats.db_degrading == 0.0
            core.retention.run = lambda now: ["db over budget: pruned 5000 rows (x)"]
            core._run_retention(clock.now())
            assert core.stats.db_degrading == 1.0
            assert core.stats.counters["db_degradations"] == 1

            core.retention.run = lambda now: []
            core._run_retention(clock.now() + 60.0)
            assert core.stats.db_degrading == 0.0, "gauge follows the last pass"
            # The counter is monotonic; only the gauge falls back.
            assert core.stats.counters["db_degradations"] == 1
        finally:
            core.conn.close()

    def test_degradation_events_are_throttled_dm_05(self, tmp_path, monkeypatch):
        """[DM-05] A permanently degrading daemon emitted ~15 events an hour.

        That buried the transition worth noticing under a stream nobody reads,
        which is the observability half of issue #102. The events remain as
        forensic detail, rate-limited, and report how many passes they cover.
        """
        core, clock = self._core(tmp_path, monkeypatch)
        try:
            core.retention.run = lambda now: ["db over budget: pruned 5000 rows (x)"]
            wall = clock.now()
            for i in range(30):  # 30 minutes of degrading passes
                core._run_retention(wall + i * 60.0)
            core.writer.commit_tick()

            events = core.conn.execute(
                "SELECT message FROM events WHERE provider='ftmon.retention' "
                "ORDER BY ts"
            ).fetchall()
            # 30 passes at a 600 s throttle: far fewer than one event each.
            assert 1 <= len(events) <= 4, f"expected throttled events, got {len(events)}"
            # DM-05: *every* report says what it covers, including the first.
            # `any` would pass while the first report omitted its count.
            assert all("covered by this report" in r["message"] for r in events)
            # Every pass still counts, so a rate is recoverable from the metric.
            assert core.stats.counters["db_degradations"] == 30
        finally:
            core.conn.close()


def add_rollup1h(conn, sid, buckets):
    conn.executemany(
        "INSERT INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
        "VALUES (?,?,1.0,1.0,1.0,1.0,1)",
        [(sid, b) for b in buckets],
    )
    conn.commit()


class _TickingClock:
    """Monotonic that advances a fixed step per reading (TS-03: tests may not
    call time.monotonic either). Wall time is unused by the expiry budget."""

    def __init__(self, step=0.0, start=1000.0):
        self._mono = start
        self._step = step

    def now(self):
        return T0

    def monotonic(self):
        value = self._mono
        self._mono += self._step
        return value

    def sleep_until(self, mono_deadline):  # pragma: no cover - never slept in tests
        raise AssertionError("retention must not sleep")


_GONE = 7 * 86400


class TestExpireGoneRollup1h:
    """[MD-09][DM-04] v0.49: hourly rollups of long-gone entities are expired
    so the catalog stops being pinned by its longest retention window."""

    def _gone_entity_with_rollups(self, conn, *, gone_age, buckets=3, sid=1,
                                  entity="e", durable=0):
        add_series(conn, sid, entity=entity, durable=durable)
        add_entity(conn, entity=entity, gone_ts=T0 - gone_age,
                   first_seen=T0 - 10 * _GONE, last_seen=T0 - gone_age)
        add_rollup1h(conn, sid, [T0 - 100 * (i + 1) for i in range(buckets)])

    def test_expires_rollups_of_entity_gone_beyond_window(self, conn):
        """[MD-09] an entity gone longer than DM-04's process 5-minute window
        loses its hourly rollups, and the deletion is attributed to it."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1)

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 0
        assert r.rollup1h_expired == 3
        assert r.expired_keys == [("m", "e")]

    def test_live_entity_keeps_hourly_history_at_any_age(self, conn):
        """[MD-09][DM-04] durability describes the source kind, not liveness:
        a long-running process keeps its full hourly window, which is the
        whole reason expiry keys on gone-duration instead of the window."""
        add_series(conn, 1, durable=0)
        add_entity(conn, gone_ts=None, first_seen=T0 - 400 * 86400)
        # Inside the 90 d process window, so normal DM-04 pruning leaves them
        # alone and the only thing that could remove them is expiry -- all far
        # older than the 7 d gone threshold, which is the point being tested.
        add_rollup1h(conn, 1, [T0 - 80 * 86400, T0 - 10 * 86400, T0 - 100])

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 3
        assert r.rollup1h_expired == 0
        assert r.expired_keys == []

    def test_recently_gone_entity_is_not_expired(self, conn):
        """[MD-09] inside the threshold nothing is expired: below 7 d the
        entity may still hold rollup5m rows DM-04 separately promises."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE - 3600)

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 3
        assert r.rollup1h_expired == 0

    def test_entity_reaps_on_a_later_pass_after_expiry(self, conn):
        """[MD-09] expiry is not a second removal rule — it lets the existing
        emptiness test succeed. The entity survives the pass that empties it
        and is collected by the unchanged reap path afterwards."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1)

        r = Retention(conn)
        r.run(now=T0)
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
        assert r.entities_reaped == 0

        r.run(now=T0 + 60)
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0
        assert r.entities_reaped == 1

    def test_row_budget_bounds_the_pass_and_progress_resumes(self, conn):
        """[MD-09] REAP_SCAN bounds entities visited, not work done: one
        entity owning more rows than the budget is expired across passes
        rather than making a single pass unbounded."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=25)

        r = Retention(conn, expire_rows=10, delete_batch=10)
        r.run(now=T0)
        assert r.rollup1h_expired == 10
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 15

        r.run(now=T0 + 60)
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 5
        r.run(now=T0 + 120)
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 0

    def test_wall_budget_stops_the_pass_between_entities(self, conn):
        """[MD-09] the elapsed-time bound protects DM-04's 1 s/cycle when the
        row budget alone would not — a slow disk must not extend the pass."""
        for i in range(5):
            self._gone_entity_with_rollups(
                conn, gone_age=_GONE + 1, buckets=2, sid=i + 1, entity=f"e{i}")

        # Each monotonic() reading jumps a full second, so the deadline is
        # already blown when the first entity's budget check runs.
        r = Retention(conn, expire_seconds=0.25, clock=_TickingClock(step=1.0))
        r.run(now=T0)

        assert len(r.expired_keys) == 1
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 8

    def test_wall_budget_still_makes_progress(self, conn):
        """[MD-09] a blown budget must not livelock: the check happens after
        an entity is handled, so every pass removes something."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=2)

        r = Retention(conn, expire_seconds=0.0, clock=_TickingClock(step=1.0))
        r.run(now=T0)

        assert r.rollup1h_expired == 2

    def test_resurrected_entity_is_no_longer_expired(self, conn):
        """[MD-09] a process that reappears is live from that moment: gone_ts
        returns to NULL and the candidate query simply stops matching, which
        is why the predicate keys on gone_ts rather than a derived flag."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=4)
        conn.execute("UPDATE entities SET gone_ts = NULL WHERE entity_id = 'e'")
        conn.commit()

        r = Retention(conn)
        r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 4
        assert r.rollup1h_expired == 0

    def test_expiry_is_idempotent_once_drained(self, conn):
        """[MD-09] repeated passes over an already-expired catalog delete
        nothing and report nothing."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=2)

        r = Retention(conn)
        r.run(now=T0)
        for i in range(3):
            r.run(now=T0 + 60 * (i + 1))
            assert r.rollup1h_expired == 0
            assert r.expired_keys == []

    def test_expiry_runs_when_the_reap_cursor_wraps(self, conn):
        """[MD-09] expiry is its own stage: _reap_catalog returns early once
        the cursor passes the last entity, and that must not skip expiry."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=2)
        conn.execute("UPDATE meta SET value = 'zzzz' WHERE key = 'reap_cursor_monitor'")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('reap_cursor_monitor','zzzz')")
        conn.commit()

        r = Retention(conn)
        r.run(now=T0)

        assert r.rollup1h_expired == 2

    def test_failed_pass_rolls_back_the_deletion(self, conn):
        """[MD-09][PM-10] expiry destroys production history irreversibly, so
        it must be inside the pass transaction: a later stage raising leaves
        every row intact rather than half-deleted."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=3)

        r = Retention(conn)
        r._cap_incident_history = lambda cur: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            r.run(now=T0)

        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 3

    def test_expiry_row_count_is_published_to_meta(self, conn):
        """[MD-09][CL-05] the deletion is attributable from the database, not
        only from an in-process counter an operator cannot see."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=3)

        Retention(conn).run(now=T0)

        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_expire_rows'").fetchone()
        assert int(row["value"]) == 3

    def test_durable_series_are_never_expired(self, conn):
        """[MD-09][DM-04] expiry is process-only. The 7 d threshold is
        justified by no *other* DM-04 window still holding data, which is
        true only for process series: durable ones keep 5-minute data 30 d
        and hourly 400 d. A canary caught this deleting 3,624 rows of `disk`
        history for snap mounts gone ~25 d, so the guard is a regression test
        with a scar, not a hypothetical."""
        self._gone_entity_with_rollups(
            conn, gone_age=_GONE + 1, buckets=4, sid=1, entity="proc", durable=0)
        self._gone_entity_with_rollups(
            conn, gone_age=100 * 86400, buckets=4, sid=2, entity="disk0", durable=1)

        r = Retention(conn)
        r.run(now=T0)

        kept = conn.execute(
            "SELECT COUNT(*) FROM rollup1h r JOIN series s ON s.id = r.series_id "
            "WHERE s.durable = 1").fetchone()[0]
        assert kept == 4, "durable hourly history must survive expiry"
        assert r.rollup1h_expired == 4
        assert r.expired_keys == [("m", "proc")]

    def test_row_budget_is_exact_when_it_falls_mid_batch(self, conn):
        """[MD-09] the budget clamps the final batch rather than overshooting
        by up to one batch: with a budget that is not a multiple of the batch
        size, the pass stops exactly on it."""
        self._gone_entity_with_rollups(conn, gone_age=_GONE + 1, buckets=25)

        r = Retention(conn, expire_rows=15, delete_batch=10)
        r.run(now=T0)

        assert r.rollup1h_expired == 15
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 10

    def test_entity_enumeration_is_bounded_per_pass(self, conn):
        """[MD-09] the candidate query is bounded too: a catalog with many
        long-gone entities is worked through across passes, so one pass never
        enumerates the whole backlog however cheap each entity looks."""
        for i in range(5):
            self._gone_entity_with_rollups(
                conn, gone_age=_GONE + 1, buckets=2, sid=i + 1, entity=f"e{i}")

        r = Retention(conn, expire_entities=2, clock=_TickingClock(step=0.0))
        r.run(now=T0)

        assert len(r.expired_keys) == 2
        assert r.rollup1h_expired == 4
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] == 6


class TestStageTimings:
    """[RB-02][TS-03] Where the tick goes, measured on the injected clock.

    `cycle_s` says a tick cost 240 ms without saying whether sampling,
    evaluation, the commit or retention explains it -- the question #107's
    scope turns on. #97 could only answer it with a disposable profiler
    against a database clone.
    """

    def test_prune_and_reap_are_timed_separately(self, conn):
        """[RB-02] the two differ by an order of magnitude in the spike, so
        one retention_s would not separate them."""
        add_series(conn, 1, durable=0)
        add_entity(conn, gone_ts=T0 - 100)
        conn.commit()

        clock = _TickingClock(step=0.5)
        r = Retention(conn, clock=clock)
        r.run(now=T0)

        assert r.prune_s > 0.0
        assert r.reap_s > 0.0

    def test_a_failed_pass_reports_no_timing_rather_than_the_last_one(self, conn):
        """[RB-02][PM-10] The reset exists for the failure path.

        Both gauges are assigned unconditionally when their stage completes,
        so a *successful* pass always overwrites them. The case that needs the
        reset is a pass that raises before reaching those assignments: without
        it the daemon would publish the previous pass's cost as though it
        measured the failed one, which is exactly the tick an operator is
        investigating.
        """
        add_series(conn, 1)
        conn.commit()

        r = Retention(conn, clock=_TickingClock(step=0.25))
        r.run(now=T0)
        assert r.prune_s > 0.0, "first pass measured something"

        r._rollup_5m = lambda cur, now: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            r.run(now=T0 + 60)

        assert r.prune_s == 0.0, "a failed pass must not inherit a timing"
        assert r.reap_s == 0.0

    def test_no_direct_clock_access(self):
        """[TS-03] timings come from the injected Clock, never time.monotonic.

        Enforced globally by the TS-03 lint, asserted here because stage
        timing is the change most tempted to reach for the wall clock.
        """
        source = (REPO_ROOT / "src" / "ftmon" / "store" / "retention.py").read_text()
        assert "time.monotonic(" not in source
        assert "self._clock.monotonic()" in source

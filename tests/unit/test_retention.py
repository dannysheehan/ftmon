"""[DM-04][DM-05][CA-05][CA-06] Rollups, retention windows, degradation
order, and EW-mean baselines — golden-value tests against a real SQLite db."""

from __future__ import annotations

import pytest

from ftmon.store.db import connect, migrate
from ftmon.store.retention import (
    BaselineLookup,
    Retention,
    reset_baselines,
)

T0 = 1_700_000_100  # deliberately not bucket-aligned


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


class TestPruneAndDegrade:
    def test_normal_retention_windows(self, conn):
        """[DM-04] raw 48h; 5m 30d; 1h 400d durable / 90d process; events 30d."""
        now = T0 + 500 * 86400
        add_series(conn, 1, durable=1)
        add_series(conn, 2, monitor="leak", entity="p", durable=0)
        add_samples(conn, 1, [(now - 49 * 3600, 1.0), (now - 3600, 2.0)])
        conn.executemany(
            "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
            "VALUES (?,?,1,1,1,1,1)",
            [(1, now - 31 * 86400), (1, now - 86400)])
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
        assert conn.execute("SELECT COUNT(*) FROM rollup5m WHERE bucket < ?",
                            (now - 30 * 86400,)).fetchone()[0] == 0
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

    def test_under_budget_never_degrades(self, conn):
        """[DM-05] a healthy database takes no degradation steps at all."""
        add_series(conn, 1)
        add_samples(conn, 1, [(T0, 1.0)])
        assert Retention(conn).run(now=T0 + 60) == []
        assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1

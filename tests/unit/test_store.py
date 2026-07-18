"""[DM-01..06][DM-15][PM-03][VC-01][UI-05][MC-02] SQLite storage layer (WP4)."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from ftmon.model import EventRecord
from ftmon.store import db
from ftmon.store.query import Query, SeriesPoint, lttb
from ftmon.store.writer import TickWriter

NOW = 2_000_000_000.0


def _fresh(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "ftmon.db")
    db.migrate(conn)
    return conn


def test_migrate_idempotent_and_pragmas(tmp_path):
    """[VC-01] migrate() twice is idempotent; WAL + incremental auto_vacuum active."""
    conn = db.connect(tmp_path / "ftmon.db")

    v1 = db.migrate(conn)
    v2 = db.migrate(conn)

    assert v1 == 4
    assert v2 == 4
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # 2 == incremental (0 == none, 1 == full, 2 == incremental)
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2


def test_migrate_table_shape(tmp_path):
    """[VC-01] the expected tables exist after migration."""
    conn = _fresh(tmp_path)
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "meta",
        "series",
        "samples",
        "rollup5m",
        "rollup1h",
        "entities",
        "events",
        "incidents",
        "incident_history",
        "notifications",
        "notification_deliveries",
        "baselines",
        "cursors",
        "monitor_loads",
        "action_runs",
    }
    assert expected <= tables
    baseline_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(baselines)")
    }
    assert baseline_columns == {
        "series_id", "value", "updates", "updated_bucket", "half_life_s"
    }


def test_migrate_v3_baselines_gain_default_half_life(tmp_path):
    """[CA-05] v3 baselines retain their learned state and gain the historic default."""
    conn = db.connect(tmp_path / "ftmon.db")
    migrations = Path(db.__file__).parent / "migrations"
    for number in range(1, 4):
        path = next(migrations.glob(f"{number:04d}_*.sql"))
        conn.executescript(path.read_text())
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO series(id, monitor, entity_id, metric, durable) "
        "VALUES (1, 'leak', 'pid:1', 'rss_bytes', 0)"
    )
    conn.execute(
        "INSERT INTO baselines(series_id, value, updates, updated_bucket) "
        "VALUES (1, 42.5, 17, 1200)"
    )
    conn.commit()

    assert db.migrate(conn) == 4
    row = conn.execute("SELECT * FROM baselines").fetchone()
    assert (row["value"], row["updates"], row["updated_bucket"]) == (42.5, 17, 1200)
    assert row["half_life_s"] == 259200
    assert db.migrate(conn) == 4
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_migrate_v2_outbox_preserves_legacy_delivery_state(tmp_path):
    """[DM-14][DM-18][NO-04] v2 rows become immutable notifications and
    independent file deliveries without inventing remote-channel work."""
    conn = db.connect(tmp_path / "ftmon.db")
    migrations = Path(db.__file__).parent / "migrations"
    conn.executescript((migrations / "0001_init.sql").read_text())
    conn.executescript((migrations / "0002_action_runs.sql").read_text())
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(11,'disk','space','/srv','open',3,'space-error',90,90,1,1)"
    )
    conn.executemany(
        "INSERT INTO outbox(id,incident_id,kind,body,created_ts,delivered_ts,stale) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (1, 11, "open", '{"severity":3,"title":"Disk","body":"full"}',
             100, 110, 0),
            (2, 12, "renotify", '{"severity":2,"title":"Memory","body":"growing"}',
             200, None, 0),
            (3, 13, "open", '{"severity":1,"title":"Old","body":"ignored"}',
             300, None, 1),
        ],
    )
    conn.commit()

    assert db.migrate(conn) == 4
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='outbox'"
    ).fetchone() is None
    notifications = conn.execute(
        "SELECT id,severity,title,body,monitor,entity_id FROM notifications ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in notifications] == [
        (1, 3, "Disk", "full", "disk", "/srv"),
        (2, 2, "Memory", "growing", "", ""),
        (3, 1, "Old", "ignored", "", ""),
    ]
    deliveries = conn.execute(
        "SELECT notification_id,channel,state,next_attempt_ts,delivered_ts,last_error "
        "FROM notification_deliveries ORDER BY notification_id"
    ).fetchall()
    assert [tuple(row) for row in deliveries] == [
        (1, "file", "delivered", None, 110, None),
        (2, "file", "pending", 200, None, None),
        (3, "file", "failed", None, None, "legacy stale delivery"),
    ]


def test_samples_reject_nan_inf(tmp_path):
    """[DM-01] NaN/inf samples are rejected + counted; valid samples are stored."""
    conn = _fresh(tmp_path)
    rejects = []
    w = TickWriter(conn, on_reject=rejects.append)
    sid = w.series_id("disk", "/", "free_pct", True)

    w.add_sample(sid, NOW, float("nan"))
    w.add_sample(sid, NOW + 1, float("inf"))
    w.add_sample(sid, NOW + 2, float("-inf"))
    w.add_sample(sid, NOW + 3, 42.5)
    w.commit_tick()

    assert rejects == ["samples_rejected"] * 3
    rows = conn.execute("SELECT ts, value FROM samples").fetchall()
    assert len(rows) == 1
    assert rows[0]["value"] == 42.5


def test_commit_tick_visibility_wal(tmp_path):
    """[PM-03] a readonly connection sees nothing before commit_tick(), and
    the committed rows after (WAL allows concurrent readers)."""
    path = tmp_path / "ftmon.db"
    conn = db.connect(path)
    db.migrate(conn)

    w = TickWriter(conn)
    sid = w.series_id("disk", "/", "free_pct", True)
    w.add_sample(sid, NOW, 10.0)

    reader = db.connect(path, readonly=True)
    assert reader.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    assert reader.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0

    w.commit_tick()

    assert reader.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert reader.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 1


def test_entity_attrs_cap(tmp_path):
    """[DM-03] oversize attrs are truncated with a marker and fit in 4096 bytes."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)
    big_attrs = {f"key{i}": "x" * 500 for i in range(20)}
    w.upsert_entity("process", "pid-123", NOW, big_attrs)
    w.commit_tick()

    row = conn.execute(
        "SELECT attrs FROM entities WHERE monitor='process' AND entity_id='pid-123'"
    ).fetchone()
    assert len(row["attrs"].encode("utf-8")) <= 4096
    decoded = json.loads(row["attrs"])
    assert decoded.get("truncated") == "true"


def test_entity_attrs_under_cap_untouched(tmp_path):
    """[DM-03] small attrs dicts are stored verbatim, no truncation marker."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)
    w.upsert_entity("disk", "/", NOW, {"fstype": "ext4", "device": "/dev/sda1"})
    w.commit_tick()

    row = conn.execute("SELECT attrs FROM entities WHERE entity_id='/'").fetchone()
    decoded = json.loads(row["attrs"])
    assert decoded == {"fstype": "ext4", "device": "/dev/sda1"}


def test_glance_selects_latest_active_entities_with_deterministic_aggregate_ui_17(tmp_path):
    """[UI-17] Current raw evidence is reduced only after latest-per-entity selection."""
    conn = _fresh(tmp_path)
    conn.executemany(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts,attrs) "
        "VALUES('disk',?,?,1000,?,NULL)",
        [
            ("/a", 800, None),
            ("/b", 800, None),
            ("/c", 800, None),
            ("/gone", 800, 999),
            ("/stale", 700, None),
        ],
    )
    conn.executemany(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(?,'disk',?,'used_pct',1)",
        [(1, "/a"), (2, "/b"), (3, "/gone"), (4, "/stale"), (5, "/c")],
    )
    conn.executemany(
        "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
        [
            (1, 900, 99), (1, 990, 80),
            (2, 995, 80),
            (3, 999, 100),
            (4, 700, 1),
            (5, 990, 70),
        ],
    )
    query = Query(conn)
    samples = query.glance_samples("disk", "used_pct", not_before=880)
    assert [(item.entity_id, item.ts, item.value) for item in samples] == [
        ("/a", 990, 80),
        ("/b", 995, 80),
        ("/c", 990, 70),
    ]
    assert query.glance_samples("disk", "used_pct", not_before=996) == ()


def test_forget_entity_removes_samples_rollups_baseline_and_catalog_state_ca_07(tmp_path):
    """[CA-07] Exemption purges every metric surface in the tick transaction."""
    conn = _fresh(tmp_path)
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('disk','/snap',100,200,'{}')"
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'disk','/snap','used_pct',1)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(1,200,100)")
    conn.execute(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,0,100,100,100,100,1)"
    )
    conn.execute(
        "INSERT INTO rollup1h(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,0,100,100,100,100,1)"
    )
    conn.execute(
        "INSERT INTO baselines(series_id,value,updates,updated_bucket,half_life_s) "
        "VALUES(1,100,240,0,259200)"
    )
    conn.commit()

    writer = TickWriter(conn)
    writer.forget_entity("disk", "/snap")
    writer.commit_tick()

    for table in ("entities", "series", "samples", "rollup5m", "rollup1h", "baselines"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_series_id_interned_across_writer_instances(tmp_path):
    """series_id() is stable across separate TickWriter instances/connections."""
    path = tmp_path / "ftmon.db"
    conn1 = db.connect(path)
    db.migrate(conn1)
    w1 = TickWriter(conn1)
    sid1 = w1.series_id("disk", "/", "used_pct", True)
    w1.commit_tick()

    # Same connection, new instance: cache is cold, must consult the DB.
    w2 = TickWriter(conn1)
    assert w2.series_id("disk", "/", "used_pct", True) == sid1

    # A brand new connection sees the same committed row.
    conn2 = db.connect(path)
    w3 = TickWriter(conn2)
    assert w3.series_id("disk", "/", "used_pct", True) == sid1

    # Calling it again on w1 (warm cache) is also stable and doesn't insert
    # a second row.
    assert w1.series_id("disk", "/", "used_pct", True) == sid1
    count = conn1.execute(
        "SELECT COUNT(*) FROM series WHERE monitor='disk' AND entity_id='/' "
        "AND metric='used_pct'"
    ).fetchone()[0]
    assert count == 1


def test_tier_selection_raw_5m_1h(tmp_path):
    """[DM-06][UI-05] the query layer picks raw/5m/1h by range, transparently."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)
    sid = w.series_id("system", "host1", "load1", True)

    # Raw samples: last hour.
    for i in range(5):
        w.add_sample(sid, NOW - i * 60, 1.0 + i)

    w.commit_tick()

    # 5-minute rollups ~20 days ago (written directly; rollup jobs are a
    # different work package, not writer.py's concern).
    bucket_5m_base = round(NOW - 20 * 86400)
    conn.executemany(
        "INSERT INTO rollup5m(series_id, bucket, avg, min, max, last, cnt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (sid, bucket_5m_base + i * 300, 2.0 + i, 1.0, 3.0, 2.0, 5)
            for i in range(5)
        ],
    )

    # 1-hour rollups ~200 days ago.
    bucket_1h_base = round(NOW - 200 * 86400)
    conn.executemany(
        "INSERT INTO rollup1h(series_id, bucket, avg, min, max, last, cnt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (sid, bucket_1h_base + i * 3600, 3.0 + i, 1.0, 5.0, 3.0, 60)
            for i in range(5)
        ],
    )
    conn.commit()

    q = Query(conn)

    recent = q.series("system", "load1", now=NOW, start=NOW - 6 * 3600, end=NOW)
    assert len(recent) == 1
    assert recent[0].resolution == "raw"
    assert len(recent[0].points) == 5

    mid = q.series(
        "system", "load1", now=NOW, start=NOW - 20 * 86400 - 1800, end=NOW - 20 * 86400 + 1800
    )
    assert len(mid) == 1
    assert mid[0].resolution == "5m"
    assert len(mid[0].points) == 5

    far = q.series("system", "load1", now=NOW, start=NOW - 200 * 86400, end=NOW)
    assert len(far) == 1
    assert far[0].resolution == "1h"
    assert len(far[0].points) == 5


def test_lttb_downsamples_exactly_and_keeps_endpoints():
    """[DM-06][UI-05] LTTB reduces 10000 points to exactly max_points, keeping ends."""
    points = [SeriesPoint(ts=i, value=math.sin(i / 37.0) * 100 + i * 0.01) for i in range(10_000)]
    out = lttb(points, 500)
    assert len(out) == 500
    assert out[0] == points[0]
    assert out[-1] == points[-1]


def test_lttb_noop_when_already_small():
    points = [SeriesPoint(ts=i, value=float(i)) for i in range(10)]
    assert lttb(points, 2000) == points


def test_cursor_roundtrip(tmp_path):
    """[DM-15] cursor set/get roundtrip, including updates."""
    conn = _fresh(tmp_path)
    q = Query(conn)
    assert q.cursor("journald") is None

    w = TickWriter(conn)
    w.set_cursor("journald", "cursor-abc", NOW)
    w.commit_tick()
    assert q.cursor("journald") == "cursor-abc"

    w.set_cursor("journald", "cursor-def", NOW + 60)
    w.commit_tick()
    assert q.cursor("journald") == "cursor-def"


def test_events_insert_and_filter(tmp_path):
    """events insert + filter by min_severity/provider/limit."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)

    records = [
        EventRecord(NOW, NOW, "journald", "sshd", None, 1, "info msg"),
        EventRecord(NOW + 1, NOW + 1, "journald", "sshd", None, 3, "error msg"),
        EventRecord(NOW + 2, NOW + 2, "journald", "cron", None, 4, "critical msg"),
        EventRecord(NOW + 3, NOW + 3, "journald", "sshd", None, 2, "warning msg"),
    ]
    ids = [w.add_event(r) for r in records]
    assert ids == sorted(ids)  # ingest order (DM-15)
    w.commit_tick()

    q = Query(conn)

    all_events = q.events(start=NOW - 10, end=NOW + 10)
    assert len(all_events) == 4

    sshd_only = q.events(start=NOW - 10, end=NOW + 10, provider="sshd")
    assert len(sshd_only) == 3
    assert all(r["provider"] == "sshd" for r in sshd_only)

    severe = q.events(start=NOW - 10, end=NOW + 10, min_severity=3)
    assert len(severe) == 2
    assert {r["message"] for r in severe} == {"error msg", "critical msg"}

    limited = q.events(start=NOW - 10, end=NOW + 10, limit=1)
    assert len(limited) == 1
    # ORDER BY ts DESC -> most recent first
    assert limited[0]["message"] == "warning msg"


def test_entity_first_seen_stable_last_seen_advances_gone(tmp_path):
    """entity first_seen stable across upserts, last_seen advances, gone_ts set."""
    conn = _fresh(tmp_path)

    w1 = TickWriter(conn)
    w1.upsert_entity("process", "pid-7", NOW, {"name": "sleep"})
    w1.commit_tick()

    row = conn.execute(
        "SELECT first_seen, last_seen, gone_ts FROM entities WHERE entity_id='pid-7'"
    ).fetchone()
    assert row["first_seen"] == round(NOW)
    assert row["last_seen"] == round(NOW)
    assert row["gone_ts"] is None

    w2 = TickWriter(conn)
    w2.upsert_entity("process", "pid-7", NOW + 120, {"name": "sleep"})
    w2.commit_tick()

    row = conn.execute(
        "SELECT first_seen, last_seen, gone_ts FROM entities WHERE entity_id='pid-7'"
    ).fetchone()
    assert row["first_seen"] == round(NOW)  # unchanged
    assert row["last_seen"] == round(NOW + 120)  # advanced

    w3 = TickWriter(conn)
    w3.upsert_entity("process", "pid-7", NOW + 400, {"name": "sleep"}, gone_ts=NOW + 400)
    w3.commit_tick()

    row = conn.execute(
        "SELECT first_seen, last_seen, gone_ts FROM entities WHERE entity_id='pid-7'"
    ).fetchone()
    assert row["first_seen"] == round(NOW)
    assert row["gone_ts"] == round(NOW + 400)


def test_monitor_loads_keeps_last_20(tmp_path):
    """[PM-07] record_monitor_load retains only the last 20 loads per monitor."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)
    for i in range(25):
        w.record_monitor_load("disk", NOW + i, f"hash{i}", "normalized-src")
        w.commit_tick()

    rows = conn.execute(
        "SELECT loaded_ts FROM monitor_loads WHERE monitor='disk' ORDER BY loaded_ts"
    ).fetchall()
    assert len(rows) == 20
    kept = [r["loaded_ts"] for r in rows]
    assert kept == [round(NOW + i) for i in range(5, 25)]


def test_status(tmp_path):
    """status() reports last tick age, db size, and open incident count."""
    conn = _fresh(tmp_path)
    w = TickWriter(conn)
    w.set_meta("last_tick_ts", str(NOW))
    w.commit_tick()

    conn.execute(
        "INSERT INTO incidents(id, monitor, grp, entity_id, state) "
        "VALUES (1, 'disk', 'g', '/', 'open')"
    )
    conn.execute(
        "INSERT INTO incidents(id, monitor, grp, entity_id, state) "
        "VALUES (2, 'disk', 'g', '/tmp', 'cleared')"
    )
    conn.commit()

    q = Query(conn)
    status = q.status(now=NOW + 30)
    assert status["last_tick_ts"] == NOW
    assert status["last_tick_age_s"] == 30
    assert status["db_bytes"] > 0
    assert status["open_incidents"] == 1


def _insert_incident(conn, incident_id, *, monitor="leak", grp="rss", entity_id="firefox:7:1"):
    conn.execute(
        "INSERT INTO incidents(id, monitor, grp, entity_id, state, severity, owning_rule, "
        "opened_ts, last_change_ts, cleared_ts, clear_reason, ack_by, ack_ts, "
        "notify_count, occurrences, flapping) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            incident_id, monitor, grp, entity_id, "open", 2, "rss-growth",
            1_000, 1_100, None, None, None, None, 1, 1, 0,
        ),
    )


def test_incident_detail_unknown(tmp_path):
    """[DM-11] incident_detail returns None for an unknown id."""
    conn = _fresh(tmp_path)
    assert Query(conn).incident_detail(404) is None


def test_incident_detail_complete_row_and_history(tmp_path):
    """[DM-11][DM-12] incident_detail returns the full row and ordered history."""
    conn = _fresh(tmp_path)
    _insert_incident(conn, 7)
    conn.executemany(
        "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (7, 1, 1_000, "opened", '{"severity":2}'),
            (7, 2, 1_050, "renotify", '{"severity":2}'),
            (7, 3, 1_100, "acked", '{"by":"cli","note":"seen"}'),
        ],
    )
    conn.commit()

    detail = Query(conn).incident_detail(7)
    assert detail is not None
    row = detail.incident
    assert row["id"] == 7
    assert row["monitor"] == "leak"
    assert row["grp"] == "rss"
    assert row["entity_id"] == "firefox:7:1"
    assert row["state"] == "open"
    assert row["severity"] == 2
    assert row["owning_rule"] == "rss-growth"
    assert row["opened_ts"] == 1_000
    assert row["last_change_ts"] == 1_100
    assert row["notify_count"] == 1
    assert row["occurrences"] == 1
    assert row["flapping"] == 0
    assert [entry.kind for entry in detail.history] == ["opened", "renotify", "acked"]
    assert detail.history[2].detail == {"by": "cli", "note": "seen"}


def test_incident_detail_no_cross_incident_leakage(tmp_path):
    """[DM-12] history is scoped to the requested incident id."""
    conn = _fresh(tmp_path)
    _insert_incident(conn, 1, monitor="hog", grp="hog", entity_id="evil")
    _insert_incident(conn, 2, monitor="disk", grp="space", entity_id="/")
    conn.executemany(
        "INSERT INTO incident_history(incident_id, seq, ts, kind, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 900, "opened", '{"severity":3}'),
            (2, 1, 910, "opened", '{"severity":1}'),
            (1, 2, 920, "cleared", '{"reason":"recovered"}'),
        ],
    )
    conn.commit()

    detail = Query(conn).incident_detail(1)
    assert detail is not None
    assert detail.incident["monitor"] == "hog"
    assert [entry.kind for entry in detail.history] == ["opened", "cleared"]
    assert all(entry.seq in (1, 2) for entry in detail.history)


def test_commit_tick_drops_buffers_on_lock_timeout(tmp_path):
    """[PM-10] BEGIN IMMEDIATE lock failure clears pending buffers (no retry burst)."""
    path = tmp_path / "ftmon.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("PRAGMA busy_timeout = 50")

    w = TickWriter(conn)
    sid = w.series_id("disk", "/", "free_pct", True)
    w.add_sample(sid, NOW, 10.0)
    w.set_meta("last_tick_ts", str(NOW))

    locker = db.connect(path)
    locker.execute("PRAGMA busy_timeout = 0")
    locker.execute("BEGIN IMMEDIATE")
    try:
        try:
            w.commit_tick()
            raise AssertionError("expected OperationalError for locked database")
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc).lower()
    finally:
        locker.rollback()
        locker.close()

    assert w._pending_samples == []
    assert w._pending_series == []
    assert w._pending_meta == {}
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0

    # After the lock clears, a fresh buffer commits normally.
    w.add_sample(sid, NOW + 1, 11.0)
    w.commit_tick()
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_series_first_seen_during_lock_timeout_recovers(tmp_path):
    """[PM-10] a series row dropped with the failed tick is re-created on the
    next tick — the id cache must not outlive the rolled-back insert, or the
    samples become unqueryable orphans and the id is reused after restart."""
    path = tmp_path / "ftmon.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("PRAGMA busy_timeout = 50")
    w = TickWriter(conn)

    locker = db.connect(path)
    locker.execute("PRAGMA busy_timeout = 0")
    locker.execute("BEGIN IMMEDIATE")
    try:
        sid = w.series_id("gpu", "card0", "vram_used", True)
        w.add_sample(sid, NOW, 5.0)
        try:
            w.commit_tick()
            raise AssertionError("expected OperationalError for locked database")
        except sqlite3.OperationalError:
            pass
    finally:
        locker.rollback()
        locker.close()

    sid2 = w.series_id("gpu", "card0", "vram_used", True)
    w.add_sample(sid2, NOW + 5, 6.0)
    w.commit_tick()

    visible = conn.execute(
        "SELECT COUNT(*) FROM samples JOIN series ON samples.series_id = series.id "
        "WHERE series.metric = 'vram_used'"
    ).fetchone()[0]
    assert visible == 1

    # Restart: a fresh writer allocating a new series must not inherit the
    # vram samples through id reuse.
    w2 = TickWriter(conn)
    other = w2.series_id("disk", "/", "free_pct", True)
    w2.add_sample(other, NOW + 10, 42.0)
    w2.commit_tick()
    stolen = conn.execute(
        "SELECT COUNT(*) FROM samples JOIN series ON samples.series_id = series.id "
        "WHERE series.metric = 'free_pct'"
    ).fetchone()[0]
    assert stolen == 1


def test_no_direct_clock_reads_in_store_package():
    """[TS-03] lint: store/*.py never reads a clock directly."""
    store_dir = Path(__file__).resolve().parents[2] / "src" / "ftmon" / "store"
    offenders = []
    for py in store_dir.rglob("*.py"):
        text = py.read_text()
        for needle in ("time.time(", "time.monotonic(", "datetime.now(", "time.sleep("):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == []

"""M6 database diagnostics tests [CL-05][VC-03]."""

import sqlite3

from ftmon.cli import main
from ftmon.paths import get_paths
from ftmon.sources.base import SOURCE_DECLS
from ftmon.store.db import connect, db_size_report, migrate
from ftmon.store.doctor import (
    _MAX_MONITOR_ATTRIBUTION,
    _MONITOR_ATTRIBUTION_SQL,
    backup,
    catalog_report,
    inspect,
)
from tests.platform_permissions import assert_private


def test_doctor_clean_database_cl_05(tmp_path):
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    report = inspect(conn, now=1000)
    assert report["ok"]
    assert report["integrity"] == ["ok"]
    assert "samples" in report["tables"]
    assert not any(report["orphans"].values())
    conn.close()


def test_db_size_report_is_the_one_dm05_arithmetic_issue_104(tmp_path):
    """[DM-05][CL-05] doctor.inspect() and Query.status() must both read
    db_size_report()'s figures rather than each re-deriving
    (page_count - freelist_count) * page_size -- issue #104 exists because
    that duplication is exactly how file-vs-used got confused before."""
    from ftmon.store.query import Query

    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("INSERT INTO meta(key, value) VALUES ('last_tick_ts', '1000')")
    conn.commit()

    size = db_size_report(conn)
    assert size["allocated_bytes"] == size["used_bytes"] + size["freelist_bytes"]

    report = inspect(conn, now=1000)
    status = Query(conn).status(now=1000)
    assert (report["allocated_bytes"] == size["allocated_bytes"]
            == status["db_allocated_bytes"])
    assert report["used_bytes"] == size["used_bytes"] == status["db_used_bytes"]
    assert (report["freelist_bytes"] == size["freelist_bytes"]
            == status["db_freelist_bytes"])
    conn.close()


def test_doctor_detects_orphan_cl_05(tmp_path):
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(99,1,1)")
    conn.commit()
    report = inspect(conn, now=1000, deep=True)
    assert not report["ok"]
    assert report["orphans"]["samples"] == 1
    assert report["check"] == "integrity_check"
    conn.close()


def test_doctor_catalog_fields_empty_db_cl_05_dm_16(tmp_path):
    """[CL-05][DM-16] Fresh DB: active catalog is zero, no reap has ever run."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    report = inspect(conn, now=1000)
    assert report["entities_not_gone"] == 0
    assert report["series_not_gone"] == 0
    assert report["dm16"] == {
        "max_entities_persisted": 400,
        "max_series_persisted": 270,
        # No daemon has published the DM-16 figures on a fresh database, and
        # doctor says so rather than substituting the counts DM-16 rejects.
        "entities_persisted": None,
        "series_persisted": None,
        "promotion_limit_per_monitor": 10,
        "promotion_limited_monitors": None,
        "promotion_rejections_total": None,
        "entities_not_gone": 0,
        "series_not_gone": 0,
    }
    assert report["last_reap_ts"] is None
    assert report["last_reap_count"] is None
    assert report["last_reap_age_s"] is None
    assert report["used_bytes"] + report["freelist_bytes"] == report["allocated_bytes"]
    assert report["freelist_pages"] >= 0
    assert 0.0 <= report["freelist_fragment_pct"] <= 1.0
    assert report["last_degradation_ts"] is None
    assert report["last_degradation_age_s"] is None
    assert report["monitor_attribution"] == {
        "monitors": [],
        "monitors_returned": 0,
        "monitors_matched": 0,
        "monitors_truncated": False,
        "limits": {"max_monitors": 64},
    }
    conn.close()


def test_doctor_reports_promotion_runtime_guardrail_cl_05_dm_16(tmp_path):
    """[CL-05][DM-16] Doctor surfaces runtime-only promotion pressure from
    the bounded self namespace rather than attempting to infer it from the
    retained catalog."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    for series_id, metric, value in (
        (7001, "promotion_limited_monitors", 2),
        (7002, "promotion_rejections_total", 17),
    ):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) "
            "VALUES (?,'self','ftmon',?,1)",
            (series_id, metric),
        )
        conn.execute(
            "INSERT INTO samples(series_id,ts,value) VALUES (?,1000,?)",
            (series_id, value),
        )
    conn.commit()
    report = inspect(conn, now=1000)
    assert report["dm16"]["promotion_limit_per_monitor"] == 10
    assert report["dm16"]["promotion_limited_monitors"] == 2
    assert report["dm16"]["promotion_rejections_total"] == 17
    conn.close()


def test_catalog_attribution_reconciles_and_orders_catalog_rows_cl_05_ui_02(tmp_path):
    """[CL-05][UI-02] Attribution includes either catalog and labels presence honestly."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES (?,?,1,1,?)",
        [
            ("alpha", "a1", None), ("alpha", "a2", None), ("alpha", "a3", 10),
            ("beta", "b1", None), ("beta", "b2", 10),
            ("entity-only", "e1", 10),
        ],
    )
    series_rows = []
    sid = 1
    for monitor, entity, count in (
        ("series-only", "missing", 7),
        ("alpha", "a1", 5),
        ("beta", "b1", 5),
    ):
        for metric_no in range(count):
            series_rows.append((sid, monitor, entity, f"m{metric_no}", 0))
            sid += 1
    conn.executemany(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES (?,?,?,?,?)",
        series_rows,
    )
    conn.commit()

    catalog = catalog_report(conn, now=1000)
    report = inspect(conn, now=1000)
    attribution = catalog["monitor_attribution"]
    assert report["monitor_attribution"] == attribution
    assert attribution == {
        "monitors": [
            {"monitor": "series-only", "entities_total": 0, "entities_present": 0,
             "entities_gone": 0, "series_total": 7},
            {"monitor": "alpha", "entities_total": 3, "entities_present": 2,
             "entities_gone": 1, "series_total": 5},
            {"monitor": "beta", "entities_total": 2, "entities_present": 1,
             "entities_gone": 1, "series_total": 5},
            {"monitor": "entity-only", "entities_total": 1, "entities_present": 0,
             "entities_gone": 1, "series_total": 0},
        ],
        "monitors_returned": 4,
        "monitors_matched": 4,
        "monitors_truncated": False,
        "limits": {"max_monitors": 64},
    }
    assert sum(item["entities_total"] for item in attribution["monitors"]) == 6
    assert sum(item["series_total"] for item in attribution["monitors"]) == 17
    conn.close()


def test_catalog_attribution_query_plan_stays_off_observation_tables_cl_05(tmp_path):
    """[CL-05] Per-monitor ownership is a catalog query, not an observation scan."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    plan = conn.execute(
        f"EXPLAIN QUERY PLAN {_MONITOR_ATTRIBUTION_SQL}",
        (_MAX_MONITOR_ATTRIBUTION + 1,),
    ).fetchall()
    detail = " ".join(str(column).lower() for row in plan for column in row)
    assert "entities" in detail
    assert "series" in detail
    for forbidden in ("samples", "rollup5m", "rollup1h", "baselines"):
        assert forbidden not in detail
    conn.close()


def test_catalog_attribution_is_bounded_and_deterministic_at_scale_cl_05(tmp_path):
    """[CL-05] A large monitor catalog returns the heaviest deterministic 64."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    entities = []
    series = []
    sid = 1
    for monitor_no in range(70):
        monitor = f"m{monitor_no:03d}"
        entities.append((monitor, "entity", 1, 1, None))
        for metric_no in range(40):
            series.append((sid, monitor, "entity", f"metric-{metric_no}", 0))
            sid += 1
    conn.executemany(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES (?,?,?,?,?)",
        entities,
    )
    conn.executemany(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES (?,?,?,?,?)",
        series,
    )
    conn.commit()

    attribution = catalog_report(conn, now=1000)["monitor_attribution"]
    assert attribution["monitors_returned"] == 64
    assert attribution["monitors_matched"] == 70
    assert attribution["monitors_truncated"] is True
    assert attribution["limits"] == {"max_monitors": 64}
    assert [item["monitor"] for item in attribution["monitors"]] == [
        f"m{monitor_no:03d}" for monitor_no in range(64)
    ]
    conn.close()


def test_doctor_catalog_splits_presence_from_total_cl_05_dm_16(tmp_path):
    """[CL-05][DM-16] Active counts (alive entities, sampled series) differ from
    the total row counts already reported under `tables` — that split is the
    whole point of the reap-visibility feature (issue #74)."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','alive-1',1,1,NULL)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','alive-2',1,1,NULL)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','gone-1',1,1,500)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','gone-2',1,1,500)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','gone-3',1,1,500)"
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES "
        "(1,'proc','alive-1','cpu',0), (2,'proc','alive-2','cpu',0), "
        "(3,'proc','gone-1','cpu',0), (4,'proc','gone-2','cpu',0)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES (1,10,1.0)")
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES (2,10,1.0)")
    # series 3 and 4 have no samples rows (no current raw data) so they don't
    # count as active even though their series/entities rows still exist.
    conn.commit()

    report = inspect(conn, now=1000)
    assert report["entities_not_gone"] == 2
    assert report["series_not_gone"] == 2
    assert report["tables"]["entities"] == 5
    assert report["tables"]["series"] == 4
    assert report["dm16"]["entities_not_gone"] == 2
    assert report["dm16"]["series_not_gone"] == 2
    conn.close()


def test_doctor_series_not_gone_excludes_gone_entity_with_sample_cl_05_dm_16(tmp_path):
    """[CL-05][DM-16] A series still holding a raw sample doesn't count as
    active once its owning entity is gone: DM-04 keeps the sample around
    until it ages out (or MD-09 reap catches up), so bare sample presence
    alone would double-count catalog pressure that's already on its way
    out, not "concurrently persisted" pressure."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','alive',1,1,NULL)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts) "
        "VALUES ('proc','just-gone',1,1,500)"
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES "
        "(1,'proc','alive','cpu',0), (2,'proc','just-gone','cpu',0)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES (1,10,1.0)")
    # gone but its sample hasn't aged out (or reaped) yet -- must not count.
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES (2,10,1.0)")
    conn.commit()

    report = inspect(conn, now=1000)
    assert report["series_not_gone"] == 1
    assert report["tables"]["series"] == 2
    conn.close()


def test_doctor_surfaces_last_reap_meta_cl_05_dm_16(tmp_path):
    """[CL-05][DM-16] `last_reap_ts`/`last_reap_count` meta rows (written by
    Retention._reap_catalog every pass) surface as value + computed age."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("INSERT INTO meta(key,value) VALUES ('last_reap_ts', '400')")
    conn.execute("INSERT INTO meta(key,value) VALUES ('last_reap_count', '7')")
    conn.commit()

    report = inspect(conn, now=1000)
    assert report["last_reap_ts"] == 400
    assert report["last_reap_count"] == 7
    assert report["last_reap_age_s"] == 600
    conn.close()


def test_doctor_reports_used_and_free_database_pages_cl_05(tmp_path):
    """[CL-05] Used plus freelist bytes equals the database file allocation;
    freed pages are visible as reusable headroom, not a health failure."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("CREATE TABLE pressure(payload BLOB)")
    conn.executemany(
        "INSERT INTO pressure(payload) VALUES (zeroblob(4096))",
        [() for _ in range(256)],
    )
    conn.commit()
    conn.execute("DELETE FROM pressure")
    conn.commit()

    report = inspect(conn, now=1000)

    assert report["freelist_pages"] > 0
    assert report["freelist_bytes"] > 0
    assert report["used_bytes"] < report["allocated_bytes"]
    assert report["used_bytes"] + report["freelist_bytes"] == report["allocated_bytes"]
    assert report["freelist_fragment_pct"] == (
        report["freelist_bytes"] / report["allocated_bytes"]
    )
    assert report["ok"]
    conn.close()


def test_doctor_surfaces_last_degradation_meta_cl_05(tmp_path):
    """[CL-05] Degradation recency is optional and uses the injected now."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("INSERT INTO meta(key,value) VALUES ('last_degradation_ts', '400')")
    conn.commit()

    report = inspect(conn, now=1000)

    assert report["last_degradation_ts"] == 400
    assert report["last_degradation_age_s"] == 600
    conn.close()


def test_backup_uses_sqlite_snapshot_vc_03(tmp_path):
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    conn.execute("INSERT INTO meta(key,value) VALUES('live','wal-data')")
    conn.commit()
    destination = tmp_path / "backup.db"
    backup(conn, destination)
    snap = sqlite3.connect(destination)
    assert snap.execute("SELECT value FROM meta WHERE key='live'").fetchone()[0] == "wal-data"
    assert snap.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    snap.close()
    assert_private(destination, 0o600)
    conn.close()


def test_doctor_reports_redacted_channel_readiness_no_10(tmp_path, monkeypatch, capsys):
    """[NO-10][SE-05] Readiness has stable states and never sends or leaks."""
    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    paths = get_paths()
    paths.ensure()
    paths.config_file.write_text(
        "[notify.desktop]\nenabled=false\n"
        "[notify.ntfy]\nenabled=true\ntopic='host'\n"
        "token_env='ABSENT_PRIVATE_TOKEN'\n"
    )
    conn = connect(paths.db_file)
    migrate(conn)
    conn.close()

    assert main(["doctor"]) == 1
    captured = capsys.readouterr()
    assert "Database: file=" in captured.out and "used=" in captured.out
    assert "Freelist:" in captured.out
    assert "Degradation: never" in captured.out
    assert "Notification desktop: disabled" in captured.out
    assert "Notification ntfy: error (invalid_config)" in captured.out
    assert "Notification webhook: disabled" in captured.out
    assert "External checks: disabled (registry missing)" in captured.out
    assert "ABSENT_PRIVATE_TOKEN" not in captured.out + captured.err


def _outbox_row(conn, *, state="pending", due=None, created=1_000, severity=3,
                channel="file", note_id=1):
    # The owning incident must exist or doctor's orphan check fails the report
    # for an unrelated reason and hides what these tests are actually asserting.
    conn.execute(
        "INSERT OR IGNORE INTO incidents(id,monitor,grp,entity_id,state,severity,"
        "opened_ts) VALUES (?,'m','g',?,'open',?,?)",
        (note_id, f"e{note_id}", severity, created),
    )
    conn.execute(
        "INSERT OR IGNORE INTO notifications(id,incident_id,kind,severity,title,body,"
        "monitor,entity_id,created_ts) VALUES (?,?,'open',?,'t','b','m','e',?)",
        (note_id, note_id, severity, created),
    )
    conn.execute(
        "INSERT INTO notification_deliveries(notification_id,channel,state,"
        "attempt_count,next_attempt_ts) VALUES (?,?,?,0,?)",
        (note_id, channel, state, due),
    )
    conn.commit()


def _meta(conn, **pairs):
    conn.executemany(
        "INSERT INTO meta(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        list(pairs.items()),
    )
    conn.commit()


def _fresh(tmp_path, name="ftmon.db"):
    conn = connect(tmp_path / name)
    migrate(conn)
    return conn


def test_doctor_fails_on_dead_dispatcher_with_no_backlog_pm_12_no_10(tmp_path):
    """[PM-12][NO-10] A dead worker fails doctor even with nothing owed.

    This is issue #98's core complaint: every channel validated, so doctor said
    ready while nothing had been delivered for fourteen hours.
    """
    conn = _fresh(tmp_path)
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="background",
          notify_dispatch_state="dead",
          notify_dispatch_last_error_category="store_corrupt")
    report = inspect(conn, now=1000, daemon_live=True)
    assert not report["ok"]
    assert report["dispatch"]["pending_total"] == 0
    assert "store_corrupt" in report["dispatch"]["problems"][0]
    conn.close()


def test_doctor_fails_on_overdue_claimable_debt_no_10(tmp_path):
    """[NO-10] Debt nobody is draining fails doctor regardless of worker state."""
    conn = _fresh(tmp_path)
    _outbox_row(conn, due=900)
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="background",
          notify_dispatch_state="running", notify_dispatch_heartbeat_ts="1000.0")
    report = inspect(conn, now=1000, daemon_live=True)
    assert not report["ok"]
    assert report["dispatch"]["due_claimable"] == 1
    assert report["dispatch"]["oldest_claimable_due_age_s"] == 100
    assert "overdue" in report["dispatch"]["problems"][0]
    conn.close()


def test_doctor_tolerates_recent_claimable_debt_no_10(tmp_path):
    """[NO-10] Normal in-flight retry backlog is not a failure."""
    conn = _fresh(tmp_path)
    _outbox_row(conn, due=990)
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="background",
          notify_dispatch_state="running", notify_dispatch_heartbeat_ts="1000.0")
    report = inspect(conn, now=1000, daemon_live=True)
    assert report["ok"]
    assert report["dispatch"]["oldest_claimable_due_age_s"] == 10
    conn.close()


def test_doctor_does_not_blame_quiet_held_debt_no_10(tmp_path):
    """[NO-10] Quiet-held rows are durable debt but never overdue.

    Without this split an overnight quiet window looks exactly like a stuck
    outbox, which would train operators to ignore the signal.
    """
    from datetime import UTC

    from ftmon.config import QuietHours

    midnight = 1_700_000_000 - (1_700_000_000 % 86400)
    night = midnight + 23 * 3600
    conn = _fresh(tmp_path)
    _outbox_row(conn, due=night, created=night, severity=2)
    _meta(conn, last_tick_ts=repr(float(night + 3600)),
          notify_dispatch_mode="background", notify_dispatch_state="running",
          notify_dispatch_heartbeat_ts=repr(float(night + 3600)))
    quiet = QuietHours(22 * 60, 8 * 60, tz=UTC)
    report = inspect(conn, now=night + 3600, quiet=quiet, daemon_live=True)
    assert report["ok"]
    assert report["dispatch"]["quiet_held"] == 1
    assert report["dispatch"]["due_claimable"] == 0
    assert report["dispatch"]["pending_total"] == 1
    assert report["dispatch"]["oldest_claimable_due_age_s"] == 0
    # The same rows without a quiet window are ordinary overdue debt.
    assert not inspect(conn, now=night + 3600, daemon_live=True)["ok"]
    conn.close()


def test_doctor_spares_a_stopped_daemon_no_10(tmp_path):
    """[NO-10] Predicates describe a running daemon; a stopped one just reports.

    The project documents stopping the daemon before inspecting the database,
    so failing on debt nobody is draining yet would punish the safe workflow.
    """
    conn = _fresh(tmp_path)
    _outbox_row(conn, due=100)
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="background",
          notify_dispatch_state="stopped")
    report = inspect(conn, now=1000, daemon_live=False)
    assert report["ok"]
    assert report["dispatch"]["due_claimable"] == 1
    assert report["dispatch"]["problems"] == []
    conn.close()


def test_doctor_expects_no_worker_in_synchronous_mode_pm_12(tmp_path):
    """[PM-12] Recorded dispatch mode separates "none expected" from "died"."""
    conn = _fresh(tmp_path)
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="synchronous")
    assert inspect(conn, now=1000, daemon_live=True)["ok"]
    _meta(conn, notify_dispatch_mode="background")
    report = inspect(conn, now=1000, daemon_live=True)
    assert not report["ok"]
    assert "state=unknown" in report["dispatch"]["problems"][0]
    conn.close()


def test_doctor_reports_failures_per_channel_no_10(tmp_path):
    """[NO-10] Terminal failures stay attributable to a channel."""
    conn = _fresh(tmp_path)
    _outbox_row(conn, state="failed", channel="file")
    _outbox_row(conn, state="failed", channel="ntfy")
    _meta(conn, last_tick_ts="1000.0", notify_dispatch_mode="background",
          notify_dispatch_state="running", notify_dispatch_heartbeat_ts="1000.0")
    dispatch = inspect(conn, now=1000, daemon_live=True)["dispatch"]
    assert dispatch["failed"] == 2
    assert dispatch["failed_by_channel"] == {"file": 1, "ntfy": 1}
    conn.close()


def test_self_source_reports_dm05_used_pages_not_file_allocation_dm_05(tmp_path, monkeypatch):
    """[DM-05][RB-02] The budget signal measures used pages; file bytes stay separate."""
    from ftmon.clock import FakeClock
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths as _paths
    from ftmon.store.db import DB_BUDGET_BYTES

    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    paths = _paths()
    paths.ensure()
    paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
    paths.config_file.chmod(0o600)
    core = DaemonCore(paths=paths, clock=FakeClock(wall=1_000, mono=1_000))
    try:
        core._sample_db_pages()
        stats = core.stats
        page_size = core.conn.execute("PRAGMA page_size").fetchone()[0]
        pages = core.conn.execute("PRAGMA page_count").fetchone()[0]
        free = core.conn.execute("PRAGMA freelist_count").fetchone()[0]
        assert stats.db_allocated_bytes == pages * page_size
        assert stats.db_used_bytes == (pages - free) * page_size
        assert stats.db_freelist_bytes == free * page_size
        # Used never exceeds file, and the two differ exactly by the freelist.
        assert (stats.db_used_bytes + stats.db_freelist_bytes
                == stats.db_allocated_bytes)
        # Headroom is signed against DM-05's target, not any alarm threshold.
        assert stats.db_headroom_bytes == DB_BUDGET_BYTES - stats.db_used_bytes
        # D1: db_bytes keeps its historical file-allocation meaning.
        metrics = core.samplers["self"].sample(1_000, 0.0, {}).entities[0].metrics
        assert metrics["db_allocated_bytes"] == stats.db_allocated_bytes
        assert metrics["db_used_bytes"] == stats.db_used_bytes
    finally:
        core.conn.close()


def test_freelist_growth_alone_does_not_consume_budget_dm_05(tmp_path, monkeypatch):
    """[DM-05] Reusable pages are allocated but cost nothing against the budget.

    This is the defect behind the flapping incident: an alarm on file bytes
    counts pages that are immediately reusable, so it can fire while the
    defined budget is healthy.
    """
    from ftmon.clock import FakeClock
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths as _paths

    for name in ("CONFIG", "DATA", "STATE", "RUNTIME"):
        monkeypatch.setenv(f"FTMON_{name}_DIR", str(tmp_path / name.lower()))
    paths = _paths()
    paths.ensure()
    paths.config_file.write_text("[notify.desktop]\nenabled=false\n")
    paths.config_file.chmod(0o600)
    core = DaemonCore(paths=paths, clock=FakeClock(wall=1_000, mono=1_000))
    try:
        core.conn.execute("CREATE TABLE ballast(x TEXT)")
        core.conn.executemany(
            "INSERT INTO ballast(x) VALUES (?)", [("y" * 400,) for _ in range(4_000)]
        )
        core.conn.commit()
        core._sample_db_pages()
        grown_used = core.stats.db_used_bytes
        core.conn.execute("DROP TABLE ballast")
        core.conn.commit()
        core._sample_db_pages()
        assert core.stats.db_freelist_bytes > 0, "expected reclaimable pages"
        # Allocation stays high while used bytes fall — the whole point.
        assert core.stats.db_used_bytes < grown_used
        assert core.stats.db_allocated_bytes > core.stats.db_used_bytes
        assert core.stats.db_headroom_bytes > 0
    finally:
        core.conn.close()


def test_run_monitor_publishes_persisted_gauges_dm_16(tmp_path, monkeypatch):
    """[DM-16] The production path publishes selection-based pressure.

    Drives `run_monitor` with a real sampler and a real TickWriter rather than
    calling `_select_persisted` and assigning the gauge by hand: the defect
    being guarded is that the *published* figure could drift back to a
    presence count, and only the production assignment inside `_persist`
    proves it does not.
    """
    from ftmon.definitions import loader
    from ftmon.engine.pipeline import Pipeline
    from ftmon.engine.rings import RingStore
    from ftmon.model import EntitySample, Snapshot
    from ftmon.store.writer import TickWriter

    class TwelveProcesses:
        decl = SOURCE_DECLS["process"]

        def sample(self, now, deadline_mono, options):
            return Snapshot(source="process", ts=now, entities=tuple(
                EntitySample(
                    entity_id=f"p{i}", attrs={"name": f"p{i}"},
                    metrics={"cpu_pct": float(i), "rss_bytes": float(i)},
                )
                for i in range(12)
            ))

    mdef = loader.load_text(
        'schema = 1\n'
        '[monitor]\n'
        'name = "proc"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "process"\n'
        '[source_options]\n'
        'top_n = 5\n'
        '[[rule]]\n'
        'id = "r"\ngroup = "g"\nwhen = \'cpu_pct > 99999\'\n'
        'severity = "warning"\nconfirm_cycles = 1\nmessage = "m"\n'
    )

    conn = connect(tmp_path / "p.db")
    migrate(conn)
    writer = TickWriter(conn)
    pipe = Pipeline(
        samplers={"process": TwelveProcesses()}, rings=RingStore(),
        counter=lambda _n: None,
    )
    pipe.run_monitor(mdef, 1_000.0, 0.0, writer, {})
    writer.commit_tick()

    # All twelve are sampled and marked seen -- which is exactly what a
    # presence-derived count would report. cpu_pct and rss_bytes rank
    # identically here, so top_n=5 selects five of them.
    assert len(pipe._state["proc"].seen) == 12
    persisted = pipe.persisted_entities(["proc"])
    assert persisted == 5

    # The gauge matches what actually reached the database, which is the
    # property that matters -- a presence count could not satisfy this.
    written = conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM series WHERE monitor='proc'"
    ).fetchone()[0]
    assert persisted == written
    series_rows = conn.execute(
        "SELECT COUNT(*) FROM series WHERE monitor='proc'"
    ).fetchone()[0]
    assert pipe.persisted_series(["proc"]) == series_rows
    # An unloaded monitor stops contributing pressure immediately (MD-09).
    assert pipe.persisted_entities([]) == 0
    conn.close()


def test_db_bytes_and_allocated_diverge_under_wal_issue_104(tmp_path):
    """[DM-05] db_bytes is the file; allocated is SQLite's logical size.

    They are not interchangeable in WAL mode, which FTMON always uses: pages
    committed since the last checkpoint live in the -wal file, so the main
    file lags allocation. Only used + freelist == allocated holds. Substituting
    db_bytes into the budget arithmetic is the mistake this pins against.
    """
    conn = connect(tmp_path / "wal.db")
    migrate(conn)
    conn.execute("CREATE TABLE ballast(payload BLOB)")
    conn.executemany(
        "INSERT INTO ballast(payload) VALUES (zeroblob(4096))",
        [() for _ in range(200)],
    )
    conn.commit()

    size = db_size_report(conn)
    assert size["used_bytes"] + size["freelist_bytes"] == size["allocated_bytes"]
    # The divergence is real, not theoretical: the main file has not yet
    # received these pages, so it is smaller than what SQLite reports.
    assert size["file_bytes"] < size["allocated_bytes"]
    # A checkpoint converges them, which is why the gap is transient rather
    # than unbounded under FTMON's default auto-checkpointing.
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    assert db_size_report(conn)["file_bytes"] == size["allocated_bytes"]
    conn.close()


def test_stale_db_bytes_rule_is_warned_but_does_not_fail_doctor_dm_05(tmp_path, monkeypatch):
    """[DM-05][CL-05] An un-upgraded budget rule is surfaced, not silently kept.

    FS-02 forbids overwriting user config, so a shipped definition fix never
    reaches an existing install by itself. Without this warning the operator's
    alarm keeps measuring file allocation while DM-05 bounds used pages — the
    exact silence this issue exists to remove. It must not fail doctor, though:
    doctor's non-zero is for an installation that is broken, and every upgraded
    host would otherwise start exiting 1 until someone edited a rule.
    """
    from ftmon.definitions import loader

    monkeypatch.setenv("FTMON_CONFIG_DIR", str(tmp_path / "config"))
    monitors = tmp_path / "config" / "monitors"
    monitors.mkdir(parents=True)
    (monitors / "stale.toml").write_text(
        'schema = 1\n'
        '[monitor]\n'
        'name = "stale"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "self"\n'
        '[parameters]\n'
        'db_budget_mb = { value = 200, doc = "d" }\n'
        '[[rule]]\n'
        'id = "db-budget"\ngroup = "db-budget"\n'
        "when = 'db_bytes > db_budget_mb * MB'\n"
        'severity = "warning"\nconfirm_cycles = 1\nmessage = "over"\n'
    )
    defs, errors = loader.load_dir(monitors)
    assert errors == []
    warnings = loader.stale_metric_warnings(defs)
    assert len(warnings) == 1
    assert "stale/db-budget" in warnings[0]
    assert "db_used_bytes" in warnings[0]

    # The corrected rule is silent, and db_used_bytes must not itself match the
    # db_bytes word-boundary probe.
    (monitors / "stale.toml").write_text(
        (monitors / "stale.toml").read_text().replace(
            "db_bytes > db_budget_mb", "db_used_bytes > db_budget_mb"
        )
    )
    fixed, errors = loader.load_dir(monitors)
    assert errors == []
    assert loader.stale_metric_warnings(fixed) == []


def _self_rule(when: str) -> str:
    return (
        'schema = 1\n'
        '[monitor]\n'
        'name = "selfbudget"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "self"\n'
        '[parameters]\n'
        'db_budget_mb = { value = 200, doc = "d" }\n'
        '[[rule]]\n'
        'id = "r"\ngroup = "g"\n'
        f"when = '{when}'\n"
        'severity = "warning"\nconfirm_cycles = 1\nmessage = "m"\n'
    )


def test_stale_metric_warning_matches_only_the_budget_shape_dm_05():
    """[DM-05] Warn on the legacy budget comparison, not any db_bytes mention.

    A rule that derives the right quantity the long way is doing nothing
    wrong, and telling it to substitute db_used_bytes would change what it
    computes. Matching the bare name would have given that advice.
    """
    from ftmon.definitions import loader

    def warns(when: str) -> bool:
        return bool(loader.stale_metric_warnings([loader.load_text(_self_rule(when))]))

    # The shape this exists to catch.
    assert warns("db_bytes > db_budget_mb * MB")
    assert warns("db_bytes > 200000000")
    # Deriving used pages by hand is correct; substituting would break it.
    assert not warns("db_bytes - db_freelist_bytes > db_budget_mb * MB")
    # A fragmentation ratio is a legitimate use of the raw quantity.
    assert not warns("db_bytes / db_allocated_bytes > 0.5")
    # Already correct.
    assert not warns("db_used_bytes > db_budget_mb * MB")

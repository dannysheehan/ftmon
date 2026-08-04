"""M6 database diagnostics tests [CL-05][VC-03]."""

import sqlite3

from ftmon.cli import main
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.doctor import backup, inspect
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
    assert report["entities_alive"] == 0
    assert report["series_active"] == 0
    assert report["dm16"] == {
        "max_entities_active": 400,
        "entities_active": 0,
        "max_series_active": 270,
        "series_active": 0,
    }
    assert report["last_reap_ts"] is None
    assert report["last_reap_count"] is None
    assert report["last_reap_age_s"] is None
    conn.close()


def test_doctor_catalog_splits_active_from_total_cl_05_dm_16(tmp_path):
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
    assert report["entities_alive"] == 2
    assert report["series_active"] == 2
    assert report["tables"]["entities"] == 5
    assert report["tables"]["series"] == 4
    assert report["dm16"]["entities_active"] == 2
    assert report["dm16"]["series_active"] == 2
    conn.close()


def test_doctor_series_active_excludes_gone_entity_with_sample_cl_05_dm_16(tmp_path):
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
    assert report["series_active"] == 1
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
    assert "Notification desktop: disabled" in captured.out
    assert "Notification ntfy: error (invalid_config)" in captured.out
    assert "Notification webhook: disabled" in captured.out
    assert "External checks: disabled (registry missing)" in captured.out
    assert "ABSENT_PRIVATE_TOKEN" not in captured.out + captured.err

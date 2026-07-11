"""M6 database diagnostics tests [CL-05][VC-03]."""

import sqlite3

from ftmon.store.db import connect, migrate
from ftmon.store.doctor import backup, inspect


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
    assert destination.stat().st_mode & 0o777 == 0o600
    conn.close()

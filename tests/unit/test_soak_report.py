"""[TS-17] Soak evidence must measure each budget as its requirement states it.

The generator is the instrument the release gate reads, so a mis-stated
quantity is indistinguishable from a daemon that passed or failed.
"""

from __future__ import annotations

from tools import soak_report

from ftmon.store.db import connect, migrate

_NOW = 1_700_000_000.0
_MIB = 1024 * 1024


def _series(conn, metric: str) -> int:
    cur = conn.execute(
        "INSERT INTO series(monitor, entity_id, metric, durable) VALUES('self','ftmon',?,1)",
        (metric,),
    )
    return int(cur.lastrowid)


def _samples(conn, metric: str, points: list[tuple[float, float]]) -> None:
    sid = _series(conn, metric)
    conn.executemany(
        "INSERT INTO samples(series_id, ts, value) VALUES(?,?,?)",
        [(sid, round(ts), value) for ts, value in points],
    )


def _rollup(conn, table: str, metric: str, buckets: list[tuple[float, float, int]]) -> None:
    sid = _series(conn, metric)
    conn.executemany(
        f"INSERT INTO {table}(series_id, bucket, avg, min, max, last, cnt) "  # noqa: S608
        "VALUES(?,?,?,?,?,?,?)",
        [(sid, round(ts), avg, avg, avg, avg, cnt) for ts, avg, cnt in buckets],
    )


def _cpu_row(report: str) -> str:
    return next(line for line in report.splitlines() if line.startswith("| cpu_pct"))


def test_cpu_percentiles_are_ten_minute_means_not_per_tick_samples_rb_01(tmp_path):
    """[RB-01] CPU is budgeted "averaged over 10 m", so one tick's spike is not a max.

    Reporting the per-sample maximum overstated the breach by more than 3x on
    real soak data and made an in-budget daemon look out of budget.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    # Forty minutes of a quiet daemon with a single 30 % tick in one window.
    # Anchored to a bucket boundary so the spike's window holds exactly ten ticks.
    base = (_NOW // 600) * 600
    points = [(base - 2400 + 60 * i, 0.5) for i in range(40)]
    points[5] = (points[5][0], 30.0)
    _samples(conn, "cpu_pct", points)
    conn.commit()
    conn.close()

    row = _cpu_row(soak_report.build_report(db, now=_NOW))

    # (9 * 0.5 + 30) / 10 == 3.45: the window the spike lands in, not the spike.
    assert "3.45 %" in row
    assert "30.00 %" not in row
    assert "10 m avg" in row


def test_storage_budget_is_used_pages_and_file_size_is_marked_non_normative_dm_05(tmp_path):
    """[DM-05][RB-02] The DM-05 target is used pages; the physical file bears no budget.

    WAL and freelist hold the file at the ceiling long after retention has
    released the space, so judging the file reports a pin that is not real.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    ts = [_NOW - 600 + 60 * i for i in range(10)]
    _samples(conn, "db_used_bytes", [(t, 100 * _MIB) for t in ts])
    _samples(conn, "db_bytes", [(t, 200 * _MIB) for t in ts])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)
    used = next(line for line in report.splitlines() if line.startswith("| db_used_mb"))
    file_row = next(line for line in report.splitlines() if line.startswith("| db_file_mb"))

    assert "100.0 MB" in used and "200 MB" in used  # value against the DM-05 target
    assert "non-normative" in file_row and "200.0 MB" in file_row
    assert "—" in file_row.rsplit("|", 2)[1]  # the file row carries no budget
    assert "carries no budget" in report


def test_five_minute_rollup_means_are_count_weighted_dm_04(tmp_path):
    """[DM-04][RB-01] A 10-minute mean over rollups must weight by sample count.

    Averaging bucket averages treats a one-sample bucket as equal to a full
    one, which silently reshapes the distribution the gate is read from.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    base = (_NOW - 600) // 600 * 600
    _rollup(conn, "rollup5m", "cpu_pct", [(base, 1.0, 1), (base + 300, 2.0, 9)])
    conn.commit()
    conn.close()

    row = _cpu_row(soak_report.build_report(db, now=_NOW))

    # Weighted: (1*1 + 2*9) / 10 == 1.90. Unweighted would read 1.50.
    assert "1.90 %" in row
    assert "1.50 %" not in row


def test_hourly_tier_is_excluded_from_the_verdict_and_reported_as_trend_rb_01(tmp_path):
    """[RB-01][DM-04] An hourly mean cannot express a 10-minute average.

    A 30-day window outlives raw retention, so the old tail survives only as
    hourly rollups; averaging it into the verdict would claim a resolution the
    stored data does not have.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _rollup(conn, "rollup1h", "cpu_pct",
            [(_NOW - 86400 + 3600 * i, 4.0, 60) for i in range(12)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "4.00 %" not in _cpu_row(report)  # never promoted into the table
    assert "too coarse" in report
    assert "For trend only" in report


def test_missing_used_page_metric_is_stated_not_silently_blank_dm_05(tmp_path):
    """[DM-05] A database predating db_used_bytes must say so, not report nothing.

    An empty storage row reads as "no growth" when it actually means the build
    never recorded the quantity the budget is defined in.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "db_bytes", [(_NOW - 60, 200 * _MIB)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "absent from this database" in report
    assert "Storage cannot be judged" in report


def test_since_scopes_the_window_to_the_build_under_test_ts_17(tmp_path):
    """[TS-17] An in-place upgrade must not report the replaced build's history.

    Both legs were upgraded on their carried databases, so a rolling 30-day
    window blends two builds and the older, longer one wins the percentiles.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    upgrade = _NOW - 3600
    old = [(upgrade - 86400 + 60 * i, 9.0) for i in range(120)]
    new = [(upgrade + 60 * i, 0.4) for i in range(60)]
    _samples(conn, "cpu_pct", old + new)
    conn.commit()
    conn.close()

    unscoped = _cpu_row(soak_report.build_report(db, now=_NOW))
    scoped = _cpu_row(soak_report.build_report(db, now=_NOW, since=upgrade))

    assert "9.00 %" in unscoped  # the replaced build dominates a rolling window
    assert "9.00 %" not in scoped
    assert "0.40 %" in scoped


def test_report_states_the_window_it_used_ts_17(tmp_path):
    """[TS-17] The window must be on the page, not inferred from odd percentiles.

    A capture scoped to the wrong build is only detectable by a reader who
    already suspects it, unless the report says which window it measured.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "cpu_pct", [(_NOW - 600 + 60 * i, 0.5) for i in range(10)])
    conn.commit()
    conn.close()

    rolling = soak_report.build_report(db, now=_NOW)
    scoped = soak_report.build_report(db, now=_NOW, since=_NOW - 7200)

    assert "rolling 30 d" in rolling
    assert "scoped to the build under test" in scoped
    assert "2.0 h," in scoped  # the window it actually measured


def test_since_accepts_iso_8601_and_epoch_ts_17():
    """[TS-17] Manifests record ISO-8601; operators reach for epoch seconds."""
    assert soak_report.parse_since("1700000000") == 1_700_000_000.0
    # An explicit offset must be honoured rather than reinterpreted as local.
    assert soak_report.parse_since("2026-09-04T13:49:15+10:00") == 1788493755.0
    # Z is what the host manifests actually carry, so it is the format that
    # must not regress -- the same instant as the offset form above.
    assert soak_report.parse_since("2026-09-04T03:49:15Z") == 1788493755.0


def test_window_label_survives_a_platform_that_rejects_pre_epoch_time_ts_17(monkeypatch):
    """[TS-17] A report must not fail over the label on a timestamp it can measure.

    Windows' localtime() raises on pre-epoch values, which the default 30-day
    window reaches whenever `now` is small — a fixture at now=1000 starts the
    window at -2,591,000. Caught by Windows CI, reproduced here by refusing the
    same way on any platform.
    """
    def _refuse(_value):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(soak_report.time, "localtime", _refuse)

    assert soak_report._stamp(-2_591_000) == "1969-12-02 00:16:40 UTC"


def test_report_builds_against_a_pre_epoch_window_ts_17(tmp_path):
    """[TS-17] The small-`now` fixture path must produce a report, not an OSError."""
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "cpu_pct", [(900, 0.2)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=1000.0)

    assert "# FTMON soak evidence report" in report
    assert "- Window:" in report


def test_superseded_clear_is_explained_not_counted_against_the_gate_rb_02(tmp_path):
    """[RB-02][TS-17] Changing definitions supersedes an incident; that is explained.

    Splitting the combined `budget` group into cpu/rss/db groups, which RB-02
    requires, superseded the old group's incident on both soak legs — and made
    each report claim one unexplained self incident, which TS-17 forbids.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    conn.executemany(
        "INSERT INTO incidents(monitor, grp, entity_id, state, severity, owning_rule, "
        "opened_ts, cleared_ts, clear_reason) VALUES('self',?,'ftmon','cleared',2,?,?,?,?)",
        [
            ("budget", "rss-budget", _NOW - 7200, _NOW - 3600, "superseded"),
            ("cpu-budget", "cpu-budget", _NOW - 1800, _NOW - 900, "recovered"),
        ],
    )
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "- Unexplained self incidents: 0" in report


def test_terminal_delivery_failures_are_not_reported_as_backlog_ts_17(tmp_path):
    """[TS-17][NO-07] "Outbox draining" is about retriable debt, not dead rows.

    A permanently failed delivery never drains and nothing prunes the table, so
    counting it as pending leaves the criterion unsatisfiable forever while
    hiding a defect inside a number that looks like a stuck queue.
    """
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    conn.execute(
        "INSERT INTO notifications(id, incident_id, created_ts, severity, kind, title, "
        "body, monitor, entity_id) VALUES(1, 1, ?, 2, 'open', 't', 'b', 'self', 'ftmon')",
        (_NOW - 600,),
    )
    conn.executemany(
        "INSERT INTO notification_deliveries(notification_id, channel, state, "
        "attempt_count, next_attempt_ts, delivered_ts, last_error) VALUES(1,?,?,?,?,?,?)",
        [
            ("desktop", "failed", 1, None, None, "desktop_exit (1)"),
            ("ntfy", "pending", 1, _NOW + 30, None, "timeout"),
        ],
    )
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "- Pending deliveries (retriable backlog): 1" in report
    assert "Terminally failed: desktop x1 (desktop_exit (1))" in report
    assert "defect signal, not backlog" in report


def _write_self_def(tmp_path, value):
    monitors = tmp_path / "monitors"
    monitors.mkdir(parents=True, exist_ok=True)
    (monitors / "self.toml").write_text(
        "[parameters]\n"
        f'cpu_budget_pct = {{ value = {value}, doc = "d" }}\n',
        encoding="utf-8",
    )
    return monitors


def test_profile_cpu_budget_reads_the_deployed_calibration_rb_01(tmp_path, monkeypatch):
    """[RB-01][DM-16] The profile figure lives in the definition, not the database."""
    monitors = _write_self_def(tmp_path, 4.0)
    monkeypatch.setattr(soak_report, "get_paths",
                        lambda: type("P", (), {"monitors_dir": monitors})())

    assert soak_report.profile_cpu_budget() == 4.0


def test_a_calibration_above_the_reference_is_not_reported_as_compliance_rb_01(
    tmp_path, monkeypatch
):
    """[RB-01] RB-01 v0.66: a looser threshold is an operational value, not a pass.

    The Windows profile's 30 % records measured sampler overhead that no
    process-count scaling explains; reporting it as the budget would launder a
    tracked defect into compliance.
    """
    monitors = _write_self_def(tmp_path, 30)
    monkeypatch.setattr(soak_report, "get_paths",
                        lambda: type("P", (), {"monitors_dir": monitors})())
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "cpu_pct", [(_NOW - 600 + 60 * i, 0.5) for i in range(10)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "RB-01 reference: 1 % of one core (server profile)" in report
    assert "calibrated **30 %**" in report
    assert "not compliance" in report


def test_a_profile_inside_the_reference_is_reported_plainly_rb_01(tmp_path, monkeypatch):
    """[RB-01] A leg alarming at or below the reference needs no caveat."""
    monitors = _write_self_def(tmp_path, 1.0)
    monkeypatch.setattr(soak_report, "get_paths",
                        lambda: type("P", (), {"monitors_dir": monitors})())
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "cpu_pct", [(_NOW - 600 + 60 * i, 0.5) for i in range(10)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "at or inside the reference" in report
    assert "not compliance" not in report


def test_an_unreadable_calibration_is_stated_not_omitted_rb_01(tmp_path, monkeypatch):
    """[RB-01] Evidence must never rest on an unstated calibration.

    Silence would read as "measured against the reference", which is precisely
    the assumption RB-01 v0.66 forbids a pass from resting on.
    """
    monkeypatch.setattr(soak_report, "get_paths",
                        lambda: type("P", (), {"monitors_dir": tmp_path / "absent"})())
    db = tmp_path / "ftmon.db"
    conn = connect(db)
    migrate(conn)
    _samples(conn, "cpu_pct", [(_NOW - 60, 0.5)])
    conn.commit()
    conn.close()

    report = soak_report.build_report(db, now=_NOW)

    assert "could not be read" in report
    assert "unstated" in report

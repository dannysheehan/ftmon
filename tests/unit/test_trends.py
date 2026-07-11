"""Historical rollup and honest disk forecast contracts (M7)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ftmon.definitions.loader import load_file
from ftmon.store.db import connect, migrate
from ftmon.store.query import Query


def _series(conn, sid, metric):
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(?,'disk','/data',?,1)", (sid, metric)
    )


def test_rollup_statistic_and_envelope_precede_downsampling_dm_17(tmp_path):
    """[DM-17] Stored last/min/max survive tier selection and share timestamps."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    _series(conn, 1, "used_pct")
    conn.executemany(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,?,?,?,?,?,5)",
        [(1000, 50.0, 45.0, 60.0, 59.0), (1300, 55.0, 50.0, 70.0, 69.0)],
    )
    result = Query(conn).series(
        "disk", "used_pct", now=200000, start=0, end=100000,
        entity_id="/data", statistic="last", include_envelope=True,
    )[0]
    assert [p.value for p in result.points] == [59.0, 69.0]
    assert [p.value for p in result.lower] == [45.0, 50.0]
    assert [p.value for p in result.upper] == [60.0, 70.0]
    assert [p.ts for p in result.points] == [p.ts for p in result.lower]
    conn.close()


def test_disk_projection_qualifies_growth_and_gaps_cleanup_ca_09_ui_11(tmp_path):
    """[CA-09][UI-11] Positive confident growth projects; cleanup becomes a gap."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    metrics = ["used_pct", "used_bytes", "free_bytes", "fill_rate_bph", "filling"]
    for sid, metric in enumerate(metrics, 1):
        _series(conn, sid, metric)
    values = {
        1: [(100, 50), (200, 55)],
        2: [(100, 500), (200, 550)],
        3: [(100, 500), (200, 450)],
        4: [(100, -10), (200, 100)],
        5: [(100, 0.95), (200, 0.9)],
    }
    for sid, points in values.items():
        conn.executemany(
            "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
            [(sid, ts, value) for ts, value in points],
        )
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(1,'disk','filling','/data','open',2,'filling',150,150,1,1)"
    )
    trend = Query(conn).disk_trend(
        "/data", now=200, start=0, end=200, filling_frac=0.85
    )
    assert trend["projection"] == [[100, None], [200, 4.5]]
    assert trend["summary"]["projected_full_ts"] == 16400
    assert trend["summary"]["fill_rate_bph"] == 100
    assert trend["incidents"][0]["id"] == 1
    conn.close()


def test_disk_projection_suppresses_low_confidence_ts_09(tmp_path):
    """[TS-09] A mathematically positive rate is not enough without confidence."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    for sid, metric in enumerate(("free_bytes", "fill_rate_bph", "filling"), 1):
        _series(conn, sid, metric)
        value = {"free_bytes": 1000, "fill_rate_bph": 100, "filling": 0.4}[metric]
        conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)", (sid, 100, value))
    trend = Query(conn).disk_trend("/data", now=100, start=0, end=100)
    assert trend["projection"] == [[100, None]]
    assert trend["summary"]["projected_full_ts"] is None
    assert "no reliable projection" in trend["summary"]["projection_reason"]
    conn.close()


def test_generic_leak_trend_has_no_projection_and_filters_incidents_ca_10_ts_10(tmp_path):
    """[CA-10][TS-10] Leak exposes value/rate/confidence but invents no ceiling."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    entity = "firefox:7:1"
    metrics = ("rss_mb", "rss_slope_mbph", "rss_growth_confidence")
    for sid, metric in enumerate(metrics, 1):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) "
            "VALUES(?,'leak',?,?,0)", (sid, entity, metric)
        )
    for sid, values in enumerate(((100, 200), (32, 64), (0.7, 0.9)), 1):
        conn.executemany(
            "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
            [(sid, 100, values[0]), (sid, 200, values[1])],
        )
    for iid, group in ((1, "leak"), (2, "unrelated")):
        conn.execute(
            "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
            "opened_ts,last_change_ts,notify_count,occurrences) "
            "VALUES(?,'leak',?,?,'open',2,'leak-warn',150,150,1,1)",
            (iid, group, entity),
        )
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/leak.toml"
    mdef = load_file(builtin)
    trend = Query(conn).trend(
        "leak", entity, mdef.trends[0], now=200, start=0, end=200,
        parameters=mdef.parameters,
    )
    assert trend["panels"]["value"]["metric"] == "rss_mb"
    assert trend["panels"]["confidence"]["threshold"] == 0.8
    assert trend["panels"]["projection"] is None
    assert trend["summary"]["projection_reason"] is None
    assert [incident["id"] for incident in trend["incidents"]] == [1]
    assert [x["value"] for x in trend["panels"]["rate"]["thresholds"]] == [32, 128]
    conn.close()


def test_absent_optional_panels_are_null_not_empty_ca_10(tmp_path):
    """[CA-10] Null means not meaningful; empty points remain reserved for no data."""
    conn = connect(tmp_path / "ftmon.db")
    migrate(conn)
    _series(conn, 1, "used_pct")
    _series(conn, 2, "used_bytes")
    profile = SimpleNamespace(
        id="simple", kind="growth", title="Simple growth",
        value_metric="used_pct", value_unit="percent",
        rate_metric="used_bytes", rate_unit="bytes/hour",
        confidence_metric=None, confidence_threshold_param=None,
        remaining_metric=None, value_threshold_params=(),
        rate_threshold_params=(), incident_group=None,
    )
    trend = Query(conn).trend(
        "disk", "/data", profile, now=100, start=0, end=100, parameters={}
    )
    assert trend["panels"]["value"]["points"] == []
    assert trend["panels"]["confidence"] is None
    assert trend["panels"]["projection"] is None
    conn.close()

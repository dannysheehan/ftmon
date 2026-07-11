"""HTTP-level coverage for the optional M5 dashboard (TS-07)."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ftmon.clock import FakeClock
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.web.app import create_app


def _client(tmp_path: Path):
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "config"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    conn = connect(paths.db_file)
    migrate(conn)
    conn.execute("INSERT INTO meta(key,value) VALUES ('last_tick_ts','990')")
    conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                 "opened_ts,last_change_ts,notify_count,occurrences) "
                 "VALUES (1,'hog','hog','evil<script>','open',3,'error',900,900,1,1)")
    conn.commit()
    conn.close()
    return TestClient(create_app(paths, FakeClock(wall=1000, mono=1000))), paths


def test_ui_pages_security_and_escaping_ts_07_ui_02_ui_08_se_02(tmp_path):
    client, _paths = _client(tmp_path)
    headers = {"host": "localhost:8420"}
    for url in ("/", "/incidents", "/incidents/1", "/metrics", "/events",
                "/monitors", "/self"):
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert "access-control-allow-origin" not in response.headers
    page = client.get("/incidents", headers=headers).text
    assert "evil&lt;script&gt;" in page
    assert "▲ error" in page
    assert 'data-refresh-ms="5000"' in page
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400


def test_ui_ack_requires_origin_and_reuses_small_writes_ui_03_ui_08(tmp_path):
    client, paths = _client(tmp_path)
    host = {"host": "localhost:8420"}
    assert client.post("/incidents/1/ack", headers=host).status_code == 403
    response = client.post("/incidents/1/ack", data={"note": "seen"},
                           headers={**host, "origin": "http://localhost:8420"},
                           follow_redirects=False)
    assert response.status_code == 303
    conn = connect(paths.db_file, readonly=True)
    assert conn.execute("SELECT state,ack_by FROM incidents WHERE id=1").fetchone()[:] == (
        "acked", "web")
    conn.close()


def test_metrics_chart_has_text_alternative_ui_05_ui_09(tmp_path):
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'load','host','load1',1)"
    )
    conn.executemany("INSERT INTO samples(series_id,ts,value) VALUES(1,?,?)",
                     [(900, 1.0), (950, 2.0), (1000, 3.0)])
    conn.commit()
    conn.close()
    page = client.get("/metrics?monitor=load&entity=host&metric=load1&hours=1",
                      headers={"host": "127.0.0.1:8420"})
    assert page.status_code == 200
    assert "Current 3" in page.text and "Trend rising" in page.text
    assert "role=\"img\"" in page.text
    assert 'data-metric-chart' in page.text


def test_metrics_explorer_uses_cascading_catalog_selectors_ui_02(tmp_path):
    """[UI-02] Monitor/entity/metric choices come from persisted history."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    rows = [
        (1, "disk", "/home", "used_pct", 70.0),
        (2, "disk", "/home", "free_bytes", 1000.0),
        (3, "leak", "firefox:7:1", "rss_mb", 512.0),
    ]
    for sid, monitor, entity, metric, value in rows:
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES(?,?,?,?,1)",
            (sid, monitor, entity, metric),
        )
        conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
                     (sid, 1000, value))
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    page = client.get(
        "/metrics?monitor=disk&entity=/home&metric=used_pct&range=6h&statistic=last",
        headers=headers,
    )
    assert page.status_code == 200
    assert '<select name="monitor"' in page.text
    assert '<select name="entity"' in page.text
    assert '<select name="metric"' in page.text
    assert '<select name="range"' in page.text
    assert '<select name="statistic"' in page.text
    assert ">disk</option>" in page.text and ">leak</option>" in page.text
    assert ">used_pct</option>" in page.text and ">free_bytes</option>" in page.text
    assert "rss_mb" not in page.text  # another monitor's metric is not offered
    assert "using last" in page.text


def test_series_api_uplot_contract_envelope_gaps_incidents_and_trend_ts_11(tmp_path):
    """[TS-11][UI-13] Metrics shares envelopes, gaps, markers, units and links."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    (paths.monitors_dir / "disk.toml").write_text(builtin.read_text())
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'disk','/home','used_pct',1)"
    )
    conn.executemany(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,?,?,?,?,?,5)",
        [(-300, 50, 45, 55, 52), (0, 55, 50, 60, 58),
         (600, 65, 60, 70, 68), (900, 70, 65, 75, 72)],
    )
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(3,'disk','space','/home','open',2,'space-warn',200,200,1,1)"
    )
    conn.commit()
    conn.close()
    response = client.get(
        "/api/series?monitor=disk&entity=/home&metric=used_pct&"
        "range=7d&statistic=last",
        headers={"host": "localhost:8420"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["unit"] == "percent" and data["statistic"] == "last"
    assert data["resolution"] == "5m"
    assert [point[1] for point in data["panel"]["lower"] if point[1] is not None] == [
        45, 50, 60, 65
    ]
    assert any(point[1] is None for point in data["panel"]["points"])
    assert data["incidents"][0]["id"] == 3
    assert data["matching_trends"][0]["id"] == "space-growth"
    assert "panels" not in data  # Metrics never fabricates interpreted panels


@pytest.mark.parametrize(
    ("statistic", "expected"),
    [("avg", 20.0), ("min", 10.0), ("max", 40.0), ("last", 30.0)],
)
def test_series_api_all_statistics_and_unknown_unit_fallback_ts_11(
    tmp_path, statistic, expected
):
    """[TS-11] API selects every rollup column and never guesses unknown units."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'custom','entity','mystery',1)"
    )
    conn.execute(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,0,20,10,40,30,4)"
    )
    conn.commit()
    conn.close()
    response = client.get(
        f"/api/series?monitor=custom&entity=entity&metric=mystery&"
        f"range=7d&statistic={statistic}",
        headers={"host": "localhost:8420"},
    )
    assert response.status_code == 200
    assert response.json()["panel"]["points"][-1][1] == expected
    assert response.json()["unit"] == "value"


def test_series_api_enforces_display_point_cap_ts_11(tmp_path):
    """[TS-11] Browser payload stays bounded even when raw history is denser."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'custom','entity','dense',1)"
    )
    conn.executemany(
        "INSERT INTO samples(series_id,ts,value) VALUES(1,?,?)",
        [(ts, float(ts)) for ts in range(-1100, 1001)],
    )
    conn.commit()
    conn.close()
    response = client.get(
        "/api/series?monitor=custom&entity=entity&metric=dense&range=6h",
        headers={"host": "localhost:8420"},
    )
    assert response.status_code == 200
    real_points = [p for p in response.json()["panel"]["points"] if p[1] is not None]
    assert len(real_points) <= 2000


def test_disk_trend_api_and_accessible_page_ui_10_ui_11_ts_09(tmp_path):
    """[UI-10][UI-11][TS-09] Shareable disk state exposes panels and honest text."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    (paths.monitors_dir / "disk.toml").write_text(builtin.read_text())
    conn = connect(paths.db_file)
    metrics = ["used_pct", "used_bytes", "free_bytes", "fill_rate_bph", "filling"]
    for sid, metric in enumerate(metrics, 1):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) "
            "VALUES(?,'disk','/data<script>',?,1)", (sid, metric)
        )
    vals = {1: 72.0, 2: 720.0, 3: 280.0, 4: 10.0, 5: 0.9}
    for sid, value in vals.items():
        conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
                     (sid, 1000, value))
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('disk','/data<script>',1,1000,'{}')"
    )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    api = client.get(
        "/api/disk-trend?entity=/data%3Cscript%3E&range=6h", headers=headers
    )
    assert api.status_code == 200
    payload = api.json()
    assert payload["units"]["rate"] == "bytes/hour"
    assert payload["thresholds"]["space_warn_pct"] == 92
    page = client.get(
        "/disks?entity=/data%3Cscript%3E&range=6h", headers=headers
    )
    assert page.status_code == 200
    assert "/data&lt;script&gt;" in page.text
    assert "no reliable projection" not in page.text
    assert 'data-panel="value"' in page.text
    assert "vendor/uPlot.iife.min.js" in page.text

    redirect = client.get("/disks?entity=/data%3Cscript%3E&range=6h",
                          headers=headers, follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].startswith("/trends/disk/space-growth?")

    generic = client.get(
        "/api/trend?monitor=disk&profile=space-growth&"
        "entity=/data%3Cscript%3E&range=6h", headers=headers,
    ).json()
    assert generic["panels"]["value"]["metric"] == "used_pct"
    assert generic["panels"]["projection"] is not None


def test_generic_leak_trend_and_context_links_ui_12_ts_10(tmp_path):
    """[UI-12][TS-10] Leak uses the shared explorer with no projection panel."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/leak.toml"
    (paths.monitors_dir / "leak.toml").write_text(builtin.read_text())
    entity = "firefox:7:1"
    conn = connect(paths.db_file)
    for sid, (metric, value) in enumerate({
        "rss_mb": 512.0,
        "rss_slope_mbph": 48.0,
        "rss_growth_confidence": 0.9,
    }.items(), 1):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) "
            "VALUES(?,'leak',?,?,0)", (sid, entity, metric)
        )
        conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
                     (sid, 1000, value))
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('leak',?,1,1000,'{}')", (entity,)
    )
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(2,'leak','leak',?,'open',2,'leak-warn',900,900,1,1)", (entity,)
    )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    page = client.get(
        f"/trends/leak/rss-growth?entity={entity}&range=6h", headers=headers
    )
    assert page.status_code == 200
    assert "Process memory growth" in page.text
    assert 'data-panel="confidence"' in page.text
    assert 'data-panel="projection"' not in page.text
    assert "Latest signed rate +48.00 MiB/hour" in page.text
    dashboard = client.get("/", headers=headers).text
    assert 'href="/trends/leak/rss-growth"' in dashboard
    incident = client.get("/incidents/2", headers=headers).text
    assert "/trends/leak/rss-growth?entity=firefox%3A7%3A1" in incident

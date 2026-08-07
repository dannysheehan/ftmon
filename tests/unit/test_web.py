"""[TS-07][UI-03][UI-04][UI-06][UI-07][UI-08][SE-02] HTTP-level web dashboard tests."""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ftmon import __version__
from ftmon.clock import FakeClock
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.web.app import _glance_meter, create_app


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
    """[TS-07][UI-08][SE-02] Pages escape strings and reject framing/form retargeting."""
    client, _paths = _client(tmp_path)
    headers = {"host": "localhost:8420"}
    for url in ("/", "/incidents", "/incidents/1", "/metrics", "/baselines", "/events",
                "/monitors", "/self"):
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "form-action 'self'" in csp
        assert "base-uri 'none'" in csp
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        assert "access-control-allow-origin" not in response.headers
    page = client.get("/incidents", headers=headers).text
    assert "evil&lt;script&gt;" in page
    assert "▲ error" in page
    assert 'data-refresh-ms="5000"' in page
    assert 'data-refresh-ms="5000"' in client.get("/events", headers=headers).text
    assert 'data-refresh-ms="15000"' in client.get("/monitors", headers=headers).text
    assert 'data-refresh-ms="15000"' in client.get("/self", headers=headers).text
    rejected = client.get("/", headers={"host": "attacker.example"})
    assert rejected.status_code == 400
    assert rejected.headers["x-frame-options"] == "DENY"


def test_offline_branding_has_accessible_wordmark_and_packaged_icons_ui_01_ui_09(
    tmp_path,
):
    """[UI-01][UI-09] Branding stays local and never replaces the link name."""
    client, _paths = _client(tmp_path)
    headers = {"host": "localhost:8420"}
    page = client.get("/", headers=headers)
    assert 'class="brand" href="/"' in page.text
    assert 'brand/ftmon-mark.png' in page.text
    assert 'alt="" width="44" height="44"><span>FTMON</span>' in page.text
    assert 'brand/favicon.ico' in page.text
    assert 'brand/favicon-64.png' in page.text
    assert 'brand/apple-touch-icon.png' in page.text
    for path, content_type in (
        ("/static/brand/ftmon-mark.png", "image/png"),
        ("/static/brand/favicon.ico", "image/vnd.microsoft.icon"),
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(content_type)


def test_self_identifies_running_web_package_ui_02(tmp_path):
    """[UI-02] Self reports the package version loaded by the web process."""
    client, _paths = _client(tmp_path)
    page = client.get("/self", headers={"host": "localhost:8420"})
    assert f"<strong>Web process version</strong> {__version__}." in page.text


def _panel_row(page: str, row_id: str) -> str:
    """The markup of one budget row, so absence is asserted per row."""
    marker = f'data-row="{row_id}"'
    start = page.index(marker)
    return page[start:page.index("</tr>", start)]


def _seed_self_metrics(paths, **metrics):
    """Give the self entity real readings so the panel has something to show."""
    conn = connect(paths.db_file)
    for i, (metric, value) in enumerate(metrics.items(), start=8000):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) "
            "VALUES (?,'self','ftmon',?,1)", (i, metric))
        conn.execute(
            "INSERT INTO samples(series_id,ts,value) VALUES (?,?,?)",
            (i, 1_700_000_000, value))
    conn.commit()
    conn.close()


def test_self_panel_separates_used_allocated_and_reusable_dm_05(tmp_path):
    """[DM-05][UI-02] The panel an operator reads to judge DM-05 headroom.

    Issue #104 found this surface printing file allocation as if it were the
    budget figure. The three quantities must stay distinguishable, and the
    used figure must be the one shown against the budget.
    """
    client, paths = _client(tmp_path)
    _seed_self_metrics(
        paths,
        db_used_bytes=120 * 1024 * 1024,
        db_allocated_bytes=180 * 1024 * 1024,
        db_freelist_bytes=60 * 1024 * 1024,
        db_headroom_bytes=80 * 1024 * 1024,
    )
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    assert "Database (used pages)" in page
    assert "120.0 MB" in page, "used pages, the DM-05 figure"
    assert "180.0 MB" in page, "file allocation, named separately"
    assert "60.0 MB" in page, "reusable freelist, named separately"
    assert "Used pages are the DM-05 figure" in page


def test_self_panel_reports_absent_metrics_as_unavailable_ui_09(tmp_path):
    """[UI-09][RB-02] A metric nobody published is not a measurement of zero.

    The stage timings #106 will add do not exist yet; showing them as 0 ms
    would assert a reading nobody took. Same for catalog pressure before the
    daemon has published it.
    """
    client, _paths = _client(tmp_path)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    assert "Per-stage timings are not collected yet" in page
    assert "no daemon has published it" in page
    # Asserted per row, not by counting occurrences: the catalog section has
    # its own "not available yet", so a page-wide count passes with only two
    # correct budget rows. Each row carries a stable data-row identifier.
    for row_id in ("cpu", "memory", "database"):
        assert "not available yet" in _panel_row(page, row_id), (
            f"the {row_id} row must report absence, not a zero"
        )
    # And no row may present a confident zero for a metric nobody published.
    assert "0.0 MB" not in page
    assert "0.0%" not in page


def test_self_panel_states_values_in_text_not_only_meters_ui_09(tmp_path):
    """[UI-09] Colour and bar length are never the only carriers of a reading."""
    client, paths = _client(tmp_path)
    _seed_self_metrics(paths, cpu_pct=3.0, cpu_10m=2.5)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    # The number, its limit and the percentage are all present as text, so the
    # page is readable with styles or SVG unavailable.
    assert "10-minute average 2.5%" in page
    assert "RB-01 target 1.0% of one core" in page


def test_dashboard_stat_leads_with_used_bytes_dm_05(tmp_path):
    """[DM-05][UI-04] The dashboard's headline "Database" stat must be the
    DM-05 used-pages figure, not the file allocation issue #104 found
    presented there as though it were the budget verdict."""
    client, _paths = _client(tmp_path)
    page = client.get("/", headers={"host": "localhost:8420"})
    assert "Database MB (used)" in page.text


def test_dark_mode_uses_legible_semantic_card_palette_ui_09(tmp_path):
    """[UI-09] Dark cards replace both light gradients and semantic foregrounds."""
    client, _paths = _client(tmp_path)
    css = client.get(
        "/static/ftmon.css", headers={"host": "localhost:8420"}
    ).text
    dark = css.split("@media (prefers-color-scheme: dark)", 1)[1]
    for declaration in (
        "--clear: #7ee2a4", "--warn: #ffd066", "--error: #ff8b96",
        "--neutral: #c4cfdb", "#211a0b", "#241015", "#151d27",
    ):
        assert declaration in dark
    assert ".monitor-list .monitor-tile:nth-child(odd)" not in css


def test_dashboard_tiles_restore_accessible_legacy_health_states_ui_14_ts_12(tmp_path):
    """[UI-14][TS-12] Clear/warn/error/disabled/config states include icon+text."""
    client, paths = _client(tmp_path)
    builtins = Path(__file__).parents[2] / "src/ftmon/definitions/builtins"
    for name in ("disk", "leak", "load", "hog", "service"):
        text = (builtins / f"{name}.toml").read_text()
        if name == "hog":
            text = text.replace("enabled = true", "enabled = false")
        (paths.monitors_dir / f"{name}.toml").write_text(text)
    (paths.monitors_dir / "broken.toml").write_text("not valid toml = [")
    conn = connect(paths.db_file)
    for name in ("disk", "leak", "load", "hog"):
        conn.execute(
            "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
            "VALUES(?,900,?,?)", (name, name, name)
        )
    conn.executemany(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(?,?,?,? ,?,?,?,900,900,1,1)",
        [
            (2, "leak", "leak", "firefox:7:1", "acked", 2, "leak-warn"),
            (3, "load", "pressure", "host", "open", 3, "pressure-error"),
            (4, "hog", "hog", "cpu:1:1", "open", 4, "hog-error"),
        ],
    )
    conn.commit()
    conn.close()
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-monitor="disk" data-state="clear"' in page and "✓" in page
    assert 'data-monitor="leak" data-state="warning"' in page and "▲" in page
    assert 'data-monitor="load" data-state="error"' in page and "✖" in page
    assert 'data-monitor="hog" data-state="disabled"' in page and "●" in page
    assert 'data-monitor="service" data-state="unknown"' in page
    assert 'data-monitor="broken" data-state="config-error"' in page
    assert "/incidents?monitor=leak" in page and "1 live incident" in page
    assert page.index('<h2>Needs attention</h2>') < page.index('data-monitor="hog"')
    assert page.index('data-monitor="hog"') < page.index('<h2>All clear</h2>')
    assert "flash" not in page.lower()

    filtered = client.get(
        "/incidents?monitor=leak", headers={"host": "localhost:8420"}
    ).text
    assert "firefox:7:1" in filtered and "pressure" not in filtered


def test_dashboard_stale_precedence_never_claims_clear_ui_14_ts_12(tmp_path):
    """[UI-04][UI-14][TS-12] Stale evidence overrides clear, warning, and disabled states."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    (paths.monitors_dir / "disk.toml").write_text(builtin.read_text())
    conn = connect(paths.db_file)
    conn.execute("UPDATE meta SET value='900' WHERE key='last_tick_ts'")
    conn.execute(
        "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
        "VALUES('disk',900,'disk','disk')"
    )
    conn.commit()
    conn.close()
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-monitor="disk" data-state="unknown"' in page
    assert 'data-monitor="disk" data-state="clear"' not in page


def test_glance_meter_percent_and_limit_scales_ui_17():
    """[UI-17] Percent meters use 0..100; other units scale to the highest threshold."""
    assert _glance_meter(48.0, "MiB/hour", ()) is None
    leak = _glance_meter(48.0, "MiB/hour", (("warn", 32.0), ("error", 128.0)))
    assert leak is not None
    assert leak.scale_max == 128.0
    assert leak.fill_pct == pytest.approx(48 / 128 * 100)
    assert leak.show_full_mark is False
    assert leak.fill_tone == "warn"  # past warn 32, below error 128
    past = _glance_meter(200.0, "MiB/hour", (("warn", 32.0), ("error", 128.0)))
    assert past is not None
    assert past.fill_pct == 100.0  # overshoot clamps; axis stays at error
    assert past.fill_tone == "error"
    quiet = _glance_meter(10.0, "MiB/hour", (("warn", 32.0), ("error", 128.0)))
    assert quiet is not None
    assert quiet.fill_tone == "ok"
    meter = _glance_meter(94.0, "percent", (("warn", 92.0), ("error", 97.0)))
    assert meter is not None
    assert meter.scale_max == 100.0
    assert meter.fill_pct == 94.0
    assert meter.show_full_mark is True
    assert meter.fill_tone == "warn"
    hog = _glance_meter(140.0, "percent", (("warn", 80.0),))
    assert hog is not None
    assert hog.scale_max == 100.0
    assert hog.fill_pct == 100.0
    assert hog.fill_tone == "warn"
    frac = _glance_meter(0.00015, "fraction", (("warn", 0.85), ("error", 0.95)))
    assert frac is not None
    assert frac.scale_max == 0.95
    assert frac.fill_pct == pytest.approx(0.00015 / 0.95 * 100)
    assert frac.fill_tone == "ok"


def test_dashboard_glance_renders_fresh_active_value_without_changing_state_ui_17_ts_12(
    tmp_path,
):
    """[UI-17][TS-12] Declared context is escaped, current and health-neutral."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    (paths.monitors_dir / "disk.toml").write_text(builtin.read_text())
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
        "VALUES('disk',990,'disk','disk')"
    )
    conn.executemany(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts,attrs) "
        "VALUES('disk',?,900,999,?,NULL)",
        [("/home<script>", None), ("/old", None), ("/gone", 999)],
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts,attrs) "
        "VALUES('disk','/snap/read-only',900,999,NULL,?)",
        (json.dumps({"fstype": "squashfs", "device": "/dev/loop0"}),),
    )
    conn.executemany(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(?,'disk',?,'used_pct',1)",
        [(10, "/home<script>"), (11, "/old"), (12, "/gone"),
         (13, "/snap/read-only")],
    )
    conn.executemany(
        "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
        [(10, 995, 94), (11, 800, 99), (12, 999, 100), (13, 999, 100)],
    )
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(20,'disk','space','/home','acked',2,'space-warn',995,995,1,1)"
    )
    conn.commit()
    conn.close()

    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-monitor="disk" data-state="warning"' in page
    assert (
        '<p class="tile-glance-value"><strong>/home&lt;script&gt;</strong> 94%'
        in page
    )
    assert 'tile-threshold-warn">warn 92%</span>' in page
    assert 'tile-threshold-error">error 97%</span>' in page
    assert "· warn 92% · error 97%" not in page
    assert 'role="meter"' in page
    assert 'aria-valuenow="94"' in page
    assert 'aria-valuemax="100"' in page
    assert 'tile-meter-svg' in page
    assert 'class="tile-meter-fill tile-meter-fill-warn"' in page and 'width="94"' in page
    assert 'tile-meter-mark-warn' in page
    assert 'tile-meter-mark-error' in page
    assert 'tile-meter-mark-full' in page
    assert "<title>100%</title>" in page
    assert 'style="width:' not in page.split("tile-meter", 1)[1].split("</div>", 1)[0]
    assert "/home<script>" not in page
    assert "/snap/read-only" not in page


def test_dashboard_glance_is_omitted_for_disabled_tile_ui_17_ts_12(tmp_path):
    """[UI-17][TS-12] A retained value cannot make a disabled tile look current."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    text = builtin.read_text().replace("enabled = true", "enabled = false", 1)
    (paths.monitors_dir / "disk.toml").write_text(text)
    conn = connect(paths.db_file)
    conn.execute("DELETE FROM incidents")
    conn.execute(
        "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
        "VALUES('disk',990,'disk','disk')"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('disk','/',900,999,NULL)"
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(10,'disk','/','used_pct',1)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(10,995,94)")
    conn.commit()
    conn.close()

    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-monitor="disk" data-state="disabled"' in page
    assert "tile-glance" not in page
    assert "<h2>Needs attention</h2>" not in page
    assert "No monitors need attention. 0 clear; 1 intentionally disabled." in page
    assert page.index("<h2>Disabled</h2>") < page.index('data-monitor="disk"')


def test_events_tile_shows_raw_ingest_rate_ui_18_dm_20(tmp_path):
    """[UI-18][DM-20] The Events tile distinguishes quiet from a coalesced storm."""
    client, paths = _client(tmp_path)
    profile = (
        Path(__file__).parents[2]
        / "src/ftmon/definitions/profile/macos/events.toml"
    )
    (paths.monitors_dir / "events.toml").write_text(profile.read_text())
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO cursors(source,cursor,updated_ts) VALUES('oslog','cursor',999)"
    )
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('self','ftmon',900,999,NULL)"
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(99,'self','ftmon','event_rate_per_min',1)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(99,999,842)")
    conn.commit()
    conn.close()

    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-monitor="events" data-state="clear"' in page
    assert "<strong>ingest</strong> 842 events/min" in page


def test_ui_ack_requires_origin_and_reuses_small_writes_ui_03_ui_08(tmp_path):
    """[UI-03][UI-08] Ack POST requires Origin and hits the same path as CLI."""
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
    default_page = client.get("/metrics", headers=headers)
    assert ">disk</option>" in default_page.text
    assert "No persisted metric observations are available" not in default_page.text
    assert "<option selected>6h</option>" in default_page.text

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

    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(4,'leak','expired:8:1','rss_mb',0)"
    )
    conn.commit()
    conn.close()
    leak_page = client.get("/metrics?monitor=leak&range=6h", headers=headers)
    assert "firefox:7:1" in leak_page.text
    assert "expired:8:1" not in leak_page.text

    expired = client.get(
        "/metrics?monitor=leak&entity=expired%3A8%3A1&metric=rss_mb&range=6h",
        headers=headers,
    )
    assert "expired:8:1" in expired.text
    assert "No observations are retained for this series" in expired.text
    assert "data-metric-chart" not in expired.text


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
    assert data["panel"]["y_domain"] == [0.0, 100.0]
    assert data["incidents"][0]["id"] == 3
    assert data["matching_trends"][0]["id"] == "space-growth"
    assert data["baseline"] is None
    assert "panels" not in data  # Metrics never fabricates interpreted panels


def test_metrics_baseline_overlay_uses_native_points_and_accessible_summary_ui_13_ts_11(
    tmp_path,
):
    """[UI-13][TS-11] Baselines expose retained 5m evidence without bridging gaps."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'load','host','load1',1)"
    )
    conn.executemany(
        "INSERT INTO rollup5m(series_id,bucket,avg,min,max,last,cnt) "
        "VALUES(1,?,?,?,?,?,1)",
        [(0, 1.0, 1.0, 1.0, 1.0), (300, 2.0, 2.0, 2.0, 2.0),
         (900, 3.0, 3.0, 3.0, 3.0)],
    )
    conn.execute(
        "INSERT INTO baselines(series_id,value,updates,updated_bucket,half_life_s) "
        "VALUES(1,20,3,900,259200)"
    )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    api = client.get(
        "/api/series?monitor=load&entity=host&metric=load1&range=7d",
        headers=headers,
    )
    assert api.status_code == 200
    baseline = api.json()["baseline"]
    assert baseline["level"] == 20
    assert baseline["updates"] == 3
    assert baseline["required_updates"] == 240
    assert baseline["coverage"] == 3 / 240
    assert baseline["ready"] is False
    assert baseline["updated_at"] == 900
    assert baseline["half_life_s"] == 259200
    assert [point[0] for point in baseline["points"]] == [0, 300, 900]
    assert [[point[0] for point in run] for run in baseline["runs"]] == [[0, 300], [900]]
    visible_values = [
        point[1]
        for key in ("points", "lower", "upper")
        for point in api.json()["panel"][key]
        if point[1] is not None
    ] + [point[1] for point in baseline["points"]]
    assert api.json()["panel"]["y_domain"][0] < min(visible_values)
    assert api.json()["panel"]["y_domain"][1] > max(visible_values)
    page = client.get(
        "/metrics?monitor=load&entity=host&metric=load1&range=7d",
        headers=headers,
    )
    assert "Baseline level 20" in page.text
    assert "1% learned (3 of 240 updates), still learning" in page.text
    assert "gaps are not connected" in page.text
    assert 'data-baseline-state="learning"' in page.text
    assert "<strong>Baseline</strong> — learning" in page.text
    script = client.get("/static/ftmon.js", headers=headers).text
    assert "baselinePlugin(metric.baseline.runs)" in script
    assert "spanGaps" not in script

    conn = connect(paths.db_file)
    conn.execute("UPDATE baselines SET updates=240 WHERE series_id=1")
    conn.commit()
    conn.close()
    ready_page = client.get(
        "/metrics?monitor=load&entity=host&metric=load1&range=7d",
        headers=headers,
    )
    assert 'data-baseline-state="ready"' in ready_page.text
    assert "<strong>Baseline</strong> — ready" in ready_page.text


def test_metrics_labels_baseline_without_retained_history_ui_13_ts_11(tmp_path):
    """[UI-13][TS-11] A stored baseline stays distinguishable from no baseline."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) "
        "VALUES(1,'load','host','load1',1)"
    )
    conn.execute("INSERT INTO samples(series_id,ts,value) VALUES(1,1000,3)")
    conn.execute(
        "INSERT INTO baselines(series_id,value,updates,updated_bucket,half_life_s) "
        "VALUES(1,2.5,10,0,259200)"
    )
    conn.commit()
    conn.close()

    page = client.get(
        "/metrics?monitor=load&entity=host&metric=load1&range=15m",
        headers={"host": "localhost:8420"},
    )
    assert "No retained baseline history falls in this selected range" in page.text
    assert 'data-baseline-state="learning"' in page.text
    payload = json.loads(page.text.split('id="metric-data">', 1)[1].split("</script>", 1)[0])
    assert payload["baseline"]["points"] == []
    assert payload["baseline"]["runs"] == []
    assert payload["panel"]["y_domain"] == [2.0, 4.0]


def test_baselines_index_filters_paginates_links_and_rejects_bad_state_ui_02(tmp_path):
    """[UI-02] Baseline inventory shares bounded keyset semantics and Metrics links."""
    client, paths = _client(tmp_path)
    conn = connect(paths.db_file)
    for sid, monitor, entity, metric, updates in (
        (1, "disk", "/home", "used_pct", 30),
        (2, "disk", "/var", "used_pct", 240),
        (3, "load", "host", "load1", 250),
    ):
        conn.execute(
            "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES(?,?,?,?,1)",
            (sid, monitor, entity, metric),
        )
        conn.execute(
            "INSERT INTO baselines(series_id,value,updates,updated_bucket,half_life_s) "
            "VALUES(?,?,?,?,259200)",
            (sid, float(sid), updates, 900),
        )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    first = client.get("/baselines?monitor=disk&limit=1", headers=headers)
    assert first.status_code == 200
    assert "/home" in first.text and "/var" not in first.text
    assert "12% — learning" in first.text
    assert "/metrics?monitor=disk&amp;entity=%2Fhome&amp;metric=used_pct" in first.text
    assert '<a rel="next"' in first.text
    next_href = first.text.split('<a rel="next" href="', 1)[1].split('"', 1)[0]
    second = client.get(next_href.replace("&amp;", "&"), headers=headers)
    assert second.status_code == 200
    assert "/var" in second.text and "100% — ready" in second.text
    assert "load1" not in second.text
    for url in (
        "/baselines?limit=0", "/baselines?limit=nope", "/baselines?ready=maybe",
        "/baselines?cursor=not-a-cursor",
        next_href.replace("&amp;", "&").replace("monitor=disk", "monitor=load"),
    ):
        assert client.get(url, headers=headers).status_code == 400


def test_baselines_index_empty_and_missing_database_states_ui_02(tmp_path):
    """[UI-02] Operators can distinguish no matches from no database."""
    client, _paths = _client(tmp_path)
    headers = {"host": "localhost:8420"}
    empty = client.get("/baselines", headers=headers)
    assert "No stored baselines match these filters" in empty.text

    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "missing-config"),
        "FTMON_DATA_DIR": str(tmp_path / "missing-data"),
        "FTMON_STATE_DIR": str(tmp_path / "missing-state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "missing-run"),
    })
    missing = TestClient(create_app(paths, FakeClock(wall=1000, mono=1000))).get(
        "/baselines", headers=headers,
    )
    assert "database is not available yet" in missing.text


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


def test_trends_selector_hides_gone_entities_but_keeps_linked_history_ui_12(tmp_path):
    """[UI-12] The primary selector stays operational without breaking incident links."""
    client, paths = _client(tmp_path)
    builtin = Path(__file__).parents[2] / "src/ftmon/definitions/builtins/leak.toml"
    (paths.monitors_dir / "leak.toml").write_text(builtin.read_text())
    conn = connect(paths.db_file)
    conn.executemany(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,gone_ts,attrs) "
        "VALUES('leak',?,1,?,?,'{}')",
        [
            ("active:1:1", 1000, None),
            ("stale-null:4:1", 100, None),
            ("exited:2:1", 1000, 1000),
            ("exited:3:1", 1000, 1000),
        ],
    )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}

    default_page = client.get("/trends/leak/rss-growth", headers=headers)
    assert default_page.status_code == 200
    assert '<option value="active:1:1" selected>active:1:1</option>' in default_page.text
    assert "stale-null:4:1" not in default_page.text
    assert "exited:2:1" not in default_page.text
    assert "exited:3:1" not in default_page.text

    linked_page = client.get(
        "/trends/leak/rss-growth?entity=exited%3A2%3A1", headers=headers
    )
    assert linked_page.status_code == 200
    assert '<option value="exited:2:1" selected>exited:2:1</option>' in linked_page.text
    assert "exited:3:1" not in linked_page.text


def test_incident_detail_shows_display_and_attrs_sa_09(tmp_path):
    """[SA-09] incident detail reads the matching entities row and shows
    the sampled display identity plus attrs (exe, cmd_hint, ...); a
    missing entities row (no attrs sampled yet) must not break the page."""
    client, paths = _client(tmp_path)
    entity = "MainThread:9:1"
    conn = connect(paths.db_file)
    attrs = json.dumps({
        "name": "MainThread", "exe": "/home/u/.local/bin/agent",
        "exe_base": "agent", "display": "agent (MainThread)",
        "cmd_hint": "agent index.js",
    })
    conn.execute(
        "INSERT INTO entities(monitor,entity_id,first_seen,last_seen,attrs) "
        "VALUES('leak',?,1,1000,?)", (entity, attrs)
    )
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(3,'leak','leak',?,'open',2,'leak-warn',900,900,1,1)", (entity,)
    )
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}

    page = client.get("/incidents/3", headers=headers)
    assert page.status_code == 200
    assert "agent (MainThread)" in page.text
    assert "exe_base" in page.text and "agent" in page.text
    assert "cmd_hint" in page.text and "agent index.js" in page.text

    # incident #1 (from _client) has no matching entities row: must still 200.
    no_attrs = client.get("/incidents/1", headers=headers)
    assert no_attrs.status_code == 200


def test_self_panel_compares_against_the_hosts_own_thresholds_md_01(tmp_path):
    """[MD-01][RB-02] The panel measures against this host's self.toml.

    An operator who retunes cpu_budget_pct must see the panel compare against
    what their daemon actually alarms at, not a constant compiled into the
    view. RB-01's normative target is shown alongside precisely because the
    two legitimately differ (the Windows profile runs at 30%).
    """
    client, paths = _client(tmp_path)
    (paths.monitors_dir / "self.toml").write_text(
        'schema = 1\n'
        '[monitor]\n'
        'name = "self"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "self"\n'
        '[parameters]\n'
        'cpu_budget_pct = { value = 4.0, doc = "d" }\n'
        'rss_budget_mb = { value = 100, doc = "d" }\n'
        'db_warn_mb = { value = 230, doc = "d" }\n'
        '[[rule]]\n'
        'id = "cpu-budget"\ngroup = "cpu-budget"\n'
        "when = 'avg(cpu_pct, \"10m\") > cpu_budget_pct'\n"
        'severity = "warning"\nconfirm_cycles = 3\nmessage = "m"\n',
        encoding="utf-8",
    )
    _seed_self_metrics(paths, cpu_pct=2.0, cpu_10m=2.0)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    # Compared against the host's 4.0, not RB-01's 1.0 ...
    assert "of 4.0%" in page
    # ... while RB-01's target stays visible, since they differ on purpose.
    assert "RB-01 target 1.0% of one core" in page
    assert "(50%)" in page, "2.0 of 4.0 is half the budget"


def test_self_panel_reports_utilization_above_one_hundred_percent_rb_02(tmp_path):
    """[RB-02][UI-09] A breach must show how far over, not saturate at 100%.

    Clamping the displayed percentage would under-report severity: 6% CPU
    against a 1.5% threshold is 400% of budget, and "100%" reads as merely
    at-the-limit. Only the meter geometry is clamped, because an SVG cannot
    be four times its own width.
    """
    client, paths = _client(tmp_path)
    (paths.monitors_dir / "self.toml").write_text(
        'schema = 1\n'
        '[monitor]\n'
        'name = "self"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "self"\n'
        '[parameters]\n'
        'cpu_budget_pct = { value = 1.5, doc = "d" }\n'
        '[[rule]]\n'
        'id = "cpu-budget"\ngroup = "cpu-budget"\n'
        "when = 'avg(cpu_pct, \"10m\") > cpu_budget_pct'\n"
        'severity = "warning"\nconfirm_cycles = 3\nmessage = "m"\n',
        encoding="utf-8",
    )
    _seed_self_metrics(paths, cpu_pct=6.0, cpu_10m=6.0)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    row = _panel_row(page, "cpu")
    assert "(400%)" in row, "true utilization, not clamped to 100"
    assert "over" in row, "and flagged as a breach"
    # The meter itself stays inside its viewBox.
    assert 'width="100"' in row


def test_self_panel_shows_a_measured_zero_stage_timing_rb_02(tmp_path):
    """[RB-02] 0.0 is a reading; only a missing metric is an absence.

    The template originally filtered stages on truthiness, so a genuine zero
    would have been reported as "not collected yet" — defeating the very
    distinction this panel exists to keep once #106 starts publishing.
    """
    client, paths = _client(tmp_path)
    # Only zeros: a fixture with any truthy timing alongside them renders the
    # section either way, so it would not isolate the truthiness bug.
    _seed_self_metrics(paths, commit_s=0.0, reap_s=0.0)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    assert "Per-stage timings are not collected yet" not in page, (
        "every published timing is zero, which is data, not absence"
    )
    assert "0.0 ms" in page, "a measured zero must be shown as measured"


def test_self_panel_links_to_six_and_twenty_four_hour_history_ui_12(tmp_path):
    """[UI-12][UI-02] Each budget links to its own history, not a generic page.

    A reading without its recent shape cannot answer "is this a spike or a
    trend", which is the first question a budget number raises.
    """
    client, paths = _client(tmp_path)
    _seed_self_metrics(paths, cpu_pct=2.0, cpu_10m=2.0, rss_bytes=5e7,
                       db_used_bytes=1e8)
    page = client.get("/self", headers={"host": "localhost:8420"}).text
    for metric in ("cpu_10m", "rss_bytes", "db_used_bytes"):
        assert f"metric={metric}&amp;range=6h" in page, f"6h history for {metric}"
        assert f"metric={metric}&amp;range=24h" in page, f"24h history for {metric}"


def test_dashboard_strip_summarises_self_budgets_ui_02(tmp_path):
    """[UI-02][RB-02] The strip answers "is FTMON itself healthy" in passing.

    Composed by the same function as the /self panel, so the two surfaces
    cannot disagree about whether the daemon is inside its budgets — the
    duplication issue #104 removed from the DM-05 arithmetic.
    """
    client, paths = _client(tmp_path)
    (paths.monitors_dir / "self.toml").write_text(
        'schema = 1\n'
        '[monitor]\n'
        'name = "self"\ndescription = "d"\nversion = 1\nenabled = true\n'
        'platforms = ["linux"]\ninterval = "60s"\nsource = "self"\n'
        '[parameters]\n'
        'cpu_budget_pct = { value = 4.0, doc = "d" }\n'
        'rss_budget_mb = { value = 100, doc = "d" }\n'
        '[[rule]]\n'
        'id = "cpu-budget"\ngroup = "cpu-budget"\n'
        "when = 'avg(cpu_pct, \"10m\") > cpu_budget_pct'\n"
        'severity = "warning"\nconfirm_cycles = 3\nmessage = "m"\n',
        encoding="utf-8",
    )
    _seed_self_metrics(paths, cpu_pct=2.0, cpu_10m=2.0, rss_bytes=50 * 1024 * 1024)
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert "Self CPU %" in page
    assert "Self RSS MB" in page
    assert ">2.0<" in page, "CPU value on the strip"
    assert ">50<" in page, "RSS in MB on the strip"
    assert "50% of the threshold set on this host" in page


def test_dashboard_strip_omits_self_budgets_when_unmeasured_ui_02(tmp_path):
    """[UI-02] Nothing measured, nothing claimed — no zero-valued stat tiles."""
    client, _paths = _client(tmp_path)
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert "Self CPU %" not in page
    assert "Self RSS MB" not in page

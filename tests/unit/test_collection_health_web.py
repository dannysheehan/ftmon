"""[EC-11] Web collection evidence and retained panel history."""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from ftmon.clock import FakeClock
from ftmon.definitions.loader import load_text
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.query import Query
from ftmon.web.app import create_app
from tests.platform_permissions import make_private, toml_path, trusted_python_executable

_MONITOR = '''schema = 1
[monitor]
name = "probe"
description = "Check health fixture"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "external"
[source_options]
check = "probe"
entity = "device"
[[source_options.perfdata]]
label = "temp"
metric = "temperature"
plugin_uom = "C"
unit = "celsius"
kind = "gauge"
[[source_options.perfdata]]
label = "fan"
metric = "fan"
plugin_uom = "%"
unit = "percent"
kind = "gauge"
[[rule]]
id = "probe-health"
group = "health"
when = "plugin_state == 3"
severity = "warning"
confirm_cycles = 2
clear_cycles = 2
message = "{plugin_message}"
[[rule]]
id = "fan-low"
group = "fan"
when = "fan < 10"
severity = "warning"
confirm_cycles = 2
clear_cycles = 2
message = "Fan low"
[[trend]]
id = "temperature"
kind = "growth"
title = "Temperature trend"
value_metric = "temperature"
value_unit = "celsius"
rate_metric = "fan"
rate_unit = "percent"
'''


def _site(tmp_path, *, monitor_text=_MONITOR):
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "config"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    paths.check_registry_file.write_text(
        f'[check.probe]\nargv=["{toml_path(trusted_python_executable())}"]\n'
        'protocol="ftmon-json"\ntimeout="2s"\n'
    )
    make_private(paths.check_registry_file, 0o600)
    (paths.monitors_dir / "probe.toml").write_text(monitor_text)
    definition = load_text(monitor_text)
    conn = connect(paths.db_file)
    migrate(conn)
    conn.executemany("INSERT INTO meta(key,value) VALUES(?,?)", [
        ("last_tick_ts", "1000"), ("daemon_pid", "42"),
    ])
    conn.execute("INSERT INTO monitor_loads VALUES(?,?,?,?)",
                 ("probe", 1000, definition.content_hash, definition.normalized_toml))
    conn.execute("INSERT INTO entities VALUES(?,?,?,?,?,?)",
                 ("probe", "device", 1, 1000, None, "{}"))
    conn.commit()
    conn.close()
    return TestClient(create_app(paths, FakeClock(wall=1000, mono=1000))), paths, definition


def _report(conn, definition, *, sampled_at=1000, plugin_state=3,
            metrics=("plugin_state", "plugin_ok", "duration_s", "temperature"),
            rules=None, pid=42, message="Check failed"):
    observation = {
        "content_hash": definition.content_hash, "entity_id": "device",
        "sampled_at": sampled_at, "plugin_state": plugin_state,
        "plugin_message": message, "failure": "protocol" if plugin_state == 3 else None,
        "available_metrics": list(metrics),
        "rules": rules or {"probe-health": "TRUE" if plugin_state == 3 else "FALSE",
                           "fan-low": "UNKNOWN"},
    }
    report = {"version": 1, "daemon_pid": pid, "generated_ts": 1000,
              "monitors": {"probe": observation}, "truncated": False}
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('external_observations',?)",
                 (json.dumps(report),))


def test_collection_status_links_only_authored_owner_and_escapes_message_ec_11(tmp_path):
    """[EC-11] A matching owner links; an unrelated rung in the group does not."""
    client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition, message="bad <script>alert(1)</script>")
    conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                 "opened_ts,last_change_ts,notify_count,occurrences) "
                 "VALUES(1,'probe','health','device','open',2,'other-rule',900,900,1,1)")
    conn.commit()
    query = Query(conn)
    evidence = query.collection_health(definition, now=1000, daemon_stale=False)
    assert evidence["state"] == "failed" and evidence["incident"] is None
    conn.execute("UPDATE incidents SET owning_rule='probe-health' WHERE id=1")
    conn.commit()
    assert query.collection_health(definition, now=1000, daemon_stale=False)["incident"]["id"] == 1
    conn.close()
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-collection-state="failed"' in page
    assert 'href="/incidents/1"' in page
    assert "bad &lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "bad <script>" not in page


def test_partial_panels_older_history_and_rule_unknown_ec_11(tmp_path):
    """[EC-11] An empty range is history, while a missing current input blocks recovery."""
    client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=0, message="OK")
    conn.execute("INSERT INTO series VALUES(1,'probe','device','temperature',1)")
    conn.execute("INSERT INTO rollup5m VALUES(1,?,30,30,30,30,1)",
                 (1000 - 3 * 86400,))
    conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                 "opened_ts,last_change_ts,notify_count,occurrences) "
                 "VALUES(2,'probe','fan','device','open',2,'fan-low',900,900,1,1)")
    conn.commit()
    conn.close()
    headers = {"host": "localhost:8420"}
    page = client.get(
        "/trends/probe/temperature?entity=device&range=24h&group=fan", headers=headers
    ).text
    assert "No observations in selected range" in page
    assert "5m bucket starting" in page
    assert "Never sampled, or retained history has expired" in page
    assert "range=7d&amp;group=fan" in page
    assert 'data-collection-state="available"' in page
    assert 'data-panel="value"' in page and 'data-panel="rate"' in page
    incident = client.get("/incidents/2", headers=headers).text
    assert "Owning rule fan-low: UNKNOWN" in incident
    assert "Missing current metrics: fan" in incident
    assert "does not count as recovery" in incident
    data = client.get(
        "/api/trend?monitor=probe&profile=temperature&entity=device&range=24h&group=fan",
        headers=headers,
    ).json()
    assert data["panel_evidence"]["value"]["last_retained"]["resolution"] == "5m"
    assert data["panel_evidence"]["rate"]["last_retained"] is None
    assert data["collection"]["state"] == "available"
    older = client.get(
        "/api/trend?monitor=probe&profile=temperature&entity=device&range=7d&group=fan",
        headers=headers,
    ).json()
    assert older["resolution"] == "5m"
    assert older["panels"]["value"]["points"]


def test_collection_identity_freshness_and_disabled_ec_11(tmp_path):
    """[EC-11] Definition, incarnation, and interval gate the current verdict."""
    client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    query = Query(conn)
    assert query.collection_health(
        definition, now=1000, daemon_stale=False
    )["state"] == "unavailable"
    _report(conn, definition, sampled_at=800)
    conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                 "opened_ts,last_change_ts,notify_count,occurrences) "
                 "VALUES(7,'probe','health','device','open',2,'probe-health',900,900,1,1)")
    conn.commit()
    stale = query.collection_health(
        definition, now=1000, daemon_stale=False
    )
    assert stale["state"] == "sample_stale" and stale["incident"]["id"] == 7
    assert query.collection_health(
        definition, now=1000, daemon_stale=True
    )["state"] == "daemon_stale"
    _report(conn, definition, pid=999)
    conn.commit()
    assert query.collection_health(
        definition, now=1000, daemon_stale=False
    )["state"] == "unavailable"
    _report(conn, definition, plugin_state=1.5)
    conn.commit()
    assert query.collection_health(
        definition, now=1000, daemon_stale=False
    )["state"] == "unavailable"
    _report(conn, definition, plugin_state=0, rules={"probe-health": []})
    conn.commit()
    assert query.collection_health(
        definition, now=1000, daemon_stale=False
    )["state"] == "unconfirmed"
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('external_observations',?)",
                 ("x" * 65537,))
    conn.commit()
    assert query.collection_health(
        definition, now=1000, daemon_stale=False
    )["state"] == "unavailable"
    short_interval = load_text(_MONITOR.replace('interval = "60s"', 'interval = "15s"'))
    _report(conn, short_interval, sampled_at=954)
    conn.commit()
    assert query.collection_health(
        short_interval, now=1000, daemon_stale=False
    )["state"] == "sample_stale"
    conn.close()
    disabled = _MONITOR.replace("enabled = true", "enabled = false")
    (paths.monitors_dir / "probe.toml").write_text(disabled)
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-collection-state="disabled"' in page


def test_declared_panels_remain_visible_before_first_observation_ec_11(tmp_path):
    """[EC-11] A fixed external entity has useful empty panel states before sampling."""
    client, paths, _definition = _site(tmp_path)
    conn = connect(paths.db_file)
    conn.execute("DELETE FROM entities WHERE monitor='probe'")
    conn.commit()
    conn.close()
    page = client.get(
        "/trends/probe/temperature", headers={"host": "localhost:8420"}
    ).text
    assert 'data-panel="value"' in page and 'data-panel="rate"' in page
    assert page.count("Never sampled, or retained history has expired") == 2
    assert "Collection evidence is not yet available" in page
    assert '<div class="trend-panel-empty" data-panel="value" role="status">' in page
    assert '<div class="trend-panel-empty" data-panel="rate" role="status">' in page
    assert '<div class="uplot-panel" data-panel="value"' not in page
    assert '<div class="uplot-panel" data-panel="rate"' not in page


def test_valid_warning_can_keep_authored_health_rule_true_ec_11(tmp_path):
    """[EC-11] A valid warning does not imply an authored broad rule is recovering."""
    text = _MONITOR.replace('when = "plugin_state == 3"',
                            'when = "plugin_state > 0"')
    client, paths, definition = _site(tmp_path, monitor_text=text)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=1,
            rules={"probe-health": "TRUE", "fan-low": "UNKNOWN"})
    conn.commit()
    evidence = Query(conn).collection_health(definition, now=1000, daemon_stale=False)
    assert evidence["state"] == "alerting"
    assert "remains TRUE" in evidence["reason"]
    conn.close()
    page = client.get("/", headers={"host": "localhost:8420"}).text
    assert 'data-collection-state="alerting"' in page
    assert 'data-state="clear"' in page


def test_unknown_health_rule_does_not_claim_recovery_ec_11(tmp_path):
    """[EC-11] A warmup UNKNOWN keeps a live collection incident unresolved."""
    _client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=0,
            rules={"probe-health": "UNKNOWN", "fan-low": "UNKNOWN"})
    conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
                 "opened_ts,last_change_ts,notify_count,occurrences) "
                 "VALUES(8,'probe','health','device','open',2,'probe-health',900,900,1,1)")
    conn.commit()
    evidence = Query(conn).collection_health(definition, now=1000, daemon_stale=False)
    assert evidence["state"] == "unconfirmed"
    assert "cannot clear" in evidence["reason"]
    assert evidence["incident"]["id"] == 8
    conn.close()


def test_historical_entity_does_not_inherit_current_check_failure_ec_11(tmp_path):
    """[EC-11] A bookmarked old entity keeps history without current entity health."""
    client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition)
    conn.commit()
    conn.close()
    data = client.get(
        "/api/trend?monitor=probe&profile=temperature&entity=old-device",
        headers={"host": "localhost:8420"},
    ).json()
    assert data["collection"] is None


def test_partial_current_panel_keeps_empty_declared_panel_ec_11(tmp_path):
    """[EC-11] A current value panel does not hide its empty companion."""
    client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=0)
    conn.execute("INSERT INTO series VALUES(1,'probe','device','temperature',1)")
    conn.execute("INSERT INTO rollup5m VALUES(1,900,35,35,35,35,1)")
    conn.commit()
    conn.close()
    data = client.get(
        "/api/trend?monitor=probe&profile=temperature&entity=device&range=24h",
        headers={"host": "localhost:8420"},
    ).json()
    assert data["panels"]["value"]["points"]
    assert data["panels"]["rate"]["points"] == []
    assert data["panel_evidence"]["value"]["has_points_in_range"] is True
    assert data["panel_evidence"]["rate"]["has_points_in_range"] is False
    page = client.get(
        "/trends/probe/temperature?entity=device&range=24h",
        headers={"host": "localhost:8420"},
    ).text
    assert '<div class="uplot-panel" data-panel="value"' in page
    assert '<div class="trend-panel-empty" data-panel="rate" role="status">' in page
    assert "No observations in the selected range for fan." in page
    assert '<div class="uplot-panel" data-panel="rate"' not in page


def test_old_rollup_summary_is_not_labeled_current_when_raw_is_fresh_ec_11(tmp_path):
    """[EC-11] A recent raw sample cannot relabel a selected hourly history value."""
    client, paths, _definition = _site(tmp_path)
    conn = connect(paths.db_file)
    conn.execute("INSERT INTO series VALUES(1,'probe','device','temperature',1)")
    conn.execute("INSERT INTO samples VALUES(1,1000,50)")
    conn.execute("INSERT INTO rollup1h VALUES(1,?,30,30,30,30,1)",
                 (1000 - 40 * 86400,))
    conn.commit()
    conn.close()
    page = client.get(
        "/metrics?monitor=probe&entity=device&metric=temperature&range=400d",
        headers={"host": "localhost:8420"},
    ).text
    assert "Latest value in selected range 30" in page
    assert "Current 30" not in page


def test_incident_links_related_collection_and_explains_derived_warmup_ec_11(tmp_path):
    """[EC-11] A partial measurement links the health incident and explains UNKNOWN."""
    derived = '''[[derived]]
name = "temp_rate"
expr = 'slope(temperature, "2h") * 3600'
'''
    rule = '''[[rule]]
id = "temp-rise"
group = "thermal-rise"
when = "temp_rate > 5"
severity = "warning"
confirm_cycles = 2
clear_cycles = 2
message = "Temperature rising"
'''
    text = _MONITOR.replace('[[rule]]\nid = "probe-health"',
                            derived + '[[rule]]\nid = "probe-health"') + rule
    client, paths, definition = _site(tmp_path, monitor_text=text)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=3,
            rules={"probe-health": "TRUE", "fan-low": "UNKNOWN",
                   "temp-rise": "UNKNOWN"})
    for iid, group, owner in ((10, "thermal-rise", "temp-rise"),
                              (11, "health", "probe-health")):
        conn.execute("INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,"
                     "owning_rule,opened_ts,last_change_ts,notify_count,occurrences) "
                     "VALUES(?,'probe',?,'device','open',2,?,900,900,1,1)",
                     (iid, group, owner))
    conn.commit()
    conn.close()
    page = client.get("/incidents/10", headers={"host": "localhost:8420"}).text
    assert 'href="/incidents/11"' in page
    assert "Missing current metrics: temp_rate" in page
    assert "Derived windows or baselines may need fresh evidence" in page
    assert "does not count as recovery" in page
    conn = connect(paths.db_file)
    conn.execute("UPDATE incidents SET state='cleared',cleared_ts=999 WHERE id=10")
    conn.commit()
    conn.close()
    cleared = client.get("/incidents/10", headers={"host": "localhost:8420"}).text
    assert "does not count as recovery" not in cleared


def test_capped_availability_does_not_claim_an_omitted_metric_is_missing_ec_11(tmp_path):
    """[EC-11] Report truncation is incomplete evidence, not a failed sensor."""
    _client, paths, definition = _site(tmp_path)
    conn = connect(paths.db_file)
    _report(conn, definition, plugin_state=0)
    row = conn.execute("SELECT value FROM meta WHERE key='external_observations'").fetchone()
    report = json.loads(row[0])
    report["monitors"]["probe"]["metrics_truncated"] = True
    conn.execute("UPDATE meta SET value=? WHERE key='external_observations'",
                 (json.dumps(report),))
    evidence = Query(conn).collection_health(definition, now=1000, daemon_stale=False)
    assert evidence["rule_details"][1]["evaluation"] == "UNKNOWN"
    assert evidence["rule_details"][1]["missing_metrics"] == []
    assert "report limit" in evidence["reason"]
    conn.close()

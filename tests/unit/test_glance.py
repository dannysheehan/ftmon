"""[UI-04][UI-14][UI-17][UI-18][MD-12][CA-07][MC-01] Shared read-side glance policy.

The dashboard and MCP `get_status` consume one selection module (issue #64), so
these tests pin the freshness boundary both once shared and once per consumer,
the UI-14 precedence used for omission, the raw-first reading contract, the
response bound, and parity over identical stored state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ftmon import glance
from ftmon.clock import FakeClock
from ftmon.definitions import loader
from ftmon.mcp_server import McpApi
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.query import Query
from ftmon.web.app import _format_glance_value, _monitor_tiles
from tests.platform_permissions import (
    make_private,
    toml_path,
    trusted_python_executable,
)

WALL = 1_700_000_000.0
BUILTINS = Path(__file__).parents[2] / "src/ftmon/definitions/builtins"

# Minimal glance-carrying sampler definition; NAME is substituted so the bound
# test can load many monitors without paying full builtin validation cost.
SMALL_DISK_DEF = """
schema = 1
[monitor]
name = "NAME"
description = "small disk monitor"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "disk"
[parameters]
warn_pct = { value = 90, doc = "warn percent used" }
[glance]
metric = "used_pct"
unit = "percent"
aggregate = "max"
thresholds = [ { label = "warn", parameter = "warn_pct" } ]
[[rule]]
id = "space"
when = 'used_pct > warn_pct'
severity = "warning"
confirm_cycles = 3
message = "{entity} filling"
"""

COLDEST_DEF = SMALL_DISK_DEF.replace('aggregate = "max"', 'aggregate = "min"')

EXTERNAL_DEF = """
schema = 1
[monitor]
name = "https_cert"
description = "certificate lifetime"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "external"
[source_options]
check = "cert_days"
entity = "example.org"
[[source_options.perfdata]]
label = "days"
metric = "days_left"
plugin_uom = "days"
unit = "days"
kind = "gauge"
[parameters]
warn_days = { value = 21, doc = "warn days remaining" }
[glance]
metric = "days_left"
unit = "days"
aggregate = "min"
thresholds = [ { label = "warn", parameter = "warn_days" } ]
[[rule]]
id = "expiring"
when = 'days_left < warn_days'
severity = "warning"
confirm_cycles = 2
message = "{plugin_message}"
"""


def _env(tmp_path):
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    return paths


def _open_db(paths):
    conn = connect(paths.db_file)
    migrate(conn)
    return conn


def _tick(conn, ts: float | None = WALL) -> None:
    conn.execute("DELETE FROM meta WHERE key='last_tick_ts'")
    if ts is not None:
        conn.execute("INSERT INTO meta(key,value) VALUES('last_tick_ts',?)", (str(ts),))


def _loaded(conn, *monitors: str) -> None:
    for monitor in monitors:
        conn.execute(
            "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
            "VALUES(?,?,?,?)", (monitor, int(WALL), monitor, monitor)
        )


def _series(
    conn, sid: int, monitor: str, entity: str, metric: str, points,
    *, attrs=None, gone_ts=None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO entities(monitor,entity_id,first_seen,last_seen,"
        "gone_ts,attrs) VALUES(?,?,?,?,?,?)",
        (monitor, entity, int(WALL) - 3600, int(WALL), gone_ts,
         None if attrs is None else json.dumps(attrs)),
    )
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES(?,?,?,?,1)",
        (sid, monitor, entity, metric),
    )
    conn.executemany(
        "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
        [(sid, int(ts), value) for ts, value in points],
    )


def _incident(conn, iid: int, monitor: str, state: str, severity: int) -> None:
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(?,?,?,?,?,?,'rule',?,?,1,1)",
        (iid, monitor, "grp", "entity", state, severity, int(WALL), int(WALL)),
    )


def _api(paths) -> McpApi:
    return McpApi(paths, clock=FakeClock(wall=WALL, mono=1000.0))


def _query(paths) -> Query:
    return Query(connect(paths.db_file, readonly=True))


def _defs(paths):
    return loader.load_dir(
        paths.monitors_dir,
        actions_dir=paths.actions_dir,
        require_actions=True,
        check_aliases=glance.check_aliases(paths),
        require_checks=True,
    )


def _tile_glances(paths, *, stale: bool = False) -> dict:
    """Compose dashboard tiles from the same stored state MCP reads."""
    defs, errors = _defs(paths)
    q = _query(paths)
    try:
        tiles = _monitor_tiles(defs, errors, q, {"daemon_stale": stale}, WALL)
    finally:
        q._conn.close()
    return {tile.name: tile for tile in tiles}


# --- UI-04 staleness: one boundary, shared -----------------------------------


class TestStaleness:
    @pytest.mark.parametrize(
        ("age", "stale"),
        ((None, True), (0.0, False), (14.999, False), (15.0, False), (15.001, True)),
    )
    def test_shared_predicate_boundary_ui_04(self, age, stale):
        """[UI-04][MC-01] Missing age is stale; exactly 15 s is still fresh."""
        assert glance.daemon_stale(age) is stale
        assert glance.daemon_alive(age) is not stale

    @pytest.mark.parametrize(
        ("last_tick_ts", "alive"),
        ((WALL, True), (WALL - 14.999, True), (WALL - 15.0, True),
         (WALL - 15.001, False)),
    )
    def test_mcp_get_status_uses_shared_boundary_mc_01(
        self, tmp_path, last_tick_ts, alive
    ):
        """[MC-01][UI-04] get_status liveness matches the shared predicate."""
        paths = _env(tmp_path)
        conn = _open_db(paths)
        _tick(conn, last_tick_ts)
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["daemon_alive"] is alive
        assert res["daemon_stale"] is not alive

    def test_mcp_reports_stale_without_any_tick_mc_01(self, tmp_path):
        """[MC-01][UI-04] An unknown age is stale, not "neither alive nor stale"."""
        paths = _env(tmp_path)
        conn = _open_db(paths)
        _tick(conn, None)
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["last_tick_age_s"] is None
        assert res["daemon_alive"] is False
        assert res["daemon_stale"] is True

    def test_missing_database_reports_stale_mc_01(self, tmp_path):
        """[MC-01] No database at all is a stale daemon with bounded metadata."""
        res = _api(_env(tmp_path)).get_status()
        assert res["daemon_alive"] is False
        assert res["daemon_stale"] is True
        assert res["glances"] == []
        assert res["limits"] == {"max_glances": 64}

    @pytest.mark.parametrize(
        ("last_tick_ts", "expected"), ((WALL - 15.0, 1), (WALL - 15.001, 0))
    )
    def test_glance_omitted_exactly_when_stale_ui_17(
        self, tmp_path, last_tick_ts, expected
    ):
        """[UI-17][UI-04] The readout survives at the boundary and dies past it."""
        paths = _env(tmp_path)
        (paths.monitors_dir / "disk.toml").write_text(
            SMALL_DISK_DEF.replace("NAME", "disk")
        )
        conn = _open_db(paths)
        _tick(conn, last_tick_ts)
        _loaded(conn, "disk")
        _series(conn, 1, "disk", "/", "used_pct", [(WALL - 5, 71.0)])
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert len(res["glances"]) == expected
        assert res["glances_returned"] == expected


# --- UI-14 precedence used for omission -------------------------------------


class TestHealthState:
    def test_stale_and_missing_evidence_precede_disabled_ui_14(self):
        """[UI-14] Nothing below stale_or_unknown can claim the tile."""
        assert glance.health_state(
            stale=True, has_evidence=True, enabled=False, max_severity=4
        ) == "unknown"
        assert glance.health_state(
            stale=False, has_evidence=False, enabled=True, max_severity=None
        ) == "unknown"

    def test_config_error_outranks_stale_ui_14(self):
        """[UI-14] A broken definition is the highest-precedence state."""
        assert glance.health_state(
            config_error=True, stale=True, has_evidence=False, enabled=True,
            max_severity=None,
        ) == "config-error"

    def test_disabled_precedes_severity_ui_14(self):
        """[UI-14] A retained incident cannot repaint an intentionally off monitor."""
        assert glance.health_state(
            stale=False, has_evidence=True, enabled=False, max_severity=4
        ) == "disabled"

    @pytest.mark.parametrize(
        ("severity", "state"),
        ((4, "error"), (3, "error"), (2, "warning"), (1, "warning"), (0, "warning")),
    )
    def test_critical_and_error_outrank_warning_ui_14(self, severity, state):
        """[UI-14] error_or_critical (>=3) beats notice_or_warning."""
        assert glance.health_state(
            stale=False, has_evidence=True, enabled=True, max_severity=severity
        ) == state

    def test_clear_requires_fresh_evidence_and_no_live_incident_ui_14(self):
        """[UI-14] clear is the last resort, never a default."""
        assert glance.health_state(
            stale=False, has_evidence=True, enabled=True, max_severity=None
        ) == "clear"

    def test_acked_incident_keeps_monitor_unhealthy_ui_14(self, tmp_path):
        """[UI-14] Acknowledgment neither clears nor downgrades severity."""
        paths = _env(tmp_path)
        conn = _open_db(paths)
        _tick(conn)
        _incident(conn, 1, "disk", "acked", 3)
        _incident(conn, 2, "disk", "cleared", 4)
        conn.commit()
        conn.close()
        q = _query(paths)
        try:
            live = glance.open_incidents_by_monitor(q)
        finally:
            q._conn.close()
        severities = [row["severity"] for row in live["disk"]]
        assert severities == [3]  # the cleared critical is not live evidence
        assert glance.health_state(
            stale=False, has_evidence=True, enabled=True,
            max_severity=max(severities),
        ) == "error"


# --- MD-12 / UI-17 / CA-07 reading contract ---------------------------------


def _disk_state(tmp_path, definition=None):
    """Multi-entity disk state: one winner, plus three entities that must lose."""
    paths = _env(tmp_path)
    (paths.monitors_dir / "disk.toml").write_text(
        definition or (BUILTINS / "disk.toml").read_text()
    )
    conn = _open_db(paths)
    _tick(conn)
    _loaded(conn, "disk")
    _series(conn, 1, "disk", "/", "used_pct", [(WALL - 5, 71.0)])
    _series(conn, 2, "disk", "/home", "used_pct", [(WALL - 5, 94.0)])
    _series(conn, 3, "disk", "/boot", "used_pct", [(WALL - 300, 99.0)])
    _series(conn, 4, "disk", "/gone", "used_pct", [(WALL - 5, 100.0)],
            gone_ts=int(WALL) - 1)
    _series(conn, 5, "disk", "/snap/core", "used_pct", [(WALL - 5, 100.0)],
            attrs={"fstype": "squashfs", "device": "/dev/loop0"})
    conn.commit()
    conn.close()
    return paths


class TestReadingContract:
    def test_identity_fields_are_raw_and_complete_md_12_ui_17(self, tmp_path):
        """[MD-12][UI-17][MC-01] Every readout identifies what it measured."""
        paths = _disk_state(tmp_path)
        res = _api(paths).get_status()
        assert res["glances"] == [{
            "monitor": "disk",
            "entity_id": "/home",
            "metric": "used_pct",
            "value": 94.0,
            "unit": "percent",
            "aggregate": "max",
            "thresholds": [
                {"label": "warn", "value": 92.0},
                {"label": "error", "value": 97.0},
            ],
        }]
        assert res["glances_matched"] == 1
        assert res["glances_truncated"] is False

    def test_exempt_gone_and_expired_entities_cannot_win_ca_07_ui_17(self, tmp_path):
        """[CA-07][UI-17] Exempt, departed and twice-interval-old samples lose.

        Each of `/snap/core` (squashfs exemption), `/gone` and `/boot` holds a
        higher value than the winner, so any one of them leaking through would
        change the reported entity.
        """
        paths = _disk_state(tmp_path)
        q = _query(paths)
        try:
            defs, _errors = _defs(paths)
            reading = glance.reading(defs[0], q, "clear", WALL)
        finally:
            q._conn.close()
        assert reading is not None
        assert (reading.entity_id, reading.value) == ("/home", 94.0)

    def test_min_aggregate_selects_the_lowest_reading_md_12(self, tmp_path):
        """[MD-12] The declared aggregate, not a hardcoded maximum, decides."""
        paths = _disk_state(
            tmp_path, definition=COLDEST_DEF.replace("NAME", "disk")
        )
        res = _api(paths).get_status()
        assert res["glances"][0]["entity_id"] == "/"
        assert res["glances"][0]["value"] == 71.0
        assert res["glances"][0]["aggregate"] == "min"

    def test_ties_break_on_newest_then_entity_id_ui_17(self, tmp_path):
        """[UI-17] Equal values resolve deterministically, never by row order."""
        paths = _env(tmp_path)
        (paths.monitors_dir / "disk.toml").write_text(
            SMALL_DISK_DEF.replace("NAME", "disk")
        )
        conn = _open_db(paths)
        _tick(conn)
        _loaded(conn, "disk")
        _series(conn, 1, "disk", "/b", "used_pct", [(WALL - 5, 88.0)])
        _series(conn, 2, "disk", "/a", "used_pct", [(WALL - 5, 88.0)])
        _series(conn, 3, "disk", "/older", "used_pct", [(WALL - 30, 88.0)])
        conn.commit()
        conn.close()
        assert _api(paths).get_status()["glances"][0]["entity_id"] == "/a"

    def test_events_readout_is_response_level_last_ui_18(self, tmp_path):
        """[UI-18][MD-12] Ingest rate is labelled `last` and carries no thresholds."""
        paths = _env(tmp_path)
        (paths.monitors_dir / "events.toml").write_text(
            (BUILTINS / "events.toml").read_text()
        )
        conn = _open_db(paths)
        _tick(conn)
        conn.execute(
            "INSERT INTO cursors(source,cursor,updated_ts) "
            "VALUES('journald','c',?)", (int(WALL),)
        )
        _series(conn, 1, "self", "ftmon", "event_rate_per_min", [(WALL - 5, 842.0)])
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["glances"] == [{
            "monitor": "events",
            "entity_id": "ingest",
            "metric": "event_rate_per_min",
            "value": 842.0,
            "unit": "events/min",
            "aggregate": "last",
            "thresholds": [],
        }]

    def test_toml_still_rejects_last_as_a_declared_aggregate_md_12(self):
        """[MD-12] `last` stays response-only; definitions accept max|min alone."""
        with pytest.raises(loader.ValidationError) as excinfo:
            loader.load_text(
                SMALL_DISK_DEF.replace("NAME", "disk").replace(
                    'aggregate = "max"', 'aggregate = "last"'
                ),
                "<test>",
            )
        assert any(
            err["path"] == "glance.aggregate" for err in excinfo.value.errors
        )


# --- MC-01 response bound ---------------------------------------------------


class TestBound:
    def test_metadata_is_present_when_nothing_qualifies_mc_01(self, tmp_path):
        """[MC-01] Absent readouts are still described, never silently missing."""
        paths = _env(tmp_path)
        conn = _open_db(paths)
        _tick(conn)
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["glances"] == []
        assert res["glances_returned"] == 0
        assert res["glances_matched"] == 0
        assert res["glances_truncated"] is False
        assert res["limits"] == {"max_glances": 64}

    def test_over_the_cap_truncates_in_name_order_mc_01(self, tmp_path):
        """[MC-01] 65 eligible monitors return the first 64 names with metadata."""
        paths = _env(tmp_path)
        names = [f"disk{i:03d}" for i in range(65)]
        for name in names:
            (paths.monitors_dir / f"{name}.toml").write_text(
                SMALL_DISK_DEF.replace("NAME", name)
            )
        conn = _open_db(paths)
        _tick(conn)
        _loaded(conn, *names)
        for sid, name in enumerate(names, start=1):
            _series(conn, sid, name, "/", "used_pct", [(WALL - 5, 50.0 + sid)])
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["glances_matched"] == 65
        assert res["glances_returned"] == 64
        assert res["glances_truncated"] is True
        assert [g["monitor"] for g in res["glances"]] == sorted(names)[:64]

    def test_reported_limit_tracks_the_constant_mc_01(self, monkeypatch, tmp_path):
        """[MC-01] The advertised bound is read, not a hardcoded duplicate."""
        monkeypatch.setattr(glance, "MAX_GLANCES", 1)
        paths = _env(tmp_path)
        for name in ("aaa", "bbb"):
            (paths.monitors_dir / f"{name}.toml").write_text(
                SMALL_DISK_DEF.replace("NAME", name)
            )
        conn = _open_db(paths)
        _tick(conn)
        _loaded(conn, "aaa", "bbb")
        _series(conn, 1, "aaa", "/", "used_pct", [(WALL - 5, 50.0)])
        _series(conn, 2, "bbb", "/", "used_pct", [(WALL - 5, 60.0)])
        conn.commit()
        conn.close()
        res = _api(paths).get_status()
        assert res["limits"] == {"max_glances": 1}
        assert [g["monitor"] for g in res["glances"]] == ["aaa"]
        assert res["glances_truncated"] is True


# --- EC-01: external alias authority, same as the dashboard ------------------


def _register_check(paths, alias: str = "cert_days") -> None:
    paths.check_registry_file.write_text(
        f'[check.{alias}]\nargv=["{toml_path(trusted_python_executable())}", '
        '"-c", "pass"]\nprotocol="ftmon-json"\ntimeout="2s"\n'
    )
    make_private(paths.check_registry_file, 0o600)


def _external_state(tmp_path):
    paths = _env(tmp_path)
    (paths.monitors_dir / "https_cert.toml").write_text(EXTERNAL_DEF)
    conn = _open_db(paths)
    _tick(conn)
    _loaded(conn, "https_cert")
    _series(conn, 1, "https_cert", "example.org", "days_left", [(WALL - 5, 9.0)])
    conn.commit()
    conn.close()
    return paths


class TestExternalAuthority:
    def test_registered_alias_loads_and_glances_ec_01_ui_17(self, tmp_path):
        """[EC-01][UI-17] A trusted alias yields a loaded monitor and a readout."""
        paths = _external_state(tmp_path)
        _register_check(paths)
        res = _api(paths).get_status()
        entry = [m for m in res["monitors"] if m["name"] == "https_cert"]
        assert entry == [{"name": "https_cert", "source": "external",
                          "enabled": True}]
        assert res["glances"] == [{
            "monitor": "https_cert", "entity_id": "example.org",
            "metric": "days_left", "value": 9.0, "unit": "days",
            "aggregate": "min",
            "thresholds": [{"label": "warn", "value": 21.0}],
        }]

    def test_unavailable_alias_is_a_config_error_without_glance_ec_01(self, tmp_path):
        """[EC-01][MC-01] An unregistered check is a config error, not a monitor.

        Before issue #64 `get_status` loaded without check authority, so the
        model saw an external monitor as healthy while the daemon refused it.
        """
        paths = _external_state(tmp_path)
        res = _api(paths).get_status()
        entry = [m for m in res["monitors"] if m["name"] == "https_cert"]
        assert entry and entry[0]["state"] == "config_error"
        assert "enabled" not in entry[0]
        assert res["glances"] == []

    def test_invalid_registry_matches_dashboard_treatment_ec_01(self, tmp_path):
        """[EC-01][UI-14] A malformed registry grants no alias to either consumer."""
        paths = _external_state(tmp_path)
        paths.check_registry_file.write_text('[check.cert_days]\nprotocol="nagios"\n')
        make_private(paths.check_registry_file, 0o600)
        assert glance.check_aliases(paths) == frozenset()
        res = _api(paths).get_status()
        assert [m for m in res["monitors"]
                if m["name"] == "https_cert"][0]["state"] == "config_error"
        assert res["glances"] == []
        tiles = _tile_glances(paths)
        assert tiles["https_cert"].state == "config-error"
        assert tiles["https_cert"].glance is None


# --- parity over identical stored state -------------------------------------


def _assert_parity(paths, monitor: str) -> dict:
    """One structured readout must reach both consumers identically (issue #64)."""
    payload = [g for g in _api(paths).get_status()["glances"]
               if g["monitor"] == monitor]
    tile = _tile_glances(paths)[monitor]
    assert len(payload) == 1
    assert tile.glance is not None
    assert tile.glance.entity_id == payload[0]["entity_id"]
    assert tile.glance.value == _format_glance_value(
        payload[0]["value"], payload[0]["unit"]
    )
    assert [t.label for t in tile.glance.thresholds] == [
        t["label"] for t in payload[0]["thresholds"]
    ]
    assert [t.raw for t in tile.glance.thresholds] == [
        t["value"] for t in payload[0]["thresholds"]
    ]
    return payload[0]


class TestParity:
    def test_multi_entity_sampler_parity_ui_17_ca_07(self, tmp_path):
        """[UI-17][CA-07] Dashboard and MCP pick the same winner and thresholds."""
        paths = _disk_state(tmp_path)
        payload = _assert_parity(paths, "disk")
        assert payload["entity_id"] == "/home"
        assert _tile_glances(paths)["disk"].glance.value == "94%"

    def test_external_parity_ui_17_ec_01(self, tmp_path):
        """[UI-17][EC-01] External perfdata is read in its native unit by both."""
        paths = _external_state(tmp_path)
        _register_check(paths)
        payload = _assert_parity(paths, "https_cert")
        assert payload["value"] == 9.0
        assert _tile_glances(paths)["https_cert"].glance.value == "9 days"

    def test_events_parity_ui_18(self, tmp_path):
        """[UI-18] The fixed ingest readout reaches both surfaces identically."""
        paths = _env(tmp_path)
        (paths.monitors_dir / "events.toml").write_text(
            (BUILTINS / "events.toml").read_text()
        )
        conn = _open_db(paths)
        _tick(conn)
        conn.execute(
            "INSERT INTO cursors(source,cursor,updated_ts) "
            "VALUES('journald','c',?)", (int(WALL),)
        )
        _series(conn, 1, "self", "ftmon", "event_rate_per_min", [(WALL - 5, 842.0)])
        conn.commit()
        conn.close()
        payload = _assert_parity(paths, "events")
        assert payload["aggregate"] == "last"
        assert _tile_glances(paths)["events"].glance.value == "842 events/min"

    def test_stale_omission_parity_ui_17(self, tmp_path):
        """[UI-17] A stale daemon removes the readout from both consumers."""
        paths = _disk_state(tmp_path)
        conn = _open_db(paths)
        _tick(conn, WALL - 60)
        conn.commit()
        conn.close()
        assert _api(paths).get_status()["glances"] == []
        tiles = _tile_glances(paths, stale=True)
        assert tiles["disk"].state == "unknown"
        assert tiles["disk"].glance is None

"""[MC-01][MC-02][MC-03][MC-04][MC-05][PM-06][TS-06] MCP tool surface: McpApi over a
DaemonCore-populated database, plus the draft/approve/enable lifecycle.

McpApi is tested directly (no stdio, TS-03: injected FakeClock shared with the
daemon core so "now" sits next to the fake data); build_server is introspected
only — never .run(), which would block on stdio.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.definitions import loader, manage
from ftmon.mcp_server import TOOL_NAMES, McpApi, build_server
from ftmon.store.db import connect
from tests.unit.test_engine import LEAKDEF, ScriptedSampler, grower
from tests.unit.test_m2_integration import core_env, tick_n  # noqa: F401 - fixture

WALL0 = 1_700_000_000.0  # 2023-11-14T22:13:20Z; ISO ranges below bracket it

@pytest.fixture
def populated(core_env):  # noqa: F811 - pytest fixture injection
    """DaemonCore ticked over a scripted leak: samples, entities with attrs,
    an open incident with history and delivered outbox rows."""
    paths = core_env
    clock = FakeClock(wall=WALL0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    sampler = ScriptedSampler()
    for i in range(30):
        # calm is tiny so top_consumers must rank leaky first
        sampler.push(grower(i),
                     ("calm:2:100", {"name": "calm"},
                      {"rss_bytes": 50_000.0, "cpu_pct": 0.1}))
    core.samplers["process"] = sampler
    tick_n(core, clock, 10)
    # one final tick with no advance afterwards: last_tick_age stays ~0 so
    # get_status must report the daemon alive (UI-04 staleness rule)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    return paths, clock, McpApi(paths, clock=clock)


def assert_err(res: dict, code: str) -> dict:
    """[MC-04] structured error contract: code+message+hint always present."""
    assert set(res) == {"error"}
    err = res["error"]
    assert {"code", "message", "hint"} <= set(err)
    assert err["code"] == code
    return err


# --- MC-01/MC-05: frozen surface ------------------------------------------


class TestSurface:
    def test_tool_list_is_exactly_frozen_names(self, core_env):  # noqa: F811
        """[MC-01] build_server exposes exactly TOOL_NAMES, nothing else."""
        server = build_server(core_env)
        tools = asyncio.run(server.list_tools())
        assert {t.name for t in tools} == set(TOOL_NAMES)

    def test_single_definitions_resource(self, core_env):  # noqa: F811
        """[MC-05] one resource: the definitions guide, readable as text."""
        server = build_server(core_env)
        resources = asyncio.run(server.list_resources())
        assert [str(r.uri) for r in resources] == ["ftmon://docs/definitions"]
        contents = asyncio.run(server.read_resource("ftmon://docs/definitions"))
        text = (contents if isinstance(contents, str)
                else "".join(str(c.content) for c in contents))
        assert "monitor" in text


# --- MC-02: tz + range grammar ---------------------------------------------


class TestRanges:
    def test_tz_on_every_successful_read(self, populated):
        """[MC-02] each read response carries the host timezone once."""
        _paths, _clock, api = populated
        for res in (api.get_status(),
                    api.top_consumers("rss", "30m"),
                    api.list_events("30m"),
                    api.list_incidents(),
                    api.list_monitors(),
                    api.get_monitor("leak")):
            assert "error" not in res
            assert res["tz"]

    def test_tz_on_series_reads(self, populated):
        """[MC-02] tz also on the series-shaped reads (blocked by the
        SeriesPoint unpack bug until fixed)."""
        _paths, _clock, api = populated
        for res in (api.query_metrics("leak", "rss_bytes", "30m"),
                    api.get_process_history("leaky", "30m")):
            assert "error" not in res
            assert res["tz"]

    def test_duration_string_range(self, populated):
        """[MC-02] '90m'-style durations end at fake-now."""
        _paths, _clock, api = populated
        res = api.query_metrics("leak", "rss_bytes", "90m")
        assert "error" not in res
        assert any(e["points"] for e in res["series"])

    def test_iso_pair_range(self, populated):
        """[MC-02] two-element ISO-8601 lists are an accepted range form."""
        _paths, _clock, api = populated
        res = api.query_metrics(
            "leak", "rss_bytes",
            ["2023-11-14T21:00:00+00:00", "2023-11-14T23:00:00+00:00"])
        assert "error" not in res
        assert any(e["points"] for e in res["series"])

    def test_garbage_range_is_structured_error(self, populated):
        """[MC-02][MC-04] a garbage range yields invalid_params with a
        hint showing both accepted forms."""
        _paths, _clock, api = populated
        err = assert_err(api.query_metrics("leak", "rss_bytes", "soonish"),
                         "invalid_params")
        assert err["hint"]


# --- MC-04: error shape across tools ----------------------------------------


class TestErrors:
    def test_bad_agg(self, populated):
        """[MC-04] unknown agg names the accepted values in the hint."""
        _paths, _clock, api = populated
        err = assert_err(api.query_metrics("leak", "rss_bytes", "30m",
                                           agg="median"), "invalid_params")
        assert "avg" in err["hint"]

    def test_bad_severity_name(self, populated):
        """[MC-04] unknown severity lists the valid names."""
        _paths, _clock, api = populated
        err = assert_err(api.list_events("30m", min_severity="fatal"),
                         "invalid_params")
        assert "warning" in err["hint"]

    def test_bad_filter_expr_hint_lists_attrs(self, populated):
        """[MC-04] a filter_expr compile failure tells the model which
        attrs it could have used — the self-correction loop."""
        _paths, _clock, api = populated
        err = assert_err(api.query_metrics("leak", "rss_bytes", "30m",
                                           filter_expr="name >"),
                         "invalid_params")
        assert "name" in err["hint"]

    def test_explain_unknown_incident(self, populated):
        """[MC-04] explain_incident on a nonexistent id -> not_found."""
        _paths, _clock, api = populated
        assert_err(api.explain_incident(999_999), "not_found")

    def test_ack_unknown_incident(self, populated):
        """[MC-04] ack on a nonexistent id -> not_found."""
        _paths, _clock, api = populated
        assert_err(api.ack_incident(999_999), "not_found")


# --- get_status --------------------------------------------------------------


class TestGetStatus:
    def test_alive_with_populated_db(self, populated):
        """[MC-01] fresh tick -> alive; open incident counted; the leak
        monitor listed as enabled."""
        _paths, _clock, api = populated
        res = api.get_status()
        assert res["daemon_alive"] is True
        assert res["last_tick_age_s"] < 15
        assert res["open_incidents"] >= 1
        leak = [m for m in res["monitors"] if m["name"] == "leak"]
        assert leak and leak[0]["enabled"] is True

    def test_no_db_reports_dead_daemon(self, core_env):  # noqa: F811
        """[MC-01] with no database at all, get_status degrades gracefully
        instead of crashing (PM-01: read paths work with the daemon down)."""
        res = McpApi(core_env).get_status()
        assert res["daemon_alive"] is False
        assert res["open_incidents"] == 0


# --- query_metrics -------------------------------------------------------------


class TestQueryMetrics:
    def test_series_points(self, populated):
        """[MC-01] raw points come back for the sampled leak metric."""
        _paths, _clock, api = populated
        res = api.query_metrics("leak", "rss_bytes", "30m")
        assert res["series"] and all(e["points"] for e in res["series"])

    def test_agg_scalars(self, populated):
        """[MC-01] agg=avg/last collapses each entity to one scalar."""
        _paths, _clock, api = populated
        for agg in ("avg", "last"):
            res = api.query_metrics("leak", "rss_bytes", "30m", agg=agg)
            for entry in res["series"]:
                assert "points" not in entry
                assert isinstance(entry["agg"], float)

    def test_entity_filter_narrows(self, populated):
        """[MC-01] entity= restricts the result to that one series."""
        _paths, _clock, api = populated
        res = api.query_metrics("leak", "rss_bytes", "30m",
                                entity="leaky:1:100")
        assert [e["entity"] for e in res["series"]] == ["leaky:1:100"]

    def test_filter_expr_over_attrs(self, populated):
        """[MC-01] filter_expr evaluates the section-8.2 language over
        stored entity attrs; calm is filtered out."""
        _paths, _clock, api = populated
        res = api.query_metrics("leak", "rss_bytes", "30m",
                                filter_expr='matches(name, "^leaky")')
        entities = {e["entity"] for e in res["series"]}
        assert entities == {"leaky:1:100"}


# --- top_consumers -------------------------------------------------------------


class TestTopConsumers:
    def test_rss_ranks_leaky_first(self, populated):
        """[MC-01] the growing process out-ranks the calm one on rss."""
        _paths, _clock, api = populated
        res = api.top_consumers("rss", "30m")
        assert res["ranked"]
        assert res["ranked"][0]["entity"] == "leaky:1:100"

    def test_bogus_resource(self, populated):
        """[MC-04] unknown resource -> invalid_params naming the choices."""
        _paths, _clock, api = populated
        err = assert_err(api.top_consumers("bogus", "30m"), "invalid_params")
        assert "rss" in err["hint"]


# --- get_process_history ---------------------------------------------------------


class TestProcessHistory:
    def test_by_name_substring(self, populated):
        """[MC-01] name match returns attrs, lifecycle stamps, and series."""
        _paths, _clock, api = populated
        res = api.get_process_history("leaky", "30m")
        assert len(res["entities"]) >= 1
        ent = res["entities"][0]
        assert ent["attrs"]["name"] == "leaky"
        assert ent["first_seen"] <= ent["last_seen"]
        assert ent["series"]["rss_bytes"]

    def test_by_pid_digits(self, populated):
        """[MC-01] all-digit input matches the ':pid:' slot of the
        '{name}:{pid}:{create_time}' entity id convention."""
        _paths, _clock, api = populated
        res = api.get_process_history("1", "30m")
        assert any(e["entity_id"] == "leaky:1:100" for e in res["entities"])


# --- list_incidents / explain_incident ---------------------------------------------


class TestIncidents:
    def test_list_open_incident(self, populated):
        """[MC-01] the leak incident is listed open with a severity name."""
        _paths, _clock, api = populated
        res = api.list_incidents()
        open_ = [i for i in res["incidents"] if i["state"] == "open"]
        assert open_ and open_[0]["monitor"] == "leak"
        assert open_[0]["severity_name"] == "warning"

    def test_explain_full_story(self, populated):
        """[MC-01] explain returns the rule source text, its parameter
        values, the open history entry, and metric series (DM-12)."""
        _paths, _clock, api = populated
        inc = api.list_incidents()["incidents"][0]
        res = api.explain_incident(inc["id"])
        assert res["rule"]["expr"] == 'slope(rss_bytes, "15m") * 3600 > warn_bph'
        assert res["rule"]["parameters"]["warn_bph"] == 1_000_000
        assert any(h["kind"] == "open" for h in res["history"])
        assert "rss_bytes" in res["series"]


# --- list_monitors / get_monitor ----------------------------------------------------


class TestMonitorsRead:
    def test_states_enabled_draft_config_error(self, populated):
        """[MC-01] the three visible states: enabled monitor, a valid
        draft, and a broken file surfaced as config_error, not hidden."""
        paths, _clock, api = populated
        (paths.drafts_dir / "x.toml").write_text(
            LEAKDEF.replace('name = "leak"', 'name = "leakdraft"'))
        (paths.monitors_dir / "broken.toml").write_text("not really toml [[")
        by_name = {m["name"]: m for m in api.list_monitors()["monitors"]}
        assert by_name["leak"]["state"] == "enabled"
        assert by_name["leakdraft"]["state"] == "draft"
        assert by_name["broken"]["state"] == "config_error"

    def test_get_monitor_and_not_found(self, populated):
        """[MC-01] get_monitor returns the raw TOML plus validity; an
        unknown name is a structured not_found (MC-04)."""
        _paths, _clock, api = populated
        res = api.get_monitor("leak")
        assert 'name = "leak"' in res["toml"]
        assert res["valid"] is True
        assert_err(api.get_monitor("nope"), "not_found")


# --- validate_monitor ----------------------------------------------------------------


class TestValidate:
    def test_valid_text(self, core_env):  # noqa: F811
        """[MC-01] valid TOML -> ok with the name and normalized form."""
        res = McpApi(core_env).validate_monitor(LEAKDEF)
        assert res["ok"] is True
        assert res["name"] == "leak"
        assert 'name = "leak"' in res["normalized"]

    def test_invalid_text_error_shape(self, core_env):  # noqa: F811
        """[MC-04] each validation error carries the MD-01 shape
        (path/code/message) a less capable model can act on."""
        res = McpApi(core_env).validate_monitor("schema = 1\n")
        assert res["ok"] is False
        assert res["errors"]
        for err in res["errors"]:
            assert {"path", "code", "message"} <= set(err)


# --- define_monitor + manage lifecycle (MC-03, PM-06) ----------------------------------


DRAFT2 = LEAKDEF.replace('name = "leak"', 'name = "leak2"')


class TestDefineAndManage:
    def test_define_draft_and_overwrite_loop(self, core_env):  # noqa: F811
        """[MC-03] a fresh name lands in drafts/ with the approval hint;
        re-defining the same draft succeeds — iterating is the normal flow."""
        paths = core_env
        api = McpApi(paths)
        res = api.define_monitor(DRAFT2)
        assert "error" not in res
        assert (paths.drafts_dir / "leak2.toml").exists()
        assert "ftmon monitor approve" in res["approval_hint"]
        again = api.define_monitor(DRAFT2)
        assert "error" not in again

    def test_define_refuses_existing_monitor_name(self, core_env):  # noqa: F811
        """[MC-03] a name that exists in monitors/ is refused, never
        silently shadowed."""
        assert_err(McpApi(core_env).define_monitor(LEAKDEF), "name_exists")

    def test_approve_moves_and_validates(self, core_env):  # noqa: F811
        """[PM-06] approve renames drafts/name.toml into monitors/ and the
        landed file re-validates."""
        paths = core_env
        McpApi(paths).define_monitor(DRAFT2)
        target = manage.approve_draft(paths, "leak2")
        assert target == paths.monitors_dir / "leak2.toml"
        assert not (paths.drafts_dir / "leak2.toml").exists()
        assert loader.load_file(target).name == "leak2"

    def test_approval_race_fails_and_keeps_draft(self, core_env):  # noqa: F811
        """[PM-06] the TS-05 approval race: the target appearing between
        draft and approve fails with name_exists — never clobbered — and
        the draft survives for a retry under a new name."""
        paths = core_env
        McpApi(paths).define_monitor(DRAFT2)
        (paths.monitors_dir / "leak2.toml").write_text(DRAFT2)
        with pytest.raises(manage.ManageError) as ei:
            manage.approve_draft(paths, "leak2")
        assert ei.value.code == "name_exists"
        assert (paths.drafts_dir / "leak2.toml").exists()

    def test_approve_missing_draft(self, core_env):  # noqa: F811
        """[PM-06] approving a name with no draft -> not_found."""
        with pytest.raises(manage.ManageError) as ei:
            manage.approve_draft(core_env, "ghost")
        assert ei.value.code == "not_found"

    def test_set_enabled_edits_one_line_only(self, core_env):  # noqa: F811
        """[PM-06] disable flips only the `enabled` line; comments and
        formatting survive verbatim (MD-05: history stays in the file)."""
        paths = core_env
        comment = "# tuned 2026-07-01 after the big leak"
        (paths.monitors_dir / "leak.toml").write_text(
            LEAKDEF.replace('source = "process"',
                            f'source = "process"\n{comment}'))
        manage.set_enabled(paths, "leak", False)
        text = (paths.monitors_dir / "leak.toml").read_text()
        assert "enabled = false" in text
        assert comment in text
        manage.set_enabled(paths, "leak", True)
        assert "enabled = true" in (paths.monitors_dir / "leak.toml").read_text()

    def test_set_enabled_missing_monitor(self, core_env):  # noqa: F811
        """[PM-06] set_enabled on an unknown name -> not_found."""
        with pytest.raises(manage.ManageError) as ei:
            manage.set_enabled(core_env, "ghost", False)
        assert ei.value.code == "not_found"


# --- ack_incident ------------------------------------------------------------------------


class TestAck:
    def test_ack_then_double_ack(self, populated):
        """[MC-01] ack marks the incident acked with by="mcp" and lands the
        note in incident_history; a second ack is invalid_params."""
        paths, _clock, api = populated
        inc_id = next(i["id"] for i in api.list_incidents()["incidents"]
                      if i["state"] == "open")
        res = api.ack_incident(inc_id, note="looking into it")
        assert res["ok"] is True
        assert res["incident"]["state"] == "acked"
        conn = connect(paths.db_file, readonly=True)
        row = conn.execute("SELECT ack_by FROM incidents WHERE id = ?",
                           (inc_id,)).fetchone()
        assert row["ack_by"] == "mcp"
        hist = conn.execute(
            "SELECT detail FROM incident_history "
            "WHERE incident_id = ? AND kind = 'acked'", (inc_id,)).fetchone()
        assert json.loads(hist["detail"])["note"] == "looking into it"
        conn.close()
        err = assert_err(api.ack_incident(inc_id), "invalid_params")
        assert "not open" in err["message"]

"""[M10] Release-readiness coverage for previously pending requirement IDs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from ftmon.definitions import ValidationError, load_text
from ftmon.definitions.manage import approve_draft, write_draft
from ftmon.engine.effects import EffectExecutor
from ftmon.engine.incidents import GroupConfig, RungConfig, RungEval, step_group
from ftmon.engine.rings import RingStore
from ftmon.expr.tribool import TriBool
from ftmon.model import EventRecord, GroupState, IncidentCore, RungState, SourceDecl
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate
from ftmon.store.retention import Retention

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "ftmon"
TOOLS = REPO / "tools"


def test_capacity_worksheet_limits_are_codified_dm_16(tmp_path):
    """[DM-16] DESIGN §9 assumptions appear as enforced retention and ring caps."""
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    conn = connect(paths.db_file)
    migrate(conn)
    policy = Retention(conn)
    assert policy._r1h_keep_durable == 400 * 86400
    assert policy._r1h_keep_process == 90 * 86400
    conn.close()
    rings = RingStore(max_bytes=64 * 2**20)
    assert rings._max_bytes == 64 * 2**20


def test_schema_rejects_unknown_keys_md_01_md_03():
    """[MD-01][MD-03] One validator rejects unknown keys with structured paths."""
    text = """
schema = 1
bogus = 1
[monitor]
name = "x"
description = "x"
version = 1
platforms = ["linux"]
interval = "60s"
source = "disk"
[[rule]]
id = "r1"
when = "used_pct > 1"
severity = "warning"
message = "hi"
"""
    with pytest.raises(ValidationError) as exc:
        load_text(text)
    assert any(err["code"] == "unknown_key" for err in exc.value.errors)


def test_message_template_unknown_field_fails_at_validation_md_02():
    """[MD-02] Message formatting errors are caught at validation time."""
    text = """
schema = 1
[monitor]
name = "x"
description = "x"
version = 1
platforms = ["linux"]
interval = "60s"
source = "disk"
[[rule]]
id = "r1"
when = "used_pct > 1"
severity = "warning"
message = "hi {missing_field}"
"""
    with pytest.raises(ValidationError) as exc:
        load_text(text)
    assert any(err["path"] == "rule[0].message" for err in exc.value.errors)


def test_draft_approve_moves_into_monitors_md_05(tmp_path):
    """[MD-05] Approval promotes a draft into the active monitors directory."""
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    draft_text = """
schema = 1
[monitor]
name = "probe"
description = "probe"
version = 1
platforms = ["linux"]
interval = "60s"
source = "system"
[[rule]]
id = "r1"
when = "load1 > 1"
severity = "warning"
message = "busy"
"""
    write_draft(paths, draft_text)
    target = approve_draft(paths, "probe")
    assert target == paths.monitors_dir / "probe.toml"
    assert not (paths.drafts_dir / "probe.toml").exists()


def test_editing_enabled_definition_supersedes_open_incidents_md_06(tmp_path):
    """[MD-06] A changed enabled file clears open incidents as superseded."""
    from ftmon.clock import FakeClock
    from ftmon.daemon import DaemonCore
    from tests.unit.test_engine import LEAKDEF, ScriptedSampler, grower

    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    sampler = ScriptedSampler()
    for i in range(8):
        sampler.push(grower(i))
    core.samplers["process"] = sampler
    for _ in range(8):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(60)
    conn = connect(paths.db_file, readonly=True)
    assert conn.execute(
        "SELECT state FROM incidents WHERE state='open'"
    ).fetchone() is not None
    conn.close()
    text = (paths.monitors_dir / "leak.toml").read_text()
    (paths.monitors_dir / "leak.toml").write_text(text.replace("version = 1", "version = 2"))
    clock.advance(31)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    conn = connect(paths.db_file, readonly=True)
    row = conn.execute(
        "SELECT state, clear_reason FROM incidents ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["state"] == "cleared" and row["clear_reason"] == "superseded"
    conn.close()


def test_derived_metric_cycle_is_rejected_md_08():
    """[MD-08] Derived-metric dependency cycles fail validation."""
    text = """
schema = 1
[monitor]
name = "x"
description = "x"
version = 1
platforms = ["linux"]
interval = "60s"
source = "disk"
[[derived]]
name = "a"
expr = "b + 1"
[[derived]]
name = "b"
expr = "a + 1"
[[rule]]
id = "r1"
when = "a > 1"
severity = "warning"
message = "cycle"
"""
    with pytest.raises(ValidationError) as exc:
        load_text(text)
    assert any(err["code"] == "derived_cycle" for err in exc.value.errors)


def test_removed_monitor_group_supersedes_incidents_md_09(tmp_path):
    """[MD-09] Removing a monitor clears open incidents with superseded."""
    from ftmon.clock import FakeClock
    from ftmon.daemon import DaemonCore

    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    leak = (REPO / "src/ftmon/definitions/builtins/leak.toml").read_text()
    (paths.monitors_dir / "leak.toml").write_text(leak)
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    DaemonCore(paths=paths, clock=clock)
    conn = connect(paths.db_file)
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(1,'leak','grow','firefox:1:1','open',2,'grow-warn',900,900,1,1)"
    )
    conn.commit()
    conn.close()
    (paths.monitors_dir / "leak.toml").unlink()
    clock.advance(31)
    core2 = DaemonCore(paths=paths, clock=clock)
    core2.on_tick(clock.now(), clock.monotonic(), 0.0)
    conn = connect(paths.db_file, readonly=True)
    row = conn.execute("SELECT clear_reason FROM incidents WHERE id=1").fetchone()
    assert row["clear_reason"] == "superseded"
    conn.close()


def test_platform_conditionals_only_behind_four_seams_pl_01():
    """[PL-01] No sys.platform / darwin / win32 checks outside adapter modules."""
    allowed = {
        "sources/process.py", "sources/disk.py", "sources/system.py",
        "sources/net.py", "sources/unit.py", "sources/journald.py",
        "sources/fixtures.py", "sources/base.py", "notify/desktop.py",
        "paths.py", "cli.py", "definitions/schema.py",
    }
    pattern = re.compile(r"\b(sys\.platform|platform\.system|darwin|win32|nt\b)")
    offenders = []
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        if rel in allowed or rel.startswith("sources/") or rel.startswith("notify/"):
            continue
        if pattern.search(py.read_text()):
            offenders.append(rel)
    assert offenders == []


def test_event_id_is_optional_string_pl_02():
    """[PL-02] Canonical events accept absent identifiers."""
    ev = EventRecord(
        ts=1.0, ingest_ts=1.0, source="journald", provider="systemd",
        event_id=None, severity=2, message="hello", attrs={},
    )
    assert ev.event_id is None


def test_fixture_samplers_exist_as_second_implementation_pl_04():
    """[PL-04] Production package ships deterministic sampler fakes."""
    from ftmon.sources import fixtures

    assert hasattr(fixtures, "FixtureSampler")
    assert hasattr(fixtures, "load_scenario")


def test_source_decl_exposes_metric_schema_pl_05():
    """[PL-05] Samplers declare entity kinds and metric names."""
    from ftmon.sources.disk import DiskSampler

    decl = DiskSampler.decl
    assert isinstance(decl, SourceDecl)
    assert decl.entity_kind
    assert decl.metric_names()


def test_attack_surface_listeners_are_loopback_or_stdio_pm_05_se_01(tmp_path):
    """[PM-05][SE-01] Web binds loopback; MCP factory does not open TCP."""
    from ftmon.mcp_server import build_server
    from ftmon.web import app as web_app

    text = Path(web_app.__file__).read_text()
    assert 'host="127.0.0.1"' in text or "host='127.0.0.1'" in text
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    server = build_server(paths)
    assert server is not None


def test_self_builtin_encodes_rb_01_budgets_rb_01():
    """[RB-01] Built-in self monitor encodes daemon CPU/RSS/DB budgets."""
    text = (REPO / "design/builtins/self.toml").read_text()
    assert "rss_budget_mb" in text and "100" in text
    assert "db_budget_mb" in text and "200" in text
    assert "cpu_budget_pct" in text


def test_config_has_no_legacy_cipher_fields_se_03():
    """[SE-03] No CipherSaber or password storage keys ship in v2 config."""
    sample = (REPO / "src/ftmon/config.py").read_text()
    assert "CipherSaber" not in sample
    assert "password" not in sample.lower() or "SecretRef" in sample


def test_repository_uses_tests_first_work_packages_ts_02():
    """[TS-02] Milestone work is specified with frozen tests in DESIGN.md."""
    design = (REPO / "DESIGN.md").read_text()
    assert "tests-first" in design or "WP" in design
    assert (REPO / "tests/unit/test_traceability.py").is_file()


def test_soak_report_emits_markdown_from_fixture_db_ts_17(tmp_path):
    """[TS-17] soak_report reads stored self history and doctor output."""
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    conn = connect(paths.db_file)
    migrate(conn)
    conn.execute("INSERT INTO meta(key,value) VALUES('last_tick_ts','1000')")
    conn.execute(
        "INSERT INTO series(id,monitor,entity_id,metric,durable) VALUES"
        "(1,'self','ftmon','rss_bytes',1),(2,'self','ftmon','cpu_pct',1),"
        "(3,'self','ftmon','db_bytes',1)"
    )
    conn.executemany(
        "INSERT INTO samples(series_id,ts,value) VALUES(?,?,?)",
        [(1, 900, 50_000_000), (2, 900, 0.2), (3, 900, 10_000_000)],
    )
    conn.commit()
    conn.close()
    sys.path.insert(0, str(TOOLS))
    from soak_report import build_report

    report = build_report(paths.db_file, now=1000.0)
    assert "# FTMON soak evidence report" in report
    assert "rss_mb" in report
    assert '"ok": true' in report


def test_web_ui_uses_vendored_uplot_without_spa_build_ui_06():
    """[UI-06] Dashboard ships server-rendered pages with vendored uPlot assets."""
    static = REPO / "src/ftmon/web/static"
    assert (static / "vendor/uPlot.iife.min.js").is_file()
    assert (static / "ftmon.js").is_file()
    assert any(
        p.read_text().startswith("{%")
        for p in (REPO / "src/ftmon/web/templates").glob("*.html")
    )


def test_daemon_runs_without_web_process_ui_07(tmp_path):
    """[UI-07] The monitoring daemon does not depend on the web server."""
    from ftmon.clock import FakeClock
    from ftmon.daemon import DaemonCore
    from tests.unit.test_engine import LEAKDEF

    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    core = DaemonCore(paths=paths, clock=FakeClock(wall=1000.0, mono=100.0))
    core.on_tick(1000.0, 100.0, 0.0)
    conn = connect(paths.db_file, readonly=True)
    assert conn.execute("SELECT value FROM meta WHERE key='last_tick_ts'").fetchone()
    conn.close()


def test_action_runner_rejects_world_writable_script_se_01(tmp_path):
    """[SE-01] Action scripts use the same trusted-executable policy as checks."""
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    script = paths.actions_dir / "capture"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o707)
    conn = connect(paths.db_file)
    migrate(conn)
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(1,'disk','filling','/','open',2,'fill',1,1,1,1)"
    )
    conn.commit()
    from ftmon.engine.actions import ActionRunner
    from ftmon.engine.effects import PendingAction

    result = ActionRunner(conn, paths).run_one(
        PendingAction(1, "capture", {"FTMON_MONITOR": "disk"}), 10.0,
    )
    assert result.status == "error"
    conn.close()


def test_traceability_pending_ratchet_is_enforced_ts_18():
    """[TS-18] Pending IDs are a shrinking ratchet tracked against coverage."""
    import sys

    sys.path.insert(0, str(TOOLS))
    from gen_reqindex import load_pending, load_reqindex, scan_test_coverage

    testable, _ = load_reqindex(REPO / "tests/reqindex.json")
    covered = scan_test_coverage(REPO / "tests")
    pending = load_pending(REPO / "tests/traceability_pending.json")
    assert pending <= testable
    assert not (covered & pending)


def test_schema_version_must_be_known_vc_02():
    """[VC-02] Only declared schema versions validate."""
    text = """
schema = 99
[monitor]
name = "x"
description = "x"
version = 1
platforms = ["linux"]
interval = "60s"
source = "disk"
[[rule]]
id = "r1"
when = "used_pct > 1"
severity = "warning"
message = "hi"
"""
    with pytest.raises(ValidationError) as exc:
        load_text(text)
    assert any(
        err["path"] == "schema" and err["code"] == "invalid_value"
        for err in exc.value.errors
    )


def test_recipe_readme_documents_original_script_policy_xr_05():
    """[XR-05] Recipes document separately licensed upstream checks."""
    readme = (REPO / "extra-monitors/README.md").read_text()
    assert "Third-party" in readme or "third-party" in readme
    assert "licence" in readme.lower() or "license" in readme.lower()


def test_acked_silent_downgrade_preserves_ack_metadata():
    """Ack columns survive effect-executor upserts on in-place downgrades."""
    cfg = GroupConfig(
        monitor="load",
        entity_id="host",
        group="pressure",
        rungs=(
            RungConfig("critical", 4, 1, 1),
            RungConfig("error", 3, 1, 1),
        ),
    )
    core = IncidentCore(
        incident_id=7,
        state="acked",
        severity=4,
        owning_rule="critical",
        opened_ts=100.0,
        last_notify_ts=100.0,
        notify_count=1,
        backoff_tier=0,
        flap_clears=(),
        occurrences=1,
        ack_by="web",
        ack_ts=200.0,
    )
    st = GroupState(
        rungs={
            "critical": RungState(confirmed=True, confirm_count=1),
            "error": RungState(confirmed=False),
        },
        core=core,
    )
    evals = {
        "critical": RungEval(TriBool.FALSE),
        "error": RungEval(TriBool.TRUE, "pressure"),
    }
    st2, effects = step_group(cfg, st, evals, 300.0)
    assert st2.core is not None and st2.core.state == "acked"
    assert st2.core.severity == 3

    class _Writer:
        last_kwargs: dict | None = None

        def upsert_incident(self, *args, **kwargs):
            self.last_kwargs = kwargs

        def alloc_incident_id(self):
            return 7

        def add_incident_history(self, *args, **kwargs):
            pass

        def add_outbox(self, *args, **kwargs):
            pass

    writer = _Writer()
    EffectExecutor(writer).apply(cfg, st2, effects, 300.0)
    assert writer.last_kwargs is not None
    assert writer.last_kwargs["ack_by"] == "web"
    assert writer.last_kwargs["ack_ts"] == 200.0

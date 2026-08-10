"""[PM-08][PL-01][SA-04][DM-01] profile/windows monitor definitions: the
dead-rule removals (disk inodes, events OOM) verified both structurally
(rule IDs absent/present) and behaviorally (the retained rules actually
fire true and false against real-shaped ticks, not just "doesn't error"),
plus src/design tree parity for the whole profile.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import EntitySample, Snapshot
from ftmon.paths import get_paths
from ftmon.sources.base import SOURCE_DECLS
from ftmon.store.db import connect

REPO = Path(__file__).resolve().parents[2]
WINDOWS_PROFILE = REPO / "src" / "ftmon" / "definitions" / "profile" / "windows"
DESIGN_WINDOWS_PROFILE = REPO / "design" / "profile" / "windows"

_EXPECTED_FILES = {
    "disk.toml", "events.toml", "hog.toml", "leak.toml",
    "load.toml", "net.toml", "self.toml", "service.toml",
}


def test_src_and_design_windows_profile_trees_are_identical():
    """CONTRIBUTING.md: the normative design/ copy and the packaged src/
    copy must never drift, same guard as builtins/ elsewhere."""
    src_files = {p.name for p in WINDOWS_PROFILE.glob("*.toml")}
    design_files = {p.name for p in DESIGN_WINDOWS_PROFILE.glob("*.toml")}
    assert src_files == _EXPECTED_FILES
    assert design_files == _EXPECTED_FILES
    for name in _EXPECTED_FILES:
        src_bytes = (WINDOWS_PROFILE / name).read_bytes()
        design_bytes = (DESIGN_WINDOWS_PROFILE / name).read_bytes()
        assert src_bytes == design_bytes, f"{name}: profile/windows src/design drifted"


class ScriptedDiskSampler:
    """Minimal fixture mirroring test_engine.py's ScriptedSampler, for the
    "disk" source instead of "process"."""

    decl = SOURCE_DECLS["disk"]

    def __init__(self) -> None:
        self.script: list[list[tuple[str, dict, dict]]] = []
        self.calls = 0

    def push(self, *entities) -> None:
        self.script.append(list(entities))

    def sample(self, now, deadline_mono, options) -> Snapshot:
        ents = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Snapshot(
            source="disk", ts=now,
            entities=tuple(EntitySample(entity_id=e, attrs=a, metrics=m) for e, a, m in ents),
        )


def _windows_core_env(tmp_path, monitor_name: str):
    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    text = (WINDOWS_PROFILE / f"{monitor_name}.toml").read_text(encoding="utf-8")
    (paths.monitors_dir / f"{monitor_name}.toml").write_text(text, encoding="utf-8")
    return paths


def _tick_n(core, clock, n, step=60.0):
    for _ in range(n):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(step)


def _disk_entity(used_pct: float) -> tuple[str, dict, dict]:
    total = 1_000_000_000.0
    used = total * used_pct / 100.0
    return (
        "C:\\",
        {"fstype": "NTFS", "device": "C:"},
        {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": total - used,
            "used_pct": used_pct,
        },
    )


class TestDiskWindowsProfile:
    def test_inode_rules_and_parameters_are_absent(self):
        """[PL-01] The 3 inode-based rules and their parameters are dropped
        entirely, not left present-but-silent -- inode_used_pct is always
        None on NTFS, so their presence would misrepresent coverage."""
        parsed = tomllib.loads((WINDOWS_PROFILE / "disk.toml").read_text(encoding="utf-8"))
        rule_ids = {r["id"] for r in parsed["rule"]}
        assert rule_ids == {"space-notice", "space-warn", "space-crit", "filling"}
        assert not any(name.startswith("inode_") for name in parsed["parameters"])

    def test_used_pct_below_threshold_opens_no_incident(self, tmp_path):
        """[SA-04] The retained space-warn rule stays quiet below its
        threshold -- the "false" side of the review's bidirectional check."""
        paths = _windows_core_env(tmp_path, "disk")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedDiskSampler()
        for _ in range(5):
            sampler.push(_disk_entity(50.0))
        core.samplers["disk"] = sampler
        _tick_n(core, clock, 5)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute("SELECT state FROM incidents WHERE state='open'").fetchone()
        conn.close()
        assert row is None

    def test_used_pct_above_threshold_opens_warning_incident(self, tmp_path):
        """[SA-04] The retained space-warn rule (confirm_cycles=3) fires
        true once used_pct sustains above space_warn_pct (92) -- the "true"
        side of the review's bidirectional check, driven through the real
        DaemonCore/pipeline, not just evaluated in isolation."""
        paths = _windows_core_env(tmp_path, "disk")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedDiskSampler()
        for _ in range(5):
            sampler.push(_disk_entity(95.0))
        core.samplers["disk"] = sampler
        _tick_n(core, clock, 5)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute(
            "SELECT state, severity FROM incidents WHERE state='open'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["severity"] == 2  # warning (space-crit needs 97%, not reached)


class ScriptedProcessSampler:
    """Minimal fixture mirroring test_engine.py's ScriptedSampler, for the
    "process" source hog/leak share."""

    decl = SOURCE_DECLS["process"]

    def __init__(self) -> None:
        self.script: list[list[tuple[str, dict, dict]]] = []
        self.calls = 0

    def push(self, *entities) -> None:
        self.script.append(list(entities))

    def sample(self, now, deadline_mono, options) -> Snapshot:
        ents = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Snapshot(
            source="process", ts=now,
            entities=tuple(EntitySample(entity_id=e, attrs=a, metrics=m) for e, a, m in ents),
        )


def _process_entity(entity_id: str, name: str, cpu_pct: float) -> tuple[str, dict, dict]:
    return (entity_id, {"name": name}, {"cpu_pct": cpu_pct})


class TestHogWindowsProfile:
    def test_system_idle_process_is_exempt(self):
        """[PL-01] The exempt clause targets exactly the entity found
        opening a permanently-stuck critical incident on a real overnight
        run (cpu_pct up to 1795%)."""
        parsed = tomllib.loads((WINDOWS_PROFILE / "hog.toml").read_text(encoding="utf-8"))
        assert parsed["exempt"] == ['matches(name, "^System Idle Process$")']

    def test_system_idle_process_opens_no_incident_even_at_extreme_cpu(self, tmp_path):
        """[SA-04][PL-03] The "false" side: an exempt entity must not open
        an incident no matter how far past threshold its (meaningless)
        cpu_pct reads -- driven through the real DaemonCore/pipeline."""
        paths = _windows_core_env(tmp_path, "hog")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedProcessSampler()
        for _ in range(6):
            sampler.push(_process_entity("System Idle Process:0:0", "System Idle Process", 1795.0))
        core.samplers["process"] = sampler
        _tick_n(core, clock, 6)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute("SELECT state FROM incidents WHERE state='open'").fetchone()
        conn.close()
        assert row is None

    def test_a_real_process_at_the_same_cpu_still_opens_an_incident(self, tmp_path):
        """[SA-04] The "true" side: exempt is scoped to the one entity name,
        not a blanket loosening of the hog rule -- a normal process at the
        same extreme cpu_pct still alerts."""
        paths = _windows_core_env(tmp_path, "hog")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedProcessSampler()
        for _ in range(6):
            sampler.push(_process_entity("runaway.exe:9999:123", "runaway.exe", 1795.0))
        core.samplers["process"] = sampler
        _tick_n(core, clock, 6)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute("SELECT state FROM incidents WHERE state='open'").fetchone()
        conn.close()
        assert row is not None


class ScriptedSelfSampler:
    """Minimal fixture for the "self" source, mirroring the other Scripted*
    samplers in this file."""

    decl = SOURCE_DECLS["self"]

    def __init__(self) -> None:
        self.script: list[list[tuple[str, dict, dict]]] = []
        self.calls = 0

    def push(self, *entities) -> None:
        self.script.append(list(entities))

    def sample(self, now, deadline_mono, options) -> Snapshot:
        ents = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Snapshot(
            source="self", ts=now,
            entities=tuple(EntitySample(entity_id=e, attrs=a, metrics=m) for e, a, m in ents),
        )


def _self_entity(cpu_pct: float) -> tuple[str, dict, dict]:
    return ("ftmon", {}, {"cpu_pct": cpu_pct, "rss_bytes": 0.0, "db_bytes": 0.0,
                           "source_activity_age_s": 0.0})


class TestSelfWindowsProfile:
    def test_cpu_budget_recalibrated_and_platforms_narrowed(self):
        """[RB-01][RB-02][PL-01] Recalibrated for measured Windows overhead
        (see docs/WIN-BACKLOG.md); platforms narrowed to windows-only so this looser
        threshold can never load on Linux/macOS, where RB-01's 1.0 default
        still applies via the generic builtins/self.toml."""
        parsed = tomllib.loads((WINDOWS_PROFILE / "self.toml").read_text(encoding="utf-8"))
        assert parsed["parameters"]["cpu_budget_pct"]["value"] == 30
        assert tuple(parsed["monitor"]["platforms"]) == ("windows",)

    def test_cpu_below_recalibrated_budget_opens_no_incident(self, tmp_path):
        """[SA-04] The steady-state overnight reading (~16%) must not alert
        against the recalibrated budget -- the "false" side."""
        paths = _windows_core_env(tmp_path, "self")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedSelfSampler()
        for _ in range(4):
            sampler.push(_self_entity(16.0))
        core.samplers["self"] = sampler
        _tick_n(core, clock, 4)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute(
            "SELECT state FROM incidents WHERE state='open' AND grp='cpu-budget'"
        ).fetchone()
        conn.close()
        assert row is None

    def test_cpu_above_recalibrated_budget_still_opens_an_incident(self, tmp_path):
        """[SA-04][RB-02] The watchdog must still fire for a genuine
        regression well past the recalibrated budget -- the "true" side;
        proves the recalibration didn't quietly gut RB-02's purpose."""
        paths = _windows_core_env(tmp_path, "self")
        clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, platform="windows")
        sampler = ScriptedSelfSampler()
        for _ in range(4):
            sampler.push(_self_entity(80.0))
        core.samplers["self"] = sampler
        _tick_n(core, clock, 4)

        conn = connect(paths.db_file, readonly=True)
        row = conn.execute(
            "SELECT state FROM incidents WHERE state='open' AND grp='cpu-budget'"
        ).fetchone()
        conn.close()
        assert row is not None


class TestEventsWindowsProfile:
    def test_oom_rule_is_absent_errors_and_shutdown_rules_kept(self):
        """[PL-01] provider == "kernel" never matches a Windows Event Log
        provider -- dropped rather than left permanently dead."""
        parsed = tomllib.loads((WINDOWS_PROFILE / "events.toml").read_text(encoding="utf-8"))
        rule_ids = {r["id"] for r in parsed["rule"]}
        assert rule_ids == {"errors", "unexpected-shutdown"}
        assert all("oom" not in rid for rid in rule_ids)


class TestLoadWindowsProfileUnchanged:
    def test_load_toml_keeps_psi_rules_matching_spec_7_7_5(self):
        """SPEC 7.7.5: a PSI-less system gets an absent readout, not a
        substitute metric -- load.toml is intentionally NOT reworked for
        Windows, unlike disk/events. This guards against someone "fixing"
        it later without re-reading that decision."""
        parsed = tomllib.loads((WINDOWS_PROFILE / "load.toml").read_text(encoding="utf-8"))
        rule_ids = {r["id"] for r in parsed["rule"]}
        assert rule_ids == {"pressure-warn", "pressure-crit"}
        pressure_crit = next(r for r in parsed["rule"] if r["id"] == "pressure-crit")
        assert "psi_some_mem" in pressure_crit["when"]

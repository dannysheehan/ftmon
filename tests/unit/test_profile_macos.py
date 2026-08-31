"""[PM-08][PL-01][SA-04][DM-01] macOS profile semantic and behavior checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import EntitySample, Snapshot
from ftmon.paths import get_paths
from ftmon.sources.base import SOURCE_DECLS
from ftmon.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "src/ftmon/definitions/profile/macos"
DESIGN = ROOT / "design/profile/macos"
FILES = {"disk.toml", "events.toml", "hog.toml", "leak.toml", "load.toml",
         "net.toml", "self.toml", "service.toml"}


class TrackingEventSource:
    decl = SOURCE_DECLS["events"]
    cursor_name = "oslog"

    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self, cursor):
        self.starts += 1

    def drain(self, now, max_items):
        return [], None

    def alive(self):
        return True

    def stop(self):
        self.stops += 1


def test_profile_trees_match_and_every_monitor_is_darwin_only():
    assert {p.name for p in PROFILE.glob("*.toml")} == FILES
    for name in FILES:
        assert (PROFILE / name).read_bytes() == (DESIGN / name).read_bytes()
        parsed = tomllib.loads((PROFILE / name).read_text())
        assert parsed["monitor"]["platforms"] == ["darwin"]


def test_platform_noise_rules_are_removed_and_events_use_safe_admission():
    events = tomllib.loads((PROFILE / "events.toml").read_text())
    load = tomllib.loads((PROFILE / "load.toml").read_text())
    disk = tomllib.loads((PROFILE / "disk.toml").read_text())
    assert events["monitor"]["enabled"] is True
    assert events["source_options"]["store_min_severity"] == "critical"
    assert {r["id"] for r in events["rule"]} == {
        "third-party-fault", "storage-integrity"
    }
    assert all("event_id ==" in rule["when"] for rule in events["rule"])
    assert {r["id"] for r in load["rule"]} == {"pressure-warn"}
    assert "psi" not in " ".join(r["when"] for r in load["rule"])
    assert not any(r["id"].startswith("inodes-") for r in disk["rule"])
    filling = next(r for r in disk["rule"] if r["id"] == "filling")
    assert 'coverage(used_bytes, "70m") >= min_filling_coverage' in filling["when"]
    assert "used_pct > 70" in filling["when"]
    assert filling["confirm_cycles"] == 9
    assert 'readonly == "true"' in disk["exempt"]
    assert (
        'contains(mount_options, "nobrowse") and mountpoint != "/System/Volumes/Data"'
        in disk["exempt"]
    )
    net = tomllib.loads((PROFILE / "net.toml").read_text())
    assert {r["id"] for r in net["rule"]} == {"listener-down"}
    service = (PROFILE / "service.toml").read_text()
    assert "{ unit =" not in service


def test_hog_profile_guards_only_known_cpu_permission_denials():
    """[PL-03][PM-08] Readable CPU keeps both windows; denied CPU is inapplicable."""
    hog = tomllib.loads((PROFILE / "hog.toml").read_text())
    assert hog["monitor"]["version"] == 3
    rules = {rule["id"]: rule["when"] for rule in hog["rule"]}
    assert rules == {
        "hog-warn": 'cpu_pct_readable == "true" and avg(cpu_pct, "5m") > warn_pct',
        "hog-crit": 'cpu_pct_readable == "true" and avg(cpu_pct, "15m") > crit_pct',
    }


def test_leak_profile_requires_persistent_growth_and_exempts_gui_helpers():
    leak = tomllib.loads((PROFILE / "leak.toml").read_text())
    assert leak["parameters"]["warn_mb_per_h"]["value"] == 96
    assert leak["parameters"]["crit_mb_per_h"]["value"] == 256
    assert leak["parameters"]["min_net_mb"]["value"] == 48
    assert all(rule["confirm_cycles"] == 9 for rule in leak["rule"])
    exemptions = " ".join(leak["exempt"])
    assert "Google Chrome Helper" in exemptions
    assert "com\\\\.apple\\\\.WebKit" in exemptions
    assert "node" not in exemptions


def test_safe_events_profile_starts_unified_log(tmp_path):
    """[PM-08][DM-15][SA-08] The standard profile starts its filtered reader."""
    env = {f"FTMON_{kind}_DIR": str(tmp_path / kind.lower())
           for kind in ("CONFIG", "DATA", "STATE", "RUNTIME")}
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "events.toml").write_bytes((PROFILE / "events.toml").read_bytes())
    source = TrackingEventSource()

    core = DaemonCore(
        paths=paths,
        clock=FakeClock(wall=1_700_000_000.0, mono=1000.0),
        platform="darwin",
        event_source=source,
    )
    assert set(core.event_monitors) == {"events"}
    assert source.starts == 1


class MemorySampler:
    decl = SOURCE_DECLS["system"]

    def __init__(self, available: float):
        self.available = available

    def sample(self, now, deadline_mono, options):
        return Snapshot(
            "system",
            now,
            (EntitySample("system", {"hostname": "mac"}, {
                "mem_total_bytes": 100.0,
                "mem_available_bytes": self.available,
                "mem_used_bytes": 100.0 - self.available,
            }),),
        )


def _core(tmp_path, available: float):
    env = {f"FTMON_{kind}_DIR": str(tmp_path / kind.lower())
           for kind in ("CONFIG", "DATA", "STATE", "RUNTIME")}
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "load.toml").write_bytes((PROFILE / "load.toml").read_bytes())
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="darwin")
    core.samplers["system"] = MemorySampler(available)
    for _ in range(6):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(60)
    return paths


def test_memory_rule_body_is_false_with_healthy_real_shaped_data(tmp_path):
    paths = _core(tmp_path, 50.0)
    conn = connect(paths.db_file, readonly=True)
    assert conn.execute("SELECT 1 FROM incidents WHERE state='open'").fetchone() is None
    conn.close()


def test_memory_rule_body_fires_with_low_real_shaped_data(tmp_path):
    paths = _core(tmp_path, 2.0)
    conn = connect(paths.db_file, readonly=True)
    assert conn.execute("SELECT 1 FROM incidents WHERE state='open'").fetchone() is not None
    conn.close()


class MixedProcessSampler:
    decl = SOURCE_DECLS["process"]

    def sample(self, now, deadline_mono, options):
        return Snapshot(
            "process",
            now,
            (
                EntitySample(
                    "readable:1:100",
                    {"name": "readable", "cpu_pct_readable": "true"},
                    {"cpu_pct": 95.0, "rss_bytes": 1000.0},
                ),
                EntitySample(
                    "denied:2:100",
                    {"name": "denied", "cpu_pct_readable": "false"},
                    {"rss_bytes": 1000.0},
                ),
                EntitySample(
                    "unexpected:3:100",
                    {"name": "unexpected"},
                    {"rss_bytes": 1000.0},
                ),
            ),
        )


def test_hog_mixed_cpu_visibility_keeps_only_unexpected_missing_unknown(tmp_path):
    """[EX-06][PL-03][SA-04] Known denial is FALSE; inconsistency stays UNKNOWN."""
    env = {
        f"FTMON_{kind}_DIR": str(tmp_path / kind.lower())
        for kind in ("CONFIG", "DATA", "STATE", "RUNTIME")
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "hog.toml").write_bytes((PROFILE / "hog.toml").read_bytes())
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="darwin")
    core.samplers["process"] = MixedProcessSampler()
    for _ in range(6):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(60)

    report = core.pipeline.unknown_report(core.monitors, clock.now())
    assert {
        (item["rule"], item["unknown_entities"], tuple(item["missing_metrics"]))
        for item in report["rules"]
    } == {
        ("hog-warn", 1, ("cpu_pct",)),
        ("hog-crit", 1, ("cpu_pct",)),
    }
    conn = connect(paths.db_file, readonly=True)
    incident = conn.execute(
        "SELECT entity_id, severity FROM incidents WHERE state='open' AND grp='hog'"
    ).fetchone()
    conn.close()
    assert incident is not None
    assert incident["entity_id"] == "readable:1:100"
    assert incident["severity"] == 3

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


def test_platform_noise_rules_are_removed_and_events_are_opt_in():
    events = tomllib.loads((PROFILE / "events.toml").read_text())
    load = tomllib.loads((PROFILE / "load.toml").read_text())
    disk = tomllib.loads((PROFILE / "disk.toml").read_text())
    assert events["monitor"]["enabled"] is False
    assert {r["id"] for r in events["rule"]} == {"third-party-fault"}
    assert "severity >= critical" in events["rule"][0]["when"]
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


def test_disabled_events_profile_does_not_start_unified_log(tmp_path):
    """[PM-08][DM-15] Installed opt-in events must incur no reader work."""
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
    assert core.event_monitors == {}
    assert source.starts == 0


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

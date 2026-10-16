"""Desktop journal regressions use shipped rules and real episodes (PM-08, IN-08)."""

from pathlib import Path

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.paths import get_paths
from ftmon.sources.fixtures import FixtureEventSource, Scenario
from ftmon.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
EVENTS = Path("profile/desktop/events.toml")
PROFILE = ROOT / "src/ftmon/definitions" / EVENTS


def _event(at, provider, message):
    return {"at": at, "event": {
        "source": "journald", "provider": provider, "severity": 0, "message": message,
    }}


def _core(tmp_path, events):
    paths = get_paths({
        f"FTMON_{kind}_DIR": str(tmp_path / kind.lower())
        for kind in ("CONFIG", "DATA", "STATE", "RUNTIME")
    })
    paths.ensure()
    (paths.monitors_dir / "events.toml").write_bytes(PROFILE.read_bytes())
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(
        paths=paths, clock=clock, platform="linux",
        event_source=FixtureEventSource(Scenario(samples={}, events=tuple(events))),
    )
    return core, clock, paths


def _incidents(paths):
    conn = connect(paths.db_file, readonly=True)
    try:
        return conn.execute("SELECT grp, state, occurrences FROM incidents").fetchall()
    finally:
        conn.close()


def test_desktop_event_definitions_match_pm_08():
    """[PM-08] The reviewed design and installed desktop event rules must agree."""
    assert PROFILE.read_bytes() == (ROOT / "design" / EVENTS).read_bytes()


@pytest.mark.parametrize("diagnostic", ["GPU HANG", "GPU hang", "gpu hang"])
@pytest.mark.parametrize("provider", ["kernel", "example-app"])
def test_gpu_hang_requires_kernel_and_accepts_diagnostic_case_pm_08(
    tmp_path, diagnostic, provider,
):
    """[PM-08][IN-08] Intel's uppercase diagnostic opens without admitting app logs."""
    core, clock, paths = _core(tmp_path, [
        _event(0, provider,
               f"i915 0000:00:02.0: [drm] {diagnostic}: ecode 9:1:85dffffb, "
               "in gnome-shell [1234]"),
    ])
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    rows = _incidents(paths)
    assert [row["grp"] for row in rows] == (["gpu-hang"] if provider == "kernel" else [])
    if rows:
        assert rows[0]["state"] == "open"


@pytest.mark.parametrize("provider", ["gnome-shell", "example-app", "tracker-miner-fs"])
def test_disposal_burst_requires_gnome_shell_and_two_hits_pm_08(tmp_path, provider):
    """[PM-08][IN-08] Application disposal logs cannot masquerade as compositor stress."""
    message = "Object Gtk.Label (0x123456789abc), has been already disposed."
    core, clock, paths = _core(tmp_path, [
        _event(0, provider, message), _event(60, provider, message),
    ])
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert _incidents(paths) == []
    clock.advance(60)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    rows = _incidents(paths)
    assert [row["grp"] for row in rows] == (
        ["gnome-shell-disposed"] if provider == "gnome-shell" else []
    )
    if rows:
        assert rows[0]["state"] == "open"
        assert rows[0]["occurrences"] == 2

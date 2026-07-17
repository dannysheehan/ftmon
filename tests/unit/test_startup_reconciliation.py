"""[IN-09][CA-08][IN-07] Startup reconciliation: entity-gone clearing must
survive daemon restarts.

CA-08's grace state is memory-only by design; without IN-09 seeding, an
entity that vanished while the daemon was down leaves rules evaluating None
forever — clear cycles never accumulate and the incident becomes immortal
(the M11 live-install bug that motivated issue #20).
"""

from __future__ import annotations

import json

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.paths import get_paths
from ftmon.store.db import connect
from tests.unit.test_engine import LEAKDEF, ScriptedSampler, grower


@pytest.fixture
def core_env(tmp_path):
    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    return paths


def _notifications(paths):
    if not paths.notifications_file.exists():
        return []
    return [json.loads(line) for line in
            paths.notifications_file.read_text().splitlines()]


def _tick_n(core, clock, n, step=60.0):
    for _ in range(n):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(step)


def _open_leak_incident(paths, clock):
    core = DaemonCore(paths=paths, clock=clock)
    sampler = ScriptedSampler()
    for i in range(200):
        sampler.push(grower(i))
    core.samplers["process"] = sampler
    _tick_n(core, clock, 8)
    row = connect(paths.db_file, readonly=True).execute(
        "SELECT state FROM incidents").fetchone()
    assert row["state"] == "open"  # precondition, not the behavior under test


def test_restart_clears_incident_for_entity_gone_during_downtime_in_09(core_env):
    """[IN-09] the entity vanished while the daemon was down, past
    gone_grace: the rebuilt daemon seeds disappearance tracking from the
    stored last_seen, and the ordinary CA-08 path clears the incident."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    _open_leak_incident(paths, clock)

    clock.advance(600)  # downtime > gone_grace (300 s default)
    core2 = DaemonCore(paths=paths, clock=clock)
    empty = ScriptedSampler()
    empty.push()  # the process never reappears
    core2.samplers["process"] = empty
    _tick_n(core2, clock, 2)

    row = connect(paths.db_file, readonly=True).execute(
        "SELECT state, clear_reason FROM incidents").fetchone()
    assert row["state"] == "cleared"
    assert row["clear_reason"] == "entity_gone"
    last = _notifications(paths)[-1]
    assert last["kind"] == "recover" and "went away" in last["body"]


def test_restart_does_not_clear_incident_when_entity_still_alive_in_09(core_env):
    """[IN-09] seeding must never invent disappearances: the entity is still
    present after restart, so the incident survives untouched."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    _open_leak_incident(paths, clock)

    clock.advance(600)  # long downtime, but the process is still running
    core2 = DaemonCore(paths=paths, clock=clock)
    sampler = ScriptedSampler()
    for i in range(200, 210):
        sampler.push(grower(i))
    core2.samplers["process"] = sampler
    _tick_n(core2, clock, 3)

    row = connect(paths.db_file, readonly=True).execute(
        "SELECT state, clear_reason FROM incidents").fetchone()
    assert row["state"] == "open"
    assert row["clear_reason"] is None


def test_restart_within_grace_defers_clear_until_grace_elapses_in_09(core_env):
    """[IN-09][CA-08] downtime shorter than gone_grace: the seeded last_seen
    keeps the original grace clock, so the clear lands only after the full
    grace has elapsed since the entity was genuinely last seen."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    _open_leak_incident(paths, clock)

    clock.advance(120)  # restart well inside the 300 s grace
    core2 = DaemonCore(paths=paths, clock=clock)
    empty = ScriptedSampler()
    empty.push()
    core2.samplers["process"] = empty

    _tick_n(core2, clock, 1)
    row = connect(paths.db_file, readonly=True).execute(
        "SELECT state FROM incidents").fetchone()
    assert row["state"] == "open"  # grace not yet elapsed: no premature clear

    _tick_n(core2, clock, 4)  # now well past grace since last genuine sighting
    row = connect(paths.db_file, readonly=True).execute(
        "SELECT state, clear_reason FROM incidents").fetchone()
    assert row["state"] == "cleared"
    assert row["clear_reason"] == "entity_gone"

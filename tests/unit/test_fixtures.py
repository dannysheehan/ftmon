"""[TS-04] Scenario fixtures: JSONL round-trip, state-in-force sampling
semantics, the named library, and replay through the full DaemonCore."""

from __future__ import annotations

import json

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.sources.fixtures import (
    SCENARIO_NAMES,
    FixtureSampler,
    dump_scenario,
    fixture_samplers,
    load_scenario,
    scenario,
)
from tests.unit.test_m2_integration import core_env, notifications, tick_n  # noqa: F401

LINES = [
    {"at": 0, "source": "process", "entities": [
        {"entity_id": "a:1:1", "attrs": {"name": "a"}, "metrics": {"rss_bytes": 100}}]},
    {"at": 60, "source": "process", "entities": [
        {"entity_id": "a:1:1", "attrs": {"name": "a"}, "metrics": {"rss_bytes": 200}}]},
    {"at": 30, "event": {"source": "journald", "provider": "kernel",
                         "severity": 3, "message": "oom"}},
]


def test_jsonl_round_trip(tmp_path):
    """[TS-04] dump -> load preserves lines; one format for every tier."""
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in LINES))
    scn = load_scenario(path)
    assert scn.sources() == {"process"}
    assert len(scn.samples["process"]) == 2
    assert len(scn.events) == 1

    out = tmp_path / "out.jsonl"
    dump_scenario(scn, out)
    assert load_scenario(out) == scn


def test_malformed_lines_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"source": "process", "entities": []}\n')  # no "at"
    with pytest.raises(ValueError, match="missing 'at'"):
        load_scenario(path)
    path.write_text('{"at": 0}\n')
    with pytest.raises(ValueError, match="source.entities or event"):
        load_scenario(path)


def test_sampler_state_in_force_and_t0_anchor(tmp_path):
    """[TS-04] a line is the state until the next line for that source, and
    relative time anchors at the first sample() call."""
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in LINES))
    s = FixtureSampler(load_scenario(path), "process")

    t0 = 5_000.0  # arbitrary wall start: "at" is relative, not absolute
    snap = s.sample(t0, 0.0, {})
    assert snap.entities[0].metrics["rss_bytes"] == 100.0
    assert s.sample(t0 + 59, 0.0, {}).entities[0].metrics["rss_bytes"] == 100.0
    assert s.sample(t0 + 60, 0.0, {}).entities[0].metrics["rss_bytes"] == 200.0
    assert s.sample(t0 + 999, 0.0, {}).entities[0].metrics["rss_bytes"] == 200.0


def test_named_library_and_unknown(tmp_path):
    assert set(SCENARIO_NAMES) == {
        "steady", "firefox-leak-2mb-min", "entity-vanishes-mid-incident",
        "oom-event-burst", "disk-ladder-updown", "disk-filling-linear", "service-flap",
        "proc-churn-300"}
    for name in SCENARIO_NAMES:
        assert scenario(name).sources() <= {"process", "disk", "unit"}
    assert len(scenario("oom-event-burst").events) == 12
    with pytest.raises(ValueError, match="unknown scenario"):
        scenario("nope")
    # a path is accepted wherever a name is (the CLI --fixtures contract)
    path = tmp_path / "s.jsonl"
    dump_scenario(scenario("steady"), path)
    assert scenario(str(path)) == scenario("steady")


def test_leak_scenario_through_daemon_core(core_env):  # noqa: F811
    """[TS-04] the firefox-leak scenario drives the real pipeline+incident
    engine to open and then recover — the in-process version of the TS-05
    subprocess run, so a harness failure can be bisected against this."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    core.samplers.update(fixture_samplers(scenario("firefox-leak-2mb-min")))

    tick_n(core, clock, 71)  # scenario: 30m growth, then flat
    kinds = [n["kind"] for n in notifications(paths)]
    assert kinds[0] == "open" and kinds[-1] == "recover"
    assert kinds.count("open") == 1 and kinds.count("recover") == 1


def test_entity_vanish_scenario_closes_incident(core_env):  # noqa: F811
    """[TS-04][IN-07] entity-vanishes-mid-incident ends with an entity-gone
    recovery, exercising the gone-grace path from a scenario."""
    paths = core_env
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    core.samplers.update(fixture_samplers(scenario("entity-vanishes-mid-incident")))

    tick_n(core, clock, 30)  # 20m leak, vanish at 21m, +grace 5m
    notes = notifications(paths)
    kinds = [n["kind"] for n in notes]
    assert kinds[0] == "open" and kinds[-1] == "recover"
    assert "went away" in notes[-1]["body"]

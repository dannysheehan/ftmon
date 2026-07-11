"""External alias scheduling and projection tests."""

from __future__ import annotations

from collections import Counter
from types import MappingProxyType, SimpleNamespace

from ftmon.checks.model import CheckSpec, RawCheckResult
from ftmon.checks.registry import CheckRegistry
from ftmon.checks.sampler import ExternalSampler
from ftmon.clock import FakeClock


def _monitor(name: str, alias: str, *, mappings=()):
    return SimpleNamespace(
        name=name,
        source="external",
        source_options={"check": alias, "entity": f"entity-{name}", "perfdata": list(mappings)},
    )


def _mapping(label="latency", metric="latency_s", uom="ms", scale=0.001):
    return {
        "label": label,
        "metric": metric,
        "plugin_uom": uom,
        "unit": "seconds",
        "kind": "gauge",
        "scale": scale,
    }


class FakeRunner:
    def __init__(self, clock, results, advance=0):
        self.clock = clock
        self.results = results
        self.advance = advance
        self.calls = []

    def run(self, spec, deadline_mono):
        self.calls.append(spec.alias)
        self.clock.advance(self.advance)
        return self.results[spec.alias]


def _registry(*aliases):
    entries = {
        alias: CheckSpec(alias, (f"/{alias}",), "nagios", 10) for alias in aliases
    }
    return CheckRegistry(MappingProxyType(entries))


def test_shared_alias_runs_once_and_projects_per_definition():
    """[EC-04][EC-05][EC-08] One raw run supports distinct declared projections."""
    clock = FakeClock()
    raw = RawCheckResult(0, "healthy", 0.25, {"latency": (1500.0, "ms")})
    runner = FakeRunner(clock, {"web": raw})
    counters = Counter()
    sampler = ExternalSampler(_registry("web"), runner, lambda name: counters.update([name]), clock)
    first = _monitor("first", "web", mappings=[_mapping()])
    second = _monitor("second", "web", mappings=[_mapping(metric="latency_ms", scale=1)])

    sampler.prepare([first, second], clock.monotonic() + 10)
    snap1 = sampler.project(first, 123)
    snap2 = sampler.project(second, 123)

    assert runner.calls == ["web"]
    assert snap1.entities[0].metrics == {
        "plugin_state": 0.0,
        "plugin_ok": 1.0,
        "duration_s": 0.25,
        "latency_s": 1.5,
    }
    assert snap2.entities[0].metrics["latency_ms"] == 1500
    assert snap1.entities[0].attrs == {"plugin_message": "healthy"}


def test_projection_omits_missing_uom_and_non_finite_values():
    """[EC-04] Invalid mapped values are omitted without losing check state."""
    clock = FakeClock()
    raw = RawCheckResult(
        2,
        "bad metrics",
        1.0,
        {"wrong": (2.0, "bytes"), "huge": (1e308, "count")},
    )
    runner = FakeRunner(clock, {"check": raw})
    counters = Counter()
    sampler = ExternalSampler(
        _registry("check"), runner, lambda name: counters.update([name]), clock
    )
    monitor = _monitor(
        "mapped",
        "check",
        mappings=[
            _mapping("missing", "missing_value", "s", 1),
            _mapping("wrong", "wrong_value", "s", 1),
            _mapping("huge", "overflow", "count", 1e308),
        ],
    )

    sampler.prepare([monitor], clock.monotonic() + 10)
    metrics = sampler.project(monitor, 1).entities[0].metrics

    assert metrics == {"plugin_state": 2.0, "plugin_ok": 0.0, "duration_s": 1.0}
    assert counters == {
        "external_perfdata_rejected:uom": 1,
        "external_perfdata_rejected:non_finite": 1,
    }


def test_budget_skip_rotates_first_unstarted_alias_to_next_cycle():
    """[EC-08] An exhausted budget skips absence and fairly rotates future work."""
    clock = FakeClock()
    ok = RawCheckResult(0, "ok", 2, {})
    runner = FakeRunner(clock, {alias: ok for alias in ("a", "b", "c")}, advance=2)
    counters = Counter()
    sampler = ExternalSampler(
        _registry("a", "b", "c"), runner, lambda name: counters.update([name]), clock
    )
    monitors = [_monitor(alias, alias) for alias in ("a", "b", "c")]

    sampler.prepare(monitors, clock.monotonic() + 1)
    assert runner.calls == ["a"]
    assert sampler.project(monitors[0], 1).entities
    assert sampler.project(monitors[1], 1).entities == ()
    assert counters["external_checks_skipped"] == 2

    sampler.prepare(monitors, clock.monotonic() + 1)
    assert runner.calls == ["a", "b"]


def test_unknown_is_evidence_but_skip_is_absence_and_only_failures_count():
    """[EC-06][EC-08] UNKNOWN evidence differs from an alias not started in budget."""
    clock = FakeClock()
    results = {
        "unknown": RawCheckResult(3, "plugin unknown", 0.1, {}),
        "failed": RawCheckResult(3, "failed", 0.1, {}, "launch"),
    }
    runner = FakeRunner(clock, results)
    counters = Counter()
    sampler = ExternalSampler(
        _registry("unknown", "failed"), runner, lambda name: counters.update([name]), clock
    )
    unknown = _monitor("unknown", "unknown")
    failed = _monitor("failed", "failed")

    sampler.prepare([unknown, failed], clock.monotonic() + 10)

    assert sampler.project(unknown, 1).entities[0].metrics["plugin_state"] == 3
    assert sampler.project(failed, 1).entities[0].metrics["plugin_state"] == 3
    assert counters == {"external_check_failures:launch": 1}

    sampler.prepare([unknown], clock.monotonic())
    assert sampler.project(unknown, 2).entities == ()

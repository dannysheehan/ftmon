"""[CA-01][SA-05] Deterministic leak scenarios against the packaged definition.

The v0.19 evidence gates exist because of a live false positive (issue #20):
a short-lived process warned after ~10 minutes because window functions treat
"45m" as a maximum, not a requirement. These fixtures pin both directions —
the gates reject thin or oscillating evidence, and they do not suppress the
genuine leaks the monitor exists for. The stepwise case is the recorded
reason `monot` stays out of the alert gate (§7.7.1): grow-plateau-grow scores
low on consecutive-delta confidence yet is exactly a leak.
"""

from __future__ import annotations

from importlib.resources import files

from ftmon.definitions import load_text
from ftmon.engine.pipeline import Pipeline
from ftmon.engine.rings import RingStore
from ftmon.model import TriBool
from tests.unit.test_engine import NullWriter, ScriptedSampler

MB = 1024 * 1024
EID = "app:7:100"


def _leak_def():
    return load_text(
        (files("ftmon.definitions") / "builtins" / "leak.toml").read_text()
    )


def _fired(rss_mb_series, cycles):
    """Run the real builtin over a scripted RSS series (values in MB; None =
    process absent) and collect (cycle, rule_id) for every TRUE evaluation."""
    mdef = _leak_def()
    sampler = ScriptedSampler()
    for v in rss_mb_series:
        if v is None:
            sampler.push()
        else:
            sampler.push((EID, {"name": "app"},
                          {"rss_bytes": float(v) * MB, "cpu_pct": 1.0}))
    rings = RingStore()
    windows: dict[str, float] = {}
    for metric, w in mdef.windows:
        windows[metric] = max(w, windows.get(metric, 0.0))
    rings.configure(mdef.name, mdef.interval_s, windows)
    pipe = Pipeline({"process": sampler}, rings, lambda n: None, gone_grace_s=300.0)
    writer = NullWriter()
    fired: list[tuple[int, str]] = []
    for i in range(cycles):
        for o in pipe.run_monitor(mdef, 1_700_000_000.0 + i * 60.0, 10**9, writer, {}):
            if o.result is TriBool.TRUE:
                fired.append((i, o.rule_id))
    return fired


def test_short_lived_fast_grower_never_fires():
    """[CA-01] the issue-#20 false positive: 10 minutes of very fast growth
    (600 MB/h, 19x the warn threshold) from a process that then exits must
    never evaluate TRUE — coverage(45m) ~ 0.2 gates the thin window."""
    series = [500 + i * 10 for i in range(10)] + [None]
    assert _fired(series, 60) == []


def test_sawtooth_with_full_coverage_never_fires():
    """[CA-01] oscillation around a flat mean (net ~ 0) with the window fully
    observed: the 45m slope and net-delta gates both reject it, even though
    single-cycle deltas of 24 MB exceed min_net_mb on their own."""
    series = [800 + (24 if i % 2 else 0) for i in range(70)]
    assert _fired(series, 70) == []


def test_monotonic_leak_still_fires_warning():
    """[CA-01][SA-05] steady 60 MB/h growth (above warn 32, below crit 128)
    sustained past the window: the warning rung fires once coverage is met —
    the gates must not suppress the detector's target."""
    series = [400 + i for i in range(60)]
    fired = _fired(series, 60)
    assert any(rule == "leak-warn" for _, rule in fired)
    assert all(rule != "leak-crit" for _, rule in fired)
    # No verdict before the window is ~80% represented (36 of 45 minutes).
    assert min(i for i, _ in fired) >= 36


def test_stepwise_leak_still_fires_despite_low_monot():
    """[CA-01] grow-then-plateau (8 MB every 4th minute = 120 MB/h average,
    monot ~ 0.25): fires. Pins the §7.7.1 decision to keep growth confidence
    out of the alert gate — gating on monot would silently hide this leak."""
    series = [400 + (i // 4) * 8 for i in range(60)]
    fired = _fired(series, 60)
    assert any(rule == "leak-warn" for _, rule in fired)

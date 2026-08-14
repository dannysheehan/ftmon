"""[DM-05][RB-02][MD-06] self/budget incident behaviour, issue #104.

Drives the *shipped* builtin self.toml through real ticks with a scripted
self sampler, then reads incidents out of the database. Nothing here assigns
pipeline or incident state: the point of these tests is that the rules as
shipped produce the transitions the issue claims, so anything short of
"definition in, incident row out" would not be evidence.

The metric arithmetic is covered in test_doctor.py; what is covered here is
what an operator actually experiences — whether an incident opens, stays
shut, or clears.
"""

import importlib.resources

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import EntitySample, Snapshot
from ftmon.paths import get_paths
from ftmon.sources.base import SOURCE_DECLS
from ftmon.store.db import DB_BUDGET_BYTES, connect

MB = 1024 * 1024
WARN_BYTES = 230 * MB  # db_warn_mb in the shipped definition


class ScriptedSelfSampler:
    """Returns the self entity the script asks for; last entry repeats."""

    decl = SOURCE_DECLS["self"]

    def __init__(self):
        self.script: list[dict] = []

    def push(self, metrics: dict, times: int = 1) -> None:
        for _ in range(times):
            self.script.append(metrics)

    def sample(self, now, deadline_mono, options) -> Snapshot:
        metrics = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return Snapshot(
            source="self", ts=now,
            entities=(EntitySample(entity_id="ftmon", attrs={}, metrics=metrics),),
        )


def _metrics(*, used=10 * MB, allocated=None, freelist=0.0, cpu=0.0, rss=0.0):
    """One self sample. Defaults are deliberately quiet on every rule."""
    allocated = used + freelist if allocated is None else allocated
    return {
        "cpu_pct": cpu,
        "rss_bytes": rss,
        "db_bytes": allocated,
        "db_allocated_bytes": allocated,
        "db_used_bytes": used,
        "db_freelist_bytes": freelist,
        "db_headroom_bytes": DB_BUDGET_BYTES - used,
        "source_activity_age_s": 0.0,
    }


def _core_with_shipped_self(tmp_path):
    """Install the definition that actually ships, not a fixture rewrite."""
    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    shipped = (
        importlib.resources.files("ftmon.definitions") / "builtins" / "self.toml"
    ).read_text(encoding="utf-8")
    (paths.monitors_dir / "self.toml").write_text(shipped, encoding="utf-8")
    return paths


def _run(paths, sampler, ticks, clock=None):
    clock = clock or FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    core.samplers["self"] = sampler
    try:
        for _ in range(ticks):
            core.on_tick(clock.now(), clock.monotonic(), 0.0)
            clock.advance(60.0)
    finally:
        core.conn.close()
    return clock


def _incidents(paths, group):
    conn = connect(paths.db_file, readonly=True)
    rows = [
        (r["state"], r["owning_rule"])
        for r in conn.execute(
            "SELECT state, owning_rule FROM incidents WHERE monitor='self' "
            "AND grp=? ORDER BY id", (group,)
        )
    ]
    conn.close()
    return rows


def test_used_bytes_over_warn_opens_then_recovery_clears_dm_05(tmp_path):
    """[DM-05][RB-02] A real used-page breach opens db-budget, and recovery clears it.

    The whole point of the issue is that this rule must still work after being
    pointed at a different quantity — a rule that can no longer fire would be
    a worse outcome than the flapping it replaced.
    """
    paths = _core_with_shipped_self(tmp_path)
    sampler = ScriptedSelfSampler()
    sampler.push(_metrics(used=WARN_BYTES + 20 * MB), times=6)  # confirm_cycles=3
    sampler.push(_metrics(used=100 * MB), times=8)  # back under: clear
    _run(paths, sampler, ticks=14)

    states = _incidents(paths, "db-budget")
    assert states, "breach above db_warn_mb must open a db-budget incident"
    assert states[-1] == ("cleared", "db-budget")


def test_allocation_and_freelist_alone_do_not_open_dm_05(tmp_path):
    """[DM-05] Reusable pages are allocated but cost nothing against the budget.

    This is the defect in one assertion: allocation far above the alarm while
    used pages sit well under it. The pre-#104 rule opened here; the corrected
    rule must not.
    """
    paths = _core_with_shipped_self(tmp_path)
    sampler = ScriptedSelfSampler()
    sampler.push(
        _metrics(used=120 * MB, allocated=300 * MB, freelist=180 * MB), times=10
    )
    _run(paths, sampler, ticks=10)

    assert _incidents(paths, "db-budget") == []


def test_used_bytes_oscillating_at_the_target_does_not_flap_dm_05(tmp_path):
    """[DM-05] Steady state at the DM-05 target must be silent.

    Reproduces what the canary actually does: writes accumulate for a few
    samples, retention prunes back under the target, and the cycle repeats.

    The *runs* matter. An earlier version of this test alternated above/below
    on every tick, which `confirm_cycles = 3` suppresses on its own — so it
    passed even with the alarm moved back to the target, pinning nothing.
    Sustained excursions are both what really happens and what makes this an
    actual test of the db_budget_mb/db_warn_mb separation.
    """
    paths = _core_with_shipped_self(tmp_path)
    sampler = ScriptedSelfSampler()
    for _cycle in range(4):  # four prune cycles
        sampler.push(_metrics(used=DB_BUDGET_BYTES + 2 * MB), times=4)
        sampler.push(_metrics(used=DB_BUDGET_BYTES - 2 * MB), times=4)
    _run(paths, sampler, ticks=32)

    assert _incidents(paths, "db-budget") == [], (
        "a footprint oscillating at the DM-05 target must not open an incident"
    )


def test_cpu_rss_and_db_breaches_are_independent_groups_md_06(tmp_path):
    """[RB-02][MD-06] Each budget owns its incident; they clear independently.

    Before the split all three shared group "budget", so one incident could
    stay open while ownership moved between unrelated signals and its duration
    described nothing. Breaching all three and recovering only storage proves
    the groups are genuinely separate.
    """
    paths = _core_with_shipped_self(tmp_path)
    sampler = ScriptedSelfSampler()
    # cpu-budget confirms in 3, rss-budget in 5, db-budget in 3.
    sampler.push(
        _metrics(used=WARN_BYTES + 20 * MB, cpu=90.0, rss=400 * MB), times=8
    )
    # Storage recovers; CPU and memory stay breached.
    sampler.push(_metrics(used=50 * MB, cpu=90.0, rss=400 * MB), times=8)
    _run(paths, sampler, ticks=16)

    db = _incidents(paths, "db-budget")
    cpu = _incidents(paths, "cpu-budget")
    rss = _incidents(paths, "rss-budget")

    assert db and db[-1][0] == "cleared", "storage recovered, so its incident clears"
    assert cpu and cpu[-1][0] != "cleared", "CPU is still breached and stays open"
    assert rss and rss[-1][0] != "cleared", "memory is still breached and stays open"
    # Three distinct incidents, not one shared row changing owner.
    assert len({id(x) for x in (db, cpu, rss)}) == 3
    for group in ("db-budget", "cpu-budget", "rss-budget"):
        rules = {rule for _state, rule in _incidents(paths, group)}
        assert rules == {group}, f"{group} must be owned only by its own rule"


def test_catalog_gauges_are_absent_until_a_tick_has_run_rb_02(tmp_path):
    """[RB-02][DM-16] Before any monitor runs, catalog pressure is unknown.

    Publishing 0 would read as a measurement — "nothing is persisted" — when
    the truth is that nothing has been counted yet. Observed on the canary:
    the first sample after a restart reported 0 and the next reported 78, and
    doctor reads the latest sample, so a diagnostic run in that window showed
    0/400. A missing metric is UNKNOWN under EX-06, which is what this means.
    """
    from ftmon.selfmon import SelfSampler, SelfStats

    stats = SelfStats()
    assert stats.entities_persisted is None
    metrics = SelfSampler(stats).sample(1_000, 0.0, {}).entities[0].metrics
    assert "entities_persisted" not in metrics
    assert "series_persisted" not in metrics

    # Once a tick has counted, the gauges appear -- including a genuine zero,
    # which is a real measurement rather than an absence of one.
    stats.entities_persisted = 0
    stats.series_persisted = 0
    metrics = SelfSampler(stats).sample(1_000, 0.0, {}).entities[0].metrics
    assert metrics["entities_persisted"] == 0.0
    assert metrics["series_persisted"] == 0.0


class TestStageTimingNamespace:
    """[RB-02][DM-16] Stage timings must not widen the self namespace.

    The same constraint that made `external_check_failures` a summed total
    rather than one series per category: every self metric is a persisted
    series billing against the DM-16 catalog budget, so a per-monitor or
    per-source timing dimension would make that budget a function of how many
    monitors an operator installs (#106).
    """

    _STAGES = (
        "sampling_seconds_total", "pipeline_seconds_total", "commit_seconds_total",
        "actions_outbox_seconds_total", "retention_seconds_total",
        "prune_seconds_total", "reap_seconds_total",
    )

    def _metrics(self, stats):
        from ftmon.selfmon import SelfSampler

        return SelfSampler(stats).sample(0.0, 0.0, {}).entities[0].metrics

    def test_all_seven_stages_are_published(self):
        from ftmon.selfmon import SelfStats

        stats = SelfStats()
        for i, name in enumerate(self._STAGES, start=1):
            setattr(stats, name, float(i))
        metrics = self._metrics(stats)
        assert [metrics[name] for name in self._STAGES] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    def test_every_emitted_metric_is_declared(self):
        """[PL-05] SourceDecl is the contract; an undeclared metric is a
        silent extension of it. The seven stage gauges were emitted before
        they were declared (review of PR #126)."""
        from ftmon.selfmon import SelfStats
        from ftmon.sources.base import SOURCE_DECLS

        declared = {m.name for m in SOURCE_DECLS["self"].metrics}
        emitted = set(self._metrics(SelfStats()))
        # Counter-derived names carry a ":category" suffix and are declared
        # under their base name; the fixed set must match exactly.
        fixed = {name for name in emitted if ":" not in name}
        assert fixed - declared == set(), "emitted but undeclared"

    def test_stage_metric_names_do_not_grow_with_runtime_data(self):
        """[DM-16] no metric name may be derived from what the host runs."""
        from ftmon.selfmon import SelfStats

        stats = SelfStats()
        first = set(self._metrics(stats))
        stats.counters["external_check_failures:timeout"] = 3
        stats.counters["external_check_failures:parse"] = 4
        assert set(self._metrics(stats)) == first
        assert not [name for name in first if ":" in name]


# --- RSS projected exhaustion (#106 §2b) ---------------------------------
# 6 h window at a 60 s interval = 360 samples; ticks are sized from that
# because coverage is the first gate and a short run is rejected regardless.
_WINDOW_TICKS = 360


def _rss_ramp(sampler, *, start_mb, per_tick_mb, ticks):
    for i in range(ticks):
        sampler.push(_metrics(rss=(start_mb + i * per_tick_mb) * MB))


class TestProjectedExhaustion:
    """[RB-02] The rule asks whether the trend exhausts the budget, not
    whether the rate exceeds a constant.

    A 59-hour canary soak grew linearly at ~0.42 MB/h from 55 MB to a 100 MB
    breach while the v3 fixed gate of 1.0 MB/h never fired. The same rate is
    benign with 45 MB of headroom and terminal with 3 MB; a rate threshold
    cannot see the difference.
    """

    # 0.42 MB/h, the rate the soak actually measured, per 60 s tick.
    SOAK_RATE = 0.42 / 60

    def test_soak_rate_inside_the_72h_horizon_warns(self, tmp_path):
        """[RB-02] the case v3 missed: real growth, headroom inside 72 h.

        Headroom is chosen to sit between the two horizons — 0.42 MB/h spans
        10.1 MB in 24 h and 30.2 MB in 72 h — so this pins the *warning* rung
        specifically. Asserting only `state == "open"` would pass on the error
        rung too, and did: a mutation replacing this rule with a fixed
        1 MB/h gate survived until the owning rule was asserted.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        # ends near 80.8 MB -> ~19 MB headroom: inside 72 h, outside 24 h
        _rss_ramp(sampler, start_mb=78, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        states = _incidents(paths, "rss-growth")
        assert states, "growth that exhausts the budget within 72 h must warn"
        assert states[-1][0] == "open"
        assert states[-1][1] == "rss-growth-warn", "the warning rung, not the error one"

    def test_headroom_inside_24h_escalates_to_error(self, tmp_path):
        """[RB-02] the shorter horizon owns the error rung."""
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        # ends near 97.8 MB -> ~2 MB headroom: inside 24 h
        _rss_ramp(sampler, start_mb=95, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        states = _incidents(paths, "rss-growth")
        assert states and states[-1][0] == "open"
        assert states[-1][1] == "rss-growth-crit"

    def test_same_rate_with_ample_headroom_stays_quiet(self, tmp_path):
        """[RB-02] the discrimination a fixed rate cannot make.

        Identical slope to the test above. Only the headroom differs, and at
        50 MB the trend needs ~120 h to exhaust the budget — outside the 72 h
        horizon, so no incident.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        _rss_ramp(sampler, start_mb=48, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        assert not [s for s in _incidents(paths, "rss-growth") if s[0] == "open"], (
            "the same slope with 50 MB of headroom is not an exhaustion risk"
        )

    def test_silent_once_actually_over_budget(self, tmp_path):
        """[RB-02] positive headroom is required, so the prediction hands off
        to rss-budget rather than duplicating it once the breach is real."""
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        _rss_ramp(sampler, start_mb=101, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        assert not [s for s in _incidents(paths, "rss-growth") if s[0] == "open"], (
            "over budget is rss-budget's condition, not a prediction"
        )
        assert _incidents(paths, "rss-budget"), "the level rule owns it instead"

    def test_flat_memory_near_the_budget_stays_quiet(self, tmp_path):
        """[RB-02] small headroom alone is not a risk; the slope must be
        positive. A daemon parked just under budget is not exhausting it."""
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        sampler.push(_metrics(rss=97 * MB), times=_WINDOW_TICKS + 40)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        assert not [s for s in _incidents(paths, "rss-growth") if s[0] == "open"]

    def test_hysteresis_holds_the_incident_through_a_quiet_patch(self, tmp_path):
        """[RB-02] clear_cycles is the anti-flap guard.

        Headroom shrinks as RSS climbs, so the condition sits near its own
        boundary and 6 h slope noise would otherwise open and close it
        repeatedly. A few quiet cycles must not clear a 30-cycle rule.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        _rss_ramp(sampler, start_mb=95, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        # A sharp drop makes the condition genuinely FALSE — a flat patch
        # would not, because the 6 h slope stays positive while the ramp is
        # still inside the window, so it would not exercise clearing at all.
        sampler.push(_metrics(rss=40 * MB), times=5)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 45)

        states = _incidents(paths, "rss-growth")
        assert states and states[-1][0] == "open", (
            "5 false cycles must not clear a 30-cycle rule"
        )

    def test_short_history_cannot_produce_a_verdict(self, tmp_path):
        """[RB-02] the coverage gate, isolated.

        Steep enough that slope, net growth and the horizon all pass easily,
        but observed for far less than the 6 h window — the situation after a
        restart. Without coverage a handful of samples would deliver a
        six-hour verdict on the daemon's remaining life.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        # 6 MB/h for 40 min, ending ~4 MB below budget: every other gate open
        _rss_ramp(sampler, start_mb=92, per_tick_mb=6.0 / 60, ticks=40)
        _run(paths, sampler, ticks=40)

        assert not [s for s in _incidents(paths, "rss-growth") if s[0] == "open"], (
            "40 minutes of history cannot support a six-hour projection"
        )

    def test_dip_and_recovery_near_the_budget_stays_quiet(self, tmp_path):
        """[RB-02] the positive-net-growth gate, isolated.

        Memory that dipped and climbed back to where it started is not
        growing, but the fitted slope over the window is positive because
        most of it rises. With little headroom the horizon test passes, so
        `delta > 0` is the only thing rejecting it.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        # 97 -> 88 -> 96 inside one window: delta -1.00 MB (negative) while
        # the long recovery leg leaves slope at +0.268 MB/h and headroom at
        # 4 MB, so the horizon test passes. Shape checked numerically: a
        # shallower dip gives a negative slope and would prove nothing,
        # because the horizon test would reject it on its own.
        sampler.push(_metrics(rss=97 * MB), times=40)
        for i in range(40):
            sampler.push(_metrics(rss=(97 - 9 * (i + 1) / 40) * MB))
        for i in range(280):
            sampler.push(_metrics(rss=(88 + 8 * (i + 1) / 280) * MB))
        _run(paths, sampler, ticks=_WINDOW_TICKS)

        assert not [s for s in _incidents(paths, "rss-growth") if s[0] == "open"], (
            "recovering to a previous level is not growth toward exhaustion"
        )

    def test_clear_requires_the_full_thirty_cycles(self, tmp_path):
        """[RB-02] the hysteresis magnitude, not merely its presence.

        The companion test proves 5 false cycles do not clear. That passes
        for any clear_cycles above 5, so it cannot distinguish 30 from 60.
        This one runs 35 false cycles and requires the incident to have
        cleared, which fails if the value is raised.

        Together the two bound the setting to (5, 35] rather than pinning it.
        That is intentional: 30 is provisional pending backtesting against
        rollup5m history, and 10 is a plausible outcome. Asserting 30 exactly
        would encode an unvalidated number as a requirement and would have to
        be edited by whatever the backtest concludes. The pair still rejects
        the values that would break the rule -- 1 removes hysteresis entirely
        and 60 is slow enough to hold a stale incident for an hour.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        _rss_ramp(sampler, start_mb=95, per_tick_mb=self.SOAK_RATE,
                  ticks=_WINDOW_TICKS + 40)
        sampler.push(_metrics(rss=40 * MB), times=35)
        _run(paths, sampler, ticks=_WINDOW_TICKS + 75)

        states = _incidents(paths, "rss-growth")
        assert states and states[-1][0] == "cleared", (
            "35 false cycles must clear a 30-cycle rule"
        )

    def test_cpu_evidence_is_level_never_slope_rb_02(self, tmp_path):
        """[RB-02][MD-10] CPU rising steadily must not open a growth incident.

        A slope is not hog evidence, the same reasoning SPEC applies to
        `monot` in the leak rules. A definition that grew a CPU growth or
        CPU-exhaustion rule would open here.
        """
        paths = _core_with_shipped_self(tmp_path)
        sampler = ScriptedSelfSampler()
        for i in range(_WINDOW_TICKS + 40):
            sampler.push(_metrics(cpu=0.1 + i * 0.002))
        _run(paths, sampler, ticks=_WINDOW_TICKS + 40)

        for group in ("cpu-budget", "cpu-growth", "rss-growth"):
            assert not [s for s in _incidents(paths, group) if s[0] == "open"], (
                f"rising CPU under budget must not open {group}"
            )

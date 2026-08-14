"""[SA-01][SA-06][SA-07][SA-05][CA-04][CA-07][CA-08] Engine: scheduler,
rings, and the sampling pipeline driven by a real loaded definition."""

from __future__ import annotations

import importlib.resources

import pytest

from ftmon.clock import FakeClock
from ftmon.definitions import load_text
from ftmon.engine.pipeline import PROMOTION_LIMIT_PER_MONITOR, Pipeline
from ftmon.engine.rings import RingStore
from ftmon.engine.scheduler import DueTable, Scheduler
from ftmon.model import EntitySample, Snapshot, TriBool
from ftmon.sources.base import SOURCE_DECLS

# --- scheduler ---


def test_due_table_first_run_immediate_then_interval():
    """[SA-01] a fresh monitor runs on the next tick, then at its interval."""
    dt = DueTable()
    overruns: list[str] = []
    dt.add("m", 60.0, mono_now=100.0)
    assert dt.due(100.0, overruns.append) == ["m"]
    assert dt.due(105.0, overruns.append) == []
    assert dt.due(160.0, overruns.append) == ["m"]
    assert overruns == []


def test_due_table_overrun_skips_not_queues():
    """[SA-01] falling 3 intervals behind runs once and skips the missed
    slots (counted) - no catch-up burst."""
    dt = DueTable()
    overruns: list[str] = []
    dt.add("m", 60.0, mono_now=0.0)
    assert dt.due(0.0, overruns.append) == ["m"]
    assert dt.due(200.0, overruns.append) == ["m"]  # due at 60; 120,180 missed
    assert overruns == ["m", "m"]
    assert dt.due(210.0, overruns.append) == []  # next due 260, not before
    assert dt.due(260.0, overruns.append) == ["m"]


def test_scheduler_reports_gap_and_reanchors():
    """[SA-07] waking far past the deadline (suspend) flags the gap once and
    skips missed ticks instead of replaying them."""
    clock = FakeClock(mono=0.0)
    ticks: list[tuple[float, float]] = []

    original_sleep = clock.sleep_until

    def sleeping(deadline):
        # simulate a 10-minute suspend during the third sleep
        if len(ticks) == 2:
            clock.advance(600)
        original_sleep(deadline)

    clock.sleep_until = sleeping  # type: ignore[method-assign]

    def on_tick(wall, mono, gap_s):
        ticks.append((mono, gap_s))

    Scheduler(clock, tick_s=5.0).run(on_tick, should_stop=lambda: len(ticks) >= 4)
    gaps = [g for _, g in ticks]
    assert gaps[0] == 0.0 and gaps[1] == 0.0
    assert gaps[2] >= 595.0  # the suspend surfaced exactly once
    assert gaps[3] == 0.0  # re-anchored: next tick is normal again
    assert ticks[3][0] - ticks[2][0] <= 6.0  # no burst of catch-up ticks


# --- rings ---


def test_rings_capacity_from_windows_and_nan_guard():
    """[CA-04][DM-01] capacity = window/interval + slack; NaN never enters."""
    r = RingStore()
    r.configure("m", 60.0, {"rss": 900.0})  # 15 samples + 2
    for i in range(40):
        r.append("m", "e1", "rss", 1000.0 + i * 60, float(i))
    assert len(r.window("m", "e1", "rss", 0)) == 17
    r.append("m", "e1", "rss", 9999.0, float("nan"))
    assert r.last("m", "e1", "rss") != float("nan")  # unchanged tail
    # un-windowed metric defaults to last-value-only capacity
    r.append("m", "e1", "other", 1.0, 1.0)
    r.append("m", "e1", "other", 2.0, 2.0)
    r.append("m", "e1", "other", 3.0, 3.0)
    assert len(r.window("m", "e1", "other", 0)) == 2


def test_rings_eviction_lru_respects_protection():
    """[CA-04] over budget: LRU unprotected entities evicted whole."""
    r = RingStore(max_bytes=48 * 30)  # room for ~30 points
    r.configure("m", 60.0, {"x": 6000.0})
    counts: list[str] = []
    for eid, base in (("old", 0.0), ("mid", 500.0), ("new", 1000.0)):
        for i in range(20):
            r.append("m", eid, "x", base + i, 1.0)
    r.evict_if_over(protected=lambda mon, e: e == "old", counter=counts.append)
    assert r.window("m", "old", "x", 0)  # protected survived despite being LRU
    assert not r.window("m", "mid", "x", 0)  # unprotected LRU evicted
    assert counts.count("ring_evictions") >= 1


# --- pipeline, driven by a real definition ---

LEAKDEF = """
schema = 1
exempt = [ 'matches(name, "^skipme$")' ]
[monitor]
name = "leak"
description = "test leak monitor"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "process"
[parameters]
warn_bph = { value = 1000000, doc = "warn bytes/hour" }
[promotion]
expr = 'monot(rss_bytes, "15m") >= 0.8 and delta(rss_bytes, "15m") > 0'
[[rule]]
id = "grow"
when = 'slope(rss_bytes, "15m") * 3600 > warn_bph'
severity = "warning"
confirm_cycles = 3
message = "{entity} leaking"
"""


class ScriptedSampler:
    """Minimal fixture: returns the snapshot scripted for the current call."""

    decl = SOURCE_DECLS["process"]

    def __init__(self):
        self.script: list[list[tuple[str, dict, dict]]] = []
        self.calls = 0

    def push(self, *entities):
        self.script.append(list(entities))

    def sample(self, now, deadline_mono, options):
        ents = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Snapshot(
            source="process",
            ts=now,
            entities=tuple(
                EntitySample(entity_id=e, attrs=a, metrics=m) for e, a, m in ents
            ),
        )


class NullWriter:
    """Records persistence calls; the real TickWriter is store-tested."""

    def __init__(self):
        self.samples: list[tuple[str, str, str, float]] = []
        self.entities: dict[tuple[str, str], dict] = {}
        self.gone: list[str] = []
        self.forgotten: list[tuple[str, str]] = []
        self._sids: dict[tuple, tuple] = {}

    def series_id(self, monitor, entity_id, metric, durable):
        return self._sids.setdefault((monitor, entity_id, metric), (monitor, entity_id, metric))

    def add_sample(self, sid, ts, value):
        self.samples.append((*sid, value))

    def upsert_entity(self, monitor, entity_id, ts, attrs, gone_ts=None):
        self.entities[(monitor, entity_id)] = attrs
        if gone_ts is not None:
            self.gone.append(entity_id)

    def forget_entity(self, monitor, entity_id):
        self.forgotten.append((monitor, entity_id))


def _run_cycles(mdef, sampler, cycles, start=1_700_000_000.0, step=60.0):
    rings = RingStore()
    windows: dict[str, float] = {}
    for metric, w in mdef.windows:
        windows[metric] = max(w, windows.get(metric, 0.0))
    rings.configure(mdef.name, mdef.interval_s, windows)
    counts: dict[str, int] = {}
    pipe = Pipeline(
        {"process": sampler}, rings,
        lambda n: counts.__setitem__(n, counts.get(n, 0) + 1),
        gone_grace_s=300.0,
    )
    writer = NullWriter()
    outcomes = []
    for i in range(cycles):
        outcomes = pipe.run_monitor(mdef, start + i * step, 10**9, writer, {})
    return pipe, writer, outcomes, counts


def grower(i, eid="leaky:1:100", rss0=1_000_000, attrs=None):
    return (
        eid, attrs if attrs is not None else {"name": "leaky"},
        {"rss_bytes": float(rss0 + i * 200_000), "cpu_pct": 1.0},
    )


def test_pipeline_unknown_then_fires_and_promotes():
    """[SA-06][EX-06][SA-05] early cycles evaluate UNKNOWN (insufficient
    window), sustained growth flips the rule TRUE and promotes the entity."""
    mdef = load_text(LEAKDEF)
    s = ScriptedSampler()
    for i in range(20):
        s.push(grower(i), ("calm:2:100", {"name": "calm"}, {"rss_bytes": 5e6, "cpu_pct": 0.1}))

    early = _run_cycles(mdef, s, 2)[2]
    grow = [o for o in early if o.entity_id.startswith("leaky")][0]
    assert grow.result is TriBool.UNKNOWN  # slope needs 3+ points (CA-02)

    s2 = ScriptedSampler()
    for i in range(20):
        s2.push(grower(i), ("calm:2:100", {"name": "calm"}, {"rss_bytes": 5e6, "cpu_pct": 0.1}))
    pipe, writer, outcomes, counts = _run_cycles(load_text(LEAKDEF), s2, 20)
    leaky = [o for o in outcomes if o.entity_id.startswith("leaky")][0]
    calm = [o for o in outcomes if o.entity_id.startswith("calm")][0]
    assert leaky.result is TriBool.TRUE
    assert calm.result is TriBool.FALSE
    assert "leaky:1:100" in pipe.promoted("leak")  # [SA-05c]
    assert counts.get("eval_unknown_total", 0) > 0  # early UNKNOWNs were counted


def test_pipeline_message_entity_prefers_display_sa_09():
    """[SA-09] {entity} in a rule message resolves to the sampler's display
    attr (e.g. 'agent (MainThread)') instead of the generic kernel name."""
    mdef = load_text(LEAKDEF)
    s = ScriptedSampler()
    for i in range(20):
        s.push(grower(i, attrs={"name": "MainThread", "display": "agent (MainThread)"}))
    _, _, outcomes, _ = _run_cycles(mdef, s, 20)
    leaky = [o for o in outcomes if o.entity_id.startswith("leaky")][0]
    assert leaky.result is TriBool.TRUE
    assert leaky.message == "agent (MainThread) leaking"


def test_pipeline_message_entity_falls_back_to_name_without_display_sa_09():
    """[SA-09] entities with no display attr still resolve {entity} to name
    (guards the pre-SA-09 fixture behavior)."""
    mdef = load_text(LEAKDEF)
    s = ScriptedSampler()
    for i in range(20):
        s.push(grower(i))
    _, _, outcomes, _ = _run_cycles(mdef, s, 20)
    leaky = [o for o in outcomes if o.entity_id.startswith("leaky")][0]
    assert leaky.result is TriBool.TRUE
    assert leaky.message == "leaky leaking"


def test_pipeline_exempt_is_transient_only_and_purges_history_ca_07():
    """[CA-07] Exempt context stays in rings but never reaches persistence."""
    mdef = load_text(LEAKDEF)
    s = ScriptedSampler()
    for i in range(6):
        s.push(
            ("skipme:9:100", {"name": "skipme"},
             {"rss_bytes": float(1e6 + i * 1e6), "cpu_pct": 99.0})
        )
    pipe, writer, outcomes, _ = _run_cycles(mdef, s, 6)
    assert outcomes == []  # no rule evaluations for the exempt entity
    assert not any(e == "skipme:9:100" for _m, e, _met, _v in writer.samples)
    assert writer.forgotten == [("leak", "skipme:9:100")] * 6
    assert pipe._rings.window("leak", "skipme:9:100", "rss_bytes", 0)


def test_pipeline_source_shared_once_per_tick():
    """[SA-06] two monitors on one source share one sampler call per tick."""
    mdef_a = load_text(LEAKDEF)
    b_text = LEAKDEF.replace('name = "leak"', 'name = "leak2"')
    mdef_b = load_text(b_text)
    s = ScriptedSampler()
    s.push(grower(0))
    rings = RingStore()
    for mdef in (mdef_a, mdef_b):
        rings.configure(mdef.name, mdef.interval_s, dict(mdef.windows))
    pipe = Pipeline({"process": s}, rings, lambda n: None)
    writer = NullWriter()
    cache: dict = {}
    pipe.run_monitor(mdef_a, 1000.0, 10**9, writer, cache)
    pipe.run_monitor(mdef_b, 1000.0, 10**9, writer, cache)
    assert s.calls == 1


def test_pipeline_gone_after_grace():
    """[CA-08] an entity absent past gone_grace is marked gone and its rings
    dropped; the survivor is untouched."""
    mdef = load_text(LEAKDEF)
    s = ScriptedSampler()
    s.push(grower(0), grower(0, eid="dies:3:100"))
    for i in range(1, 10):
        s.push(grower(i))  # dies:3:100 vanishes after the first cycle
    pipe, writer, _, _ = _run_cycles(mdef, s, 10)
    assert "dies:3:100" in writer.gone
    assert pipe._rings.window("leak", "dies:3:100", "rss_bytes", 0) == []


def test_pipeline_top_n_persistence_selection():
    """[SA-05b] only top-N consumers (union of cpu and rss rankings, plus
    promoted) get persisted samples; the long tail stays rings-only."""
    text = LEAKDEF.replace("[parameters]", "[source_options]\ntop_n = 5\n[parameters]")
    mdef = load_text(text)
    s = ScriptedSampler()
    ents = [("big:1:1", {"name": "big"}, {"rss_bytes": 9e9, "cpu_pct": 0.0}),
            ("busy:2:1", {"name": "busy"}, {"rss_bytes": 1.0, "cpu_pct": 90.0})]
    # 12 meek processes with strictly increasing tiny footprints: meek00 can
    # never rank in either top-5, so it must not be persisted.
    for i in range(12):
        ents.append(
            (f"meek{i:02d}:9:{i}", {"name": f"meek{i:02d}"},
             {"rss_bytes": 100.0 + i, "cpu_pct": 0.1 + i / 100})
        )
    s.push(*ents)
    _pipe, writer, _, _ = _run_cycles(mdef, s, 1)
    persisted = {e for _m, e, _met, _v in writer.samples}
    assert {"big:1:1", "busy:2:1"} <= persisted
    assert "meek00:9:0" not in persisted
    assert len(persisted) <= 10  # union of two top-5 rankings


def test_pipeline_caps_promotion_without_changing_evaluation_or_top_n():
    """[SA-05][DM-16] broad promotion cannot create an unbounded durable set.

    Rules still evaluate for every non-exempt process, and top-N remains an
    independent persistence path even when its entity was refused promotion.
    One transition event reports the monitor-level condition rather than one
    event per refused entity or per tick.
    """
    text = LEAKDEF.replace("[parameters]", "[source_options]\ntop_n = 5\n[parameters]")
    mdef = load_text(text)
    sampler = ScriptedSampler()
    entity_ids = [f"grow{i:02d}:1:{i}" for i in range(15)]
    for tick in range(20):
        sampler.push(*[
            grower(tick, eid=entity_id, rss0=1_000_000 + i * 10_000)
            for i, entity_id in enumerate(entity_ids)
        ])

    pipe, writer, outcomes, _ = _run_cycles(mdef, sampler, 20)

    assert pipe.promoted("leak") == set(entity_ids[:PROMOTION_LIMIT_PER_MONITOR])
    assert len(outcomes) == len(entity_ids), "persistence admission never gates rules"
    assert pipe.promotion_limited_monitors(["leak"]) == 1
    assert pipe.promotion_rejections_total == 5
    # High-RSS entities sort into top-N even though the promotion cap refused
    # them; alerting and the already-bounded top-N path keep working.
    persisted = {entity for _monitor, entity, _metric, _value in writer.samples}
    assert set(entity_ids[-5:]) <= persisted
    events = pipe.drain_self_events()
    limit_events = [event for event in events if event.event_id == "promotion-limit"]
    assert len(limit_events) == 1
    assert limit_events[0].provider == "ftmon.leak"
    assert "5 refused (limit 10)" in limit_events[0].message

    flat = [
        grower(20, eid=entity_id, rss0=1_000_000 + i * 10_000)
        for i, entity_id in enumerate(entity_ids)
    ]
    for _ in range(50):
        sampler.push(*flat)
    for tick in range(20, 70):
        pipe.run_monitor(mdef, 1_700_000_000 + tick * 60, 10**9, writer, {})
    recovery_events = [
        event for event in pipe.drain_self_events()
        if event.event_id == "promotion-limit"
    ]
    assert len(recovery_events) == 1
    assert "no promotion matches are being refused" in recovery_events[0].message
    assert pipe.promotion_limited_monitors(["leak"]) == 0


# --- daemon core (composition), FakeClock-driven ---


def test_daemon_core_ticks_persist_and_hot_reload(tmp_path):
    """[PM-04][SA-01][RB-02] DaemonCore end-to-end without the blocking loop:
    definitions load, ticks persist samples and meta, and an edited
    definition file is picked up within the 30s rescan window."""
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths
    from ftmon.store.db import connect
    from ftmon.store.query import Query

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    assert set(core.monitors) == {"leak"}

    scripted = ScriptedSampler()
    for i in range(5):
        scripted.push(grower(i))
    # samplers dict is shared by reference with the pipeline, so swapping the
    # implementation here redirects sampling without re-wiring.
    core.samplers["process"] = scripted

    for _ in range(4):
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        clock.advance(60)

    conn = connect(paths.db_file, readonly=True)
    q = Query(conn)
    info = q.status(now=clock.now())
    assert info["last_tick_ts"] is not None
    rows = conn.execute("SELECT count(*) c FROM samples").fetchone()
    assert rows["c"] > 0
    conn.close()

    # PM-04: bump the definition version on disk; next tick past the rescan
    # window must load the new hash without a restart.
    text = (paths.monitors_dir / "leak.toml").read_text()
    (paths.monitors_dir / "leak.toml").write_text(text.replace("version = 1", "version = 2"))
    old_hash = core.monitors["leak"].content_hash
    clock.advance(31)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.monitors["leak"].content_hash != old_hash


def test_daemon_survives_database_locked_pm_10(tmp_path):
    """[PM-10] lock timeout on commit_tick is counted and recoverable, not fatal."""
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths
    from ftmon.store.db import connect

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    core.samplers["process"] = ScriptedSampler()
    core.samplers["process"].push(grower(0))
    core.conn.execute("PRAGMA busy_timeout = 50")

    locker = connect(paths.db_file)
    locker.execute("PRAGMA busy_timeout = 0")
    locker.execute("BEGIN IMMEDIATE")
    try:
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
    finally:
        locker.rollback()
        locker.close()

    assert core.stats.counters.get("sqlite_lock_errors") == 1
    assert core.conn.execute(
        "SELECT value FROM meta WHERE key = 'last_tick_ts'"
    ).fetchone() is None

    clock.advance(60)
    core.samplers["process"].push(grower(1))
    core.on_tick(clock.now(), clock.monotonic(), 0.0)

    assert core.conn.execute(
        "SELECT value FROM meta WHERE key = 'last_tick_ts'"
    ).fetchone() is not None
    rows = core.conn.execute(
        "SELECT message FROM events WHERE source = 'self' AND provider = 'ftmon.store'"
    ).fetchall()
    assert len(rows) == 1
    assert "locked" in rows[0]["message"].lower()
    snap = core.samplers["self"].sample(clock.now(), clock.monotonic() + 10.0, {})
    assert snap.entities[0].metrics["sqlite_lock_errors"] == 1.0


def test_daemon_rethrows_non_lock_operational_errors_pm_10(tmp_path, monkeypatch):
    """[PM-10] only lock timeouts are swallowed; other OperationalErrors still raise."""
    import sqlite3

    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    core = DaemonCore(
        paths=paths, clock=FakeClock(wall=1.0, mono=1.0), platform="linux"
    )
    core.samplers["process"] = ScriptedSampler()
    core.samplers["process"].push(grower(0))

    def boom():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(core.writer, "commit_tick", boom)
    try:
        core.on_tick(1.0, 1.0, 0.0)
        raise AssertionError("expected OperationalError")
    except sqlite3.OperationalError as exc:
        assert "disk I/O error" in str(exc)
    assert core.stats.counters.get("sqlite_lock_errors", 0) == 0


def test_sighup_reload_request_applies_next_tick_pm_11(tmp_path):
    """[PM-11] request_reload() runs the PM-04 refresh on the next tick,
    without waiting out the 30 s rescan window."""
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    scripted = ScriptedSampler()
    for i in range(3):
        scripted.push(grower(i))
    core.samplers["process"] = scripted

    core.on_tick(clock.now(), clock.monotonic(), 0.0)  # startup rescan

    text = (paths.monitors_dir / "leak.toml").read_text()
    (paths.monitors_dir / "leak.toml").write_text(text.replace("version = 1", "version = 2"))
    old_hash = core.monitors["leak"].content_hash

    clock.advance(5)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.monitors["leak"].content_hash == old_hash  # window not out, no request

    core.request_reload()
    clock.advance(5)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.monitors["leak"].content_hash != old_hash


def test_daemon_skips_disabled_monitors_md_05(tmp_path):
    """[MD-05][PM-04] enabled=false stays on disk but is not scheduled; flipping
    it off on rescan unloads a previously-enabled monitor."""
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    disabled = LEAKDEF.replace('name = "leak"', 'name = "parked"').replace(
        "enabled = true", "enabled = false", 1
    )
    (paths.monitors_dir / "parked.toml").write_text(disabled)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    assert set(core.monitors) == {"leak"}
    assert "parked" not in core.due.names()

    # Disabling a live monitor must drop it on the next PM-04 rescan.
    (paths.monitors_dir / "leak.toml").write_text(
        LEAKDEF.replace("enabled = true", "enabled = false", 1)
    )
    clock.advance(31)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.monitors == {}
    assert core.due.names() == []


WATCHDEF = """
schema = 1
[monitor]
name = "service"
description = "watchlist durability fixture"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "process"
[[rule]]
id = "down"
when = 'cpu_pct > 999'
severity = "warning"
message = "{entity} down"
"""


class DurabilityWriter(NullWriter):
    """NullWriter records ids only; durability is the point here."""

    def __init__(self):
        super().__init__()
        self.durability: dict[str, bool] = {}

    def series_id(self, monitor, entity_id, metric, durable):
        self.durability[entity_id] = durable
        return super().series_id(monitor, entity_id, metric, durable)


class SyntheticScriptedSampler(ScriptedSampler):
    """Scripts entities with a per-entity synthetic flag."""

    def sample(self, now, deadline_mono, options):
        ents = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Snapshot(
            source="process", ts=now,
            entities=tuple(
                EntitySample(entity_id=e, attrs=a, metrics=m, synthetic=s)
                for e, a, m, s in ents
            ),
        )


class TestEntityLevelDurability:
    """[DM-04][CA-08] Durability is an entity decision, not a monitor one.

    `_DURABLE_SOURCES` assigned one value per monitor, so `net`'s watchlist
    listeners were stored non-durable alongside its discovered `totals`.
    Adding `net`/`unit` to that set would have mislabelled `totals` instead;
    the synthetic flag lets one monitor hold both kinds correctly.
    """

    def _run(self, entities):
        mdef = load_text(WATCHDEF)
        sampler = SyntheticScriptedSampler()
        sampler.push(*entities)
        rings = RingStore()
        rings.configure(mdef.name, mdef.interval_s, {})
        pipe = Pipeline({"process": sampler}, rings, lambda n: None, gone_grace_s=300.0)
        writer = DurabilityWriter()
        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, writer, {})
        return writer.durability

    def test_synthetic_entity_is_durable_on_a_non_durable_source(self):
        got = self._run([("tcp:22", {}, {"cpu_pct": 1.0}, True)])
        assert got == {"tcp:22": True}

    def test_discovered_entity_on_the_same_monitor_stays_non_durable(self):
        got = self._run([
            ("tcp:22", {}, {"cpu_pct": 1.0}, True),
            ("totals", {}, {"cpu_pct": 1.0}, False),
        ])
        assert got == {"tcp:22": True, "totals": False}


class TestPipelineStageTiming:
    """[RB-02][SA-06][TS-03] Sampling is timed apart from evaluation.

    "The tick is slow" has two very different answers -- a sampler blocking on
    /proc, or rule evaluation across many entities -- and a single `cycle_s`
    cannot tell them apart (#106).
    """

    class _SlowSampler(ScriptedSampler):
        """Charges the injected clock for the time a real sampler would block."""

        def __init__(self, clock, cost):
            super().__init__()
            self._clock, self._cost = clock, cost

        def sample(self, now, deadline_mono, options):
            self._clock.advance(self._cost)
            return super().sample(now, deadline_mono, options)

    def _pipeline(self, sampler, clock):
        rings = RingStore()
        mdef = load_text(LEAKDEF)
        rings.configure(mdef.name, mdef.interval_s, {m: w for m, w in mdef.windows})
        return mdef, Pipeline({"process": sampler}, rings, lambda n: None,
                              gone_grace_s=300.0, clock=clock)

    def test_sampling_cost_is_attributed_to_the_sampler(self):
        """[SA-06] a blocking sampler shows up as sample_s, not pipeline time."""
        clock = FakeClock()
        sampler = self._SlowSampler(clock, 2.0)
        sampler.push(grower(0))
        mdef, pipe = self._pipeline(sampler, clock)

        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, NullWriter(), {})

        assert pipe.sample_s == pytest.approx(2.0)
        assert pipe.evaluate_s == pytest.approx(0.0), "sampling must not double-count"

    def test_shared_snapshot_is_sampled_once_per_tick(self):
        """[SA-06] a cache hit costs nothing and is not counted again."""
        clock = FakeClock()
        sampler = self._SlowSampler(clock, 1.0)
        sampler.push(grower(0))
        mdef, pipe = self._pipeline(sampler, clock)
        cache: dict = {}

        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, NullWriter(), cache)
        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, NullWriter(), cache)

        assert pipe.sample_s == pytest.approx(1.0), "second call reuses the snapshot"

    def test_non_process_sources_are_counted_too(self):
        """[SA-06][RB-02] `sampling_s` covers every shared sample, not only
        the process source.

        Counting only `process` would push disk, net, unit and self sampling
        into `pipeline_s` — replacing one mislabelled gauge with another
        (review of PR #126). External check *preparation* runs before the
        monitor loop and is outside both.
        """
        diskdef = """
schema = 1
[monitor]
name = "diskwatch"
description = "non-process sampler timing fixture"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "disk"
[[rule]]
id = "full"
when = 'used_pct > 99'
severity = "warning"
message = "{entity} full"
"""
        clock = FakeClock()
        sampler = self._SlowSampler(clock, 1.5)
        sampler.push(("/", {}, {"used_pct": 10.0}))
        rings = RingStore()
        mdef = load_text(diskdef)
        rings.configure(mdef.name, mdef.interval_s, {m: w for m, w in mdef.windows})
        pipe = Pipeline({"disk": sampler}, rings, lambda n: None,
                        gone_grace_s=300.0, clock=clock)

        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, NullWriter(), {})

        assert pipe.sample_s == pytest.approx(1.5)

    def test_accumulates_across_monitors_within_a_tick(self):
        """[RB-02] one tick runs many monitors; the gauge is about the tick."""
        clock = FakeClock()
        sampler = self._SlowSampler(clock, 0.5)
        sampler.push(grower(0))
        mdef, pipe = self._pipeline(sampler, clock)

        pipe.run_monitor(mdef, 1_700_000_000.0, 10**9, NullWriter(), {})
        pipe.run_monitor(mdef, 1_700_000_060.0, 10**9, NullWriter(), {})

        assert pipe.sample_s == pytest.approx(1.0)


def test_locked_tick_reports_only_the_stages_it_ran_pm_10(tmp_path):
    """[PM-10][RB-02] The lock path must not publish a previous tick's stages.

    It returns before actions, outbox and retention run. Zeroing at each
    stage instead of at tick entry left those four gauges describing the last
    successful tick, so the breakdown of the tick an operator is actually
    investigating would be fiction (review of PR #126).
    """
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths
    from ftmon.store.db import connect

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")
    core.samplers["process"] = ScriptedSampler()
    core.samplers["process"].push(grower(0))
    core.conn.execute("PRAGMA busy_timeout = 50")

    # Seed every counter as though earlier ticks had done work.
    stages = ("sampling_seconds_total", "pipeline_seconds_total",
              "commit_seconds_total", "actions_outbox_seconds_total",
              "retention_seconds_total", "prune_seconds_total",
              "reap_seconds_total")
    for name in stages:
        setattr(core.stats, name, 9.0)

    locker = connect(paths.db_file)
    locker.execute("PRAGMA busy_timeout = 0")
    locker.execute("BEGIN IMMEDIATE")
    try:
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
    finally:
        locker.rollback()
        locker.close()

    assert core.stats.counters.get("sqlite_lock_errors") == 1
    # A counter records work done. The stages after the failed commit never
    # ran, so their counters must not advance -- and must not be zeroed
    # either, since earlier ticks' work is real and cumulative.
    for name in ("actions_outbox_seconds_total", "retention_seconds_total",
                 "prune_seconds_total", "reap_seconds_total"):
        assert getattr(core.stats, name) == 9.0, f"{name} advanced without running"
    # The commit did run, and blocking to the busy_timeout is the most
    # expensive commit there is -- it must still be counted.
    assert core.stats.commit_seconds_total >= 9.0
    assert core.stats.sampling_seconds_total >= 9.0


def test_self_sampler_publishes_the_last_completed_tick_rb_02(tmp_path):
    """[RB-02] Stage gauges must reach the self sampler, not arrive as zeros.

    The sampler runs inside the monitor loop, so it reports the last
    *completed* tick -- the same contract as `cycle_s`, whose declaration
    says "last full tick duration". An earlier revision zeroed the gauges at
    tick entry to keep the PM-10 path honest; that also wiped them before the
    sampler ran, so every stage published 0.000 forever. Unit tests passed and
    the canary caught it, which is why this asserts the published value rather
    than the field.
    """
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)
    shipped = (
        importlib.resources.files("ftmon.definitions") / "builtins" / "self.toml"
    ).read_text(encoding="utf-8")
    (paths.monitors_dir / "self.toml").write_text(shipped, encoding="utf-8")

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")

    class _CostlySampler(ScriptedSampler):
        def sample(self, now, deadline_mono, options):
            clock.advance(0.25)          # charge the injected clock
            return super().sample(now, deadline_mono, options)

    core.samplers["process"] = _CostlySampler()
    core.samplers["process"].push(grower(0))

    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.stats.sampling_seconds_total > 0.0, "the tick counted its sampling"

    # Second tick: the self monitor is sampled *inside* the loop, so what it
    # persists is what an operator actually reads. Asserting the stored value
    # rather than the field is the whole point -- the field was always right.
    clock.advance(60)
    core.samplers["process"].push(grower(1))
    core.on_tick(clock.now(), clock.monotonic(), 0.0)

    stored = core.conn.execute(
        "SELECT s.value FROM series se JOIN samples s ON s.series_id = se.id "
        "WHERE se.monitor = 'self' AND se.metric = 'sampling_seconds_total' "
        "ORDER BY s.ts DESC LIMIT 1"
    ).fetchone()
    assert stored is not None, "the self monitor never persisted sampling_s"
    assert stored[0] > 0.0, "stage gauges reached the sampler as zero"


def test_stage_counters_accumulate_across_sparse_ticks_rb_02(tmp_path):
    """[RB-02] Counters, not last-tick gauges, because stages are sparse.

    The self monitor samples every 60 s while ticks run every 5 s, so a
    last-tick reading of a stage that runs on only some ticks is 0 almost
    always: measured on the canary as 0 in 10 of 10 samples for sampling and
    retention, against 6 of 10 for commit, which every tick performs. A
    counter makes the one-tick publication lag harmless because each sample
    observes all work completed up to that point.
    """
    from ftmon.daemon import DaemonCore
    from ftmon.paths import get_paths

    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    paths = get_paths(env)
    paths.ensure()
    (paths.monitors_dir / "leak.toml").write_text(LEAKDEF)

    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, platform="linux")

    class _CostlySampler(ScriptedSampler):
        def sample(self, now, deadline_mono, options):
            clock.advance(0.25)
            return super().sample(now, deadline_mono, options)

    core.samplers["process"] = _CostlySampler()
    core.samplers["process"].push(grower(0))

    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    after_first = core.stats.sampling_seconds_total
    assert after_first >= 0.25

    # A tick with nothing due adds nothing, and must not reset the total --
    # that is the whole difference from a last-tick gauge.
    clock.advance(5)
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.stats.sampling_seconds_total == pytest.approx(after_first)

    # A later tick that does work advances it again.
    clock.advance(60)
    core.samplers["process"].push(grower(1))
    core.on_tick(clock.now(), clock.monotonic(), 0.0)
    assert core.stats.sampling_seconds_total > after_first

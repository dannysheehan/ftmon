"""[TS-04][IN-03][IN-05][RB-03][SA-05][SA-07] M4 scenario-library cases
driven through the real DaemonCore: ladder up/down, flap guard, identity
churn bounds, and suspend/resume gaps — the in-process twins of the TS-05
subprocess runs, so an e2e failure can be bisected against these."""

from __future__ import annotations

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.sources.fixtures import fixture_samplers, scenario
from ftmon.store.db import connect
from tests.unit.test_engine import LEAKDEF
from tests.unit.test_m2_integration import core_env, notifications, tick_n  # noqa: F401

DISKDEF = """
schema = 1
[monitor]
name = "space"
description = "disk ladder test"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "disk"
[[rule]]
id = "crit"
group = "space"
when = 'used_pct > 95'
severity = "critical"
confirm_cycles = 2
clear_cycles = 2
message = "{entity} critically full"
[[rule]]
id = "warn"
group = "space"
when = 'used_pct > 85'
severity = "warning"
confirm_cycles = 2
clear_cycles = 2
message = "{entity} filling up"
"""

FLAPDEF = """
schema = 1
[monitor]
name = "watchdog"
description = "service flap test"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "unit"
[[rule]]
id = "down"
when = 'present == 0'
severity = "warning"
confirm_cycles = 1
clear_cycles = 1
message = "{entity} is down"
"""


def _core(paths, name=None):
    clock = FakeClock(wall=1_700_000_000.0, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock)
    if name is not None:
        core.samplers.update(fixture_samplers(scenario(name)))
    return core, clock


def test_disk_ladder_escalate_downgrade_recover(core_env):  # noqa: F811
    """[IN-03][TS-04] the disk-ladder-updown scenario walks one incident up
    and down the rung ladder: one open at warning, one escalate to critical,
    a *silent* downgrade (history-only), one recover — all on a single
    incident row, because the rungs share group 'space'."""
    paths = core_env
    (paths.monitors_dir / "leak.toml").unlink()  # only the disk monitor runs
    (paths.monitors_dir / "space.toml").write_text(DISKDEF)
    core, clock = _core(paths, "disk-ladder-updown")

    tick_n(core, clock, 30)  # plateau schedule ends at 20m; recover ~21m

    notes = notifications(paths)
    kinds = [n["kind"] for n in notes]
    # No "downgrade" notification kind exists at all; the downgrade must
    # leave no delivery trace. Renotifies between escalate and recover are
    # legitimate IN-02 backoff behavior, not ladder noise.
    assert set(kinds) <= {"open", "escalate", "renotify", "recover"}
    assert kinds.count("open") == 1 and kinds.count("escalate") == 1
    assert kinds.count("recover") == 1
    assert kinds.index("open") < kinds.index("escalate") < kinds.index("recover")
    opens = [n for n in notes if n["kind"] == "open"]
    escalates = [n for n in notes if n["kind"] == "escalate"]
    assert opens[0]["severity"] == 2 and escalates[0]["severity"] == 4
    # the recovery names the true peak, not the downgraded-to severity
    recover = next(n for n in notes if n["kind"] == "recover")
    assert "peak critical" in recover["body"]

    conn = connect(paths.db_file, readonly=True)
    rows = conn.execute("SELECT * FROM incidents").fetchall()
    assert len(rows) == 1  # the whole ladder shares one incident (IN-03)
    assert rows[0]["state"] == "cleared" and rows[0]["clear_reason"] == "recovered"
    assert rows[0]["severity"] == 2  # downgraded back to warning before clearing
    history = [h["kind"] for h in conn.execute(
        "SELECT kind FROM incident_history ORDER BY seq").fetchall()]
    assert "downgrade" in history  # silent, but never invisible


def test_service_flap_guard_marks_flapping(core_env):  # noqa: F811
    """[IN-05][TS-04] service-flap's quick open/clear cycles trip the flap
    guard: once three clears land inside the 10-minute window, the next open
    arrives pre-marked '(flapping) ' and the incident row records it."""
    paths = core_env
    (paths.monitors_dir / "leak.toml").unlink()  # only the unit monitor runs
    (paths.monitors_dir / "watchdog.toml").write_text(FLAPDEF)
    core, clock = _core(paths, "service-flap")

    tick_n(core, clock, 25)  # flips end at 20m; last clear lands by then

    notes = notifications(paths)
    opens = [n for n in notes if n["kind"] == "open"]
    assert len(opens) >= 4  # five down phases; the guard needs history first
    assert not opens[0]["body"].startswith("(flapping) ")
    assert any(n["body"].startswith("(flapping) ") for n in opens[1:])

    conn = connect(paths.db_file, readonly=True)
    last = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT 1").fetchone()
    assert last["flapping"] == 1


def test_proc_churn_bounds_db_and_rings(core_env):  # noqa: F811
    """[RB-03][SA-05][TS-04] ~5800 churned process identities over 20 minutes
    must not blow up the samples store (top-N persistence selection) or ring
    memory, and flat metrics must open nothing."""
    paths = core_env
    # Same leak monitor, explicit top_n: churn pressure hits the SA-05b path.
    (paths.monitors_dir / "leak.toml").write_text(
        LEAKDEF.replace("[parameters]", "[source_options]\ntop_n = 15\n[parameters]"))
    core, clock = _core(paths, "proc-churn-300")

    tick_n(core, clock, 20)

    conn = connect(paths.db_file, readonly=True)
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM series").fetchone()[0]
    assert distinct < 800  # vs ~5800 seen identities: persistence is selective
    assert core.rings.mem_bytes() < 32 * 2**20
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert notifications(paths) == []
    # tick loop completed all 20 cycles without wedging or crashing
    last_tick = conn.execute(
        "SELECT value FROM meta WHERE key = 'last_tick_ts'").fetchone()
    assert last_tick is not None
    assert float(last_tick["value"]) == clock.now() - 60.0  # the 20th tick's wall


def test_suspend_gap_counted_once_and_harmless(core_env):  # noqa: F811
    """[SA-07][TS-04] a laptop-lid gap (scheduler passes gap_s > 0 after
    suspend) is counted exactly once, produces no notifications, and the
    next ticks proceed normally — time jumps are survived, not alerted on."""
    paths = core_env  # keeps the leak.toml monitor; steady data never fires
    core, clock = _core(paths, "steady")

    tick_n(core, clock, 5)
    clock.advance(3600.0)
    core.on_tick(clock.now(), clock.monotonic(), 3600.0)
    clock.advance(60.0)
    before = clock.now()
    tick_n(core, clock, 5)

    assert core.stats.counters.get("clock_gaps") == 1
    assert notifications(paths) == []
    conn = connect(paths.db_file, readonly=True)
    last_tick = float(conn.execute(
        "SELECT value FROM meta WHERE key = 'last_tick_ts'").fetchone()["value"])
    assert last_tick >= before  # post-gap ticks kept advancing the meta row

"""[IN-01][IN-02][IN-03][IN-04][IN-05][IN-06][IN-07] Incident state machine.

Pure-function table tests: no DB, no clock, no notifier — exactly what
IN-06 buys us. T/F/U shorthand drives cycles through step_group.
"""

from __future__ import annotations

from ftmon.engine.incidents import (
    BACKOFF_S,
    GroupConfig,
    RungConfig,
    RungEval,
    clear_for_entity_gone,
    empty_state,
    step_group,
)
from ftmon.model import ActionEffect, NotifyEffect, RecordEffect, TriBool

T, F, U = TriBool.TRUE, TriBool.FALSE, TriBool.UNKNOWN

WARN = RungConfig(rule_id="warn", severity=2, confirm_cycles=3, clear_cycles=3)
CRIT = RungConfig(rule_id="crit", severity=3, confirm_cycles=2, clear_cycles=2)
LADDER = GroupConfig(monitor="disk", entity_id="/", group="space", rungs=(CRIT, WARN))
SINGLE = GroupConfig(monitor="hog", entity_id="p:1:1", group="hog",
                     rungs=(RungConfig("hog", 2, 3, 3, action="calm-down"),))


def run(cfg, sequence, start=1000.0, step=60.0, state=None):
    """Drive cycles; sequence items are {rule: TriBool} dicts. Returns
    (final_state, [(cycle_index, effect), ...])."""
    st = state or empty_state(cfg)
    all_effects = []
    now = start
    for i, evals in enumerate(sequence):
        st, effects = step_group(
            cfg, st, {k: RungEval(v, f"{k} fired") for k, v in evals.items()}, now
        )
        all_effects.extend((i, e) for e in effects)
        now += step
    return st, all_effects


def kinds(effects):
    out = []
    for i, e in effects:
        if isinstance(e, NotifyEffect):
            out.append((i, f"notify:{e.notification.kind}"))
        elif isinstance(e, ActionEffect):
            out.append((i, f"action:{e.action}"))
        elif isinstance(e, RecordEffect):
            out.append((i, f"record:{e.kind}"))
    return out


# --- confirmation (IN-01) ---


def test_opens_only_after_confirm_cycles():
    """[IN-01][IN-06] two TRUEs are not enough at confirm_cycles=3."""
    st, fx = run(SINGLE, [{"hog": T}, {"hog": T}])
    assert st.core is None and fx == []
    st, fx = run(SINGLE, [{"hog": T}] * 3)
    assert st.core is not None and st.core.state == "open"
    assert (2, "notify:open") in kinds(fx)


def test_false_resets_confirmation():
    """[IN-01] T T F T T T: the F restarts the count."""
    st, fx = run(SINGLE, [{"hog": T}, {"hog": T}, {"hog": F}, {"hog": T}, {"hog": T}])
    assert st.core is None
    st, fx = run(SINGLE, [{"hog": T}, {"hog": T}, {"hog": F}] + [{"hog": T}] * 3)
    assert st.core.state == "open"


def test_unknown_freezes_both_directions():
    """[IN-01] UNKNOWN neither confirms nor clears: T T U T opens (U was a
    hole, not a reset); and an open incident survives a run of UNKNOWNs."""
    st, _ = run(SINGLE, [{"hog": T}, {"hog": T}, {"hog": U}, {"hog": T}])
    assert st.core is not None and st.core.state == "open"
    st, fx = run(SINGLE, [{"hog": U}] * 10, state=st, start=2000.0)
    assert st.core.state == "open"  # missing data is not evidence of recovery


def test_action_runs_on_open_only():
    """[AC-02 boundary] the action effect fires exactly once, at open."""
    st, fx = run(SINGLE, [{"hog": T}] * 8)
    assert [k for k in kinds(fx) if k[1].startswith("action:")] == [(2, "action:calm-down")]


# --- ladder semantics (IN-03) ---


def test_ladder_escalates_with_notify_and_backoff_reset():
    """[IN-03] warn opens; crit confirming later raises severity in place,
    notifies, and resets the backoff schedule."""
    seq = [{"warn": T, "crit": F}] * 3 + [{"warn": T, "crit": T}] * 2
    st, fx = run(LADDER, seq)
    assert st.core.severity == 3 and st.core.owning_rule == "crit"
    assert (2, "notify:open") in kinds(fx)
    assert (4, "notify:escalate") in kinds(fx)
    assert st.core.backoff_tier == 0  # reset by escalation


def test_ladder_downgrade_is_silent():
    """[IN-03] crit clearing while warn holds lowers severity with a record
    effect but NO notification."""
    seq = ([{"warn": T, "crit": T}] * 3  # opens at warn (cycle2)? crit confirms cycle1? see below
           )
    # Build explicitly: crit confirms at cycle 1 (confirm=2) -> open at crit.
    seq = [{"warn": T, "crit": T}] * 2  # crit confirmed -> open severity 3
    seq += [{"warn": T, "crit": F}] * 2  # crit clears (clear_cycles=2) at cycle 3
    seq += [{"warn": T, "crit": F}] * 2  # warn keeps holding
    st, fx = run(LADDER, seq)
    assert st.core.state == "open" and st.core.severity == 2
    ks = kinds(fx)
    assert (3, "record:downgrade") in ks or (4, "record:downgrade") in ks
    assert not any(k[1] == "notify:escalate" and k[0] >= 3 for k in ks)
    downgrade_cycle = next(i for i, k in ks if k == "record:downgrade")
    assert not any(i == downgrade_cycle and k.startswith("notify") for i, k in ks)


def test_clear_requires_all_rungs_and_notifies_recovery_once():
    """[IN-04] incident clears only when every rung is done, with exactly
    one recovery notification carrying duration."""
    seq = [{"warn": T, "crit": T}] * 2 + [{"warn": F, "crit": F}] * 3
    st, fx = run(LADDER, seq)
    assert st.core.state == "cleared"
    recoveries = [(i, e) for i, e in fx
                  if isinstance(e, NotifyEffect) and e.notification.kind == "recover"]
    assert len(recoveries) == 1
    assert "recovered after" in recoveries[0][1].notification.body


def test_notify_recovery_false_suppresses():
    """[IN-04] rungs may opt out of recovery notifications."""
    quiet = GroupConfig(
        monitor="m", entity_id="e", group="g",
        rungs=(RungConfig("r", 2, 1, 1, notify_recovery=False),),
    )
    st, fx = run(quiet, [{"r": T}, {"r": F}])
    assert st.core.state == "cleared"
    assert not any(k[1] == "notify:recover" for k in kinds(fx))


# --- renotification backoff (IN-02) ---


def test_renotify_follows_backoff_ladder():
    """[IN-02] 5m -> 15m -> 1h -> 6h; the 6h tier repeats."""
    st, fx = run(SINGLE, [{"hog": T}] * 3)  # opens at t=1120 (3rd cycle @60s)
    notify_times = []
    now = 1000.0 + 3 * 60.0
    # 900 cycles x 60s = 54000s: enough to cross 300+900+3600+21600+21600.
    for _ in range(900):
        st, effects = step_group(SINGLE, st, {"hog": RungEval(T, "still")}, now)
        for e in effects:
            if isinstance(e, NotifyEffect):
                notify_times.append(now)
        now += 60.0
    gaps = [b - a for a, b in zip(notify_times, notify_times[1:], strict=False)][:5]
    # First renotify ~300s after open, then 900, 3600, 21600, 21600...
    assert gaps[0] == BACKOFF_S[1]  # 900: tier advanced past 300 at first renotify
    # (the 300s tier elapsed between open and first renotify)
    assert notify_times[0] - (1000.0 + 2 * 60.0) >= BACKOFF_S[0]
    assert gaps[1] == BACKOFF_S[2] and gaps[2] == BACKOFF_S[3] and gaps[3] == BACKOFF_S[3]


def test_acked_suppresses_renotify_but_still_clears():
    """[IN-02] ack means quiet, not resolved."""
    from dataclasses import replace

    st, _ = run(SINGLE, [{"hog": T}] * 3)
    st = type(st)(rungs=st.rungs, core=replace(st.core, state="acked"))
    now = 1000.0 + 3 * 60.0
    renotifies = []
    for _ in range(50):
        st, effects = step_group(SINGLE, st, {"hog": RungEval(T, "x")}, now)
        renotifies.extend(e for e in effects if isinstance(e, NotifyEffect))
        now += 600.0
    assert renotifies == []
    st, effects = run(SINGLE, [{"hog": F}] * 3, state=st, start=now)[0], None
    assert st.core.state == "cleared"


def test_escalation_clears_ack():
    """[IN-03] documented decision: a worse situation is new information -
    escalation notifies even on an acked incident and re-opens it."""
    from dataclasses import replace

    st, _ = run(LADDER, [{"warn": T, "crit": F}] * 3)
    st = type(st)(rungs=st.rungs, core=replace(st.core, state="acked"))
    st, effects = step_group(
        LADDER, st,
        {"warn": RungEval(T, "w"), "crit": RungEval(T, "c")}, 2000.0,
    )
    st, effects = step_group(
        LADDER, st,
        {"warn": RungEval(T, "w"), "crit": RungEval(T, "c")}, 2060.0,
    )
    assert st.core.state == "open" and st.core.severity == 3
    assert any(isinstance(e, NotifyEffect) and e.notification.kind == "escalate"
               for e in effects)


# --- flap guard (IN-05) ---


def test_flap_guard_engages_on_third_quick_reopen():
    """[IN-05] three clears each followed by a quick re-open -> flapping,
    starting at the slowest backoff tier with a marked notification."""
    fast = GroupConfig(monitor="m", entity_id="e", group="g",
                       rungs=(RungConfig("r", 2, 1, 1),))
    st = empty_state(fast)
    now = 1000.0
    opens = []
    for _cycle in range(4):
        st, fx = step_group(fast, st, {"r": RungEval(T, "up")}, now)
        opens.extend(e for e in fx if isinstance(e, NotifyEffect)
                     and e.notification.kind == "open")
        now += 30.0
        st, _fx = step_group(fast, st, {"r": RungEval(F, "")}, now)
        now += 30.0
    assert len(opens) == 4
    assert "(flapping)" in opens[-1].notification.body
    assert st.core.state == "cleared"  # last cycle cleared again
    # and the flapping open started at the top backoff tier
    st2, fx2 = step_group(fast, st, {"r": RungEval(T, "up")}, now)
    assert st2.core.backoff_tier == len(BACKOFF_S) - 1


# --- entity gone (IN-07 / CA-08) ---


def test_entity_gone_clears_with_reason_and_wording():
    """[IN-07][CA-08] gone entity: incident clears, recovery message says
    the entity went away, counters reset for id reuse."""
    st, _ = run(SINGLE, [{"hog": T}] * 3)
    st, effects = clear_for_entity_gone(SINGLE, st, 5000.0)
    assert st.core.state == "cleared"
    rec = [e for e in effects if isinstance(e, RecordEffect)][0]
    assert rec.detail["reason"] == "entity_gone"
    note = [e for e in effects if isinstance(e, NotifyEffect)][0]
    assert "went away" in note.notification.body
    assert all(r.confirm_count == 0 and not r.confirmed for r in st.rungs.values())


def test_gone_on_clean_state_is_noop():
    """[IN-07] no incident, nothing to do."""
    st = empty_state(SINGLE)
    st2, effects = clear_for_entity_gone(SINGLE, st, 1.0)
    assert effects == () and st2 == st


# --- purity (IN-06) ---


def test_step_group_is_pure():
    """[IN-06] same inputs -> same outputs; inputs unmutated (frozen)."""
    st, _ = run(SINGLE, [{"hog": T}] * 2)
    evals = {"hog": RungEval(T, "m")}
    a = step_group(SINGLE, st, evals, 9999.0)
    b = step_group(SINGLE, st, evals, 9999.0)
    assert a == b

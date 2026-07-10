"""Pure incident state machine (IN-01..IN-07, SPEC section 9.1).

`step_group` is a pure function (state, evaluations, now, config) ->
(state', effects) — a hard requirement (IN-06) so every transition in the
SPEC diagram is exhaustively table-testable without a database, a clock, or
a notifier. All I/O lives in the effect executor.

Model recap (IN-03, ladder groups): one incident per (monitor, entity,
group). Each rung (rule) keeps independent confirm/clear counters; incident
severity is the highest *confirmed* rung; the owning rung supplies message
and action. Escalation notifies and resets backoff; downgrades are silent;
the incident clears only when no rung remains confirmed.

Decisions this module fixes that the SPEC leaves open:
- Escalation of an *acked* incident clears the ack: a materially worse
  situation is new information the user has not seen (recorded in history).
- Rung order inside GroupConfig must be severity-descending; ownership ties
  break toward the earlier rung (stable and definition-file-ordered).

Episodes (IN-08) arrive with the event pipeline in M3 and share IncidentCore.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ftmon.model import (
    ActionEffect,
    Effect,
    GroupState,
    IncidentCore,
    Notification,
    NotifyEffect,
    RecordEffect,
    RungState,
    TriBool,
    severity_name,
)

BACKOFF_S = (300.0, 900.0, 3600.0, 21600.0)  # IN-02: 5m -> 15m -> 1h -> 6h repeating
FLAP_WINDOW_S = 600.0  # IN-05: re-open within 10m of clearing...
FLAP_COUNT = 3  # ...this many times -> flapping
_BODY_MAX = 200  # NO-01


@dataclass(frozen=True)
class RungConfig:
    rule_id: str
    severity: int  # 1..4 (notice..critical)
    confirm_cycles: int
    clear_cycles: int
    action: str | None = None
    notify_recovery: bool = True


@dataclass(frozen=True)
class GroupConfig:
    monitor: str
    entity_id: str
    group: str
    rungs: tuple[RungConfig, ...]  # MUST be severity-descending (loader guarantees)


@dataclass(frozen=True)
class RungEval:
    result: TriBool
    message: str = ""  # rendered rule message; only meaningful when TRUE


def empty_state(cfg: GroupConfig) -> GroupState:
    return GroupState(rungs={r.rule_id: RungState() for r in cfg.rungs}, core=None)


def step_group(
    cfg: GroupConfig,
    st: GroupState,
    evals: dict[str, RungEval],
    now: float,
) -> tuple[GroupState, tuple[Effect, ...]]:
    rungs = _step_rungs(cfg, st, evals)
    confirmed = [r for r in cfg.rungs if rungs[r.rule_id].confirmed]
    effects: list[Effect] = []
    core = st.core

    if core is None or core.state == "cleared":
        if not confirmed:
            return GroupState(rungs=rungs, core=core), ()
        core, effects = _open(cfg, core, confirmed, evals, now)
    elif not confirmed:
        core, effects = _clear(cfg, core, now, reason="recovered")
    else:
        core, effects = _update_open(cfg, core, confirmed, evals, now)

    return GroupState(rungs=rungs, core=core), tuple(effects)


def clear_for_entity_gone(
    cfg: GroupConfig, st: GroupState, now: float
) -> tuple[GroupState, tuple[Effect, ...]]:
    """CA-08/IN-07: the entity vanished — a leaking process that exits is a
    resolved leak. Counters reset too: a reused id must start clean."""
    if st.core is None or st.core.state == "cleared":
        return st, ()
    core, effects = _clear(cfg, st.core, now, reason="entity_gone")
    return GroupState(rungs={r.rule_id: RungState() for r in cfg.rungs}, core=core), tuple(effects)


def clear_superseded(
    cfg: GroupConfig, st: GroupState, now: float
) -> tuple[GroupState, tuple[Effect, ...]]:
    """MD-06/MD-09: the definition changed or vanished. Silent (no recovery
    notification): the world didn't get better, the rules did."""
    if st.core is None or st.core.state == "cleared":
        return st, ()
    core, effects = _clear(cfg, st.core, now, reason="superseded")
    return GroupState(rungs={r.rule_id: RungState() for r in cfg.rungs}, core=core), tuple(effects)


# -- internals -------------------------------------------------------------


def _step_rungs(
    cfg: GroupConfig, st: GroupState, evals: dict[str, RungEval]
) -> dict[str, RungState]:
    """IN-01: TRUE advances confirmation, FALSE resets it and advances
    clearing, UNKNOWN freezes both — missing data is not evidence."""
    out: dict[str, RungState] = {}
    for rung in cfg.rungs:
        rs = st.rungs.get(rung.rule_id, RungState())
        ev = evals.get(rung.rule_id)
        result = ev.result if ev is not None else TriBool.UNKNOWN
        if result is TriBool.TRUE:
            confirm = min(rs.confirm_count + 1, rung.confirm_cycles)
            out[rung.rule_id] = RungState(
                confirmed=rs.confirmed or confirm >= rung.confirm_cycles,
                confirm_count=confirm,
                clear_count=0,
            )
        elif result is TriBool.FALSE:
            clear = min(rs.clear_count + 1, rung.clear_cycles) if rs.confirmed else 0
            out[rung.rule_id] = RungState(
                confirmed=rs.confirmed and clear < rung.clear_cycles,
                confirm_count=0,
                clear_count=clear,
            )
        else:  # UNKNOWN
            out[rung.rule_id] = rs
    return out


def _owning(confirmed: list[RungConfig]) -> RungConfig:
    # cfg.rungs is severity-descending, so the first confirmed rung is the
    # highest-severity one; ties resolve to definition order.
    return confirmed[0]


def _message(evals: dict[str, RungEval], rung: RungConfig, fallback: str) -> str:
    ev = evals.get(rung.rule_id)
    if ev is not None and ev.message:
        return ev.message[:_BODY_MAX]
    return fallback[:_BODY_MAX]


def _notify(core: IncidentCore, cfg: GroupConfig, kind: str, body: str, now: float) -> Effect:
    title = f"{severity_name(core.severity)}: {cfg.monitor} — {cfg.entity_id}"
    return NotifyEffect(
        Notification(
            incident_id=core.incident_id or 0,  # executor fills real id on insert
            kind=kind,  # type: ignore[arg-type]
            severity=core.severity,
            title=title,
            body=body[:_BODY_MAX],
            created_ts=now,
        )
    )


def _open(
    cfg: GroupConfig,
    prior: IncidentCore | None,
    confirmed: list[RungConfig],
    evals: dict[str, RungEval],
    now: float,
) -> tuple[IncidentCore, list[Effect]]:
    owner = _owning(confirmed)
    # IN-05: quick re-opens after recent clears mark the incident flapping
    # and start it at the slowest backoff tier immediately.
    flap_clears = tuple(
        t for t in (prior.flap_clears if prior else ()) if now - t <= FLAP_WINDOW_S
    )
    flapping = len(flap_clears) >= FLAP_COUNT
    core = IncidentCore(
        incident_id=None,  # executor assigns
        state="open",
        severity=owner.severity,
        owning_rule=owner.rule_id,
        opened_ts=now,
        last_notify_ts=now,
        notify_count=1,
        backoff_tier=len(BACKOFF_S) - 1 if flapping else 0,
        flap_clears=flap_clears,
        occurrences=1,
    )
    body = _message(evals, owner, f"{cfg.group} triggered")
    if flapping:
        body = f"(flapping) {body}"
    effects: list[Effect] = [
        RecordEffect("open", {"rule": owner.rule_id, "severity": owner.severity,
                              "flapping": flapping}),
        _notify(core, cfg, "open", body, now),
    ]
    if owner.action:
        effects.append(ActionEffect(owner.action, {}))  # AC-02: on open only
    return core, effects


def _update_open(
    cfg: GroupConfig,
    core: IncidentCore,
    confirmed: list[RungConfig],
    evals: dict[str, RungEval],
    now: float,
) -> tuple[IncidentCore, list[Effect]]:
    owner = _owning(confirmed)
    effects: list[Effect] = []

    if owner.severity > core.severity:
        # IN-03 escalation: notify, reset backoff; ack does not survive a
        # worse situation (module docstring).
        core = replace(
            core,
            state="open",
            severity=owner.severity,
            owning_rule=owner.rule_id,
            last_notify_ts=now,
            notify_count=core.notify_count + 1,
            backoff_tier=0,
        )
        effects.append(RecordEffect("escalate", {"rule": owner.rule_id,
                                                 "severity": owner.severity}))
        effects.append(_notify(core, cfg, "escalate", _message(evals, owner, "escalated"), now))
        return core, effects

    if owner.severity < core.severity:
        # IN-03 downgrade: silent, in place; history records it.
        core = replace(core, severity=owner.severity, owning_rule=owner.rule_id)
        return core, [RecordEffect("downgrade", {"rule": owner.rule_id,
                                                 "severity": owner.severity})]

    # Same severity: IN-02 renotify on backoff, unless acked. The owner must
    # also be TRUE *right now* — while clear_cycles are accumulating the
    # incident is technically open, but "still firing" would be a lie.
    owner_eval = evals.get(owner.rule_id)
    owner_true = owner_eval is not None and owner_eval.result is TriBool.TRUE
    if core.state == "open" and core.last_notify_ts is not None and owner_true:
        wait = BACKOFF_S[min(core.backoff_tier, len(BACKOFF_S) - 1)]
        elapsed = max(0.0, now - core.last_notify_ts)  # SA-07: never negative
        if elapsed >= wait:
            core = replace(
                core,
                last_notify_ts=now,
                notify_count=core.notify_count + 1,
                backoff_tier=min(core.backoff_tier + 1, len(BACKOFF_S) - 1),
            )
            effects.append(_notify(core, cfg, "renotify", _message(evals, owner, "still firing"),
                                   now))
    return core, effects


def _clear(
    cfg: GroupConfig, core: IncidentCore, now: float, reason: str
) -> tuple[IncidentCore, list[Effect]]:
    duration = max(0.0, now - core.opened_ts)
    cleared = replace(
        core,
        state="cleared",
        flap_clears=(*core.flap_clears, now)[-5:],  # bounded history for IN-05
    )
    effects: list[Effect] = [RecordEffect("clear", {"reason": reason,
                                                    "duration_s": duration})]
    # IN-04: exactly one recovery notification; wording differs for gone
    # entities (CA-08); superseded clears are silent (rules changed, not
    # the world). notify_recovery=False rungs suppress it.
    owner = next((r for r in cfg.rungs if r.rule_id == core.owning_rule), None)
    if reason != "superseded" and (owner is None or owner.notify_recovery):
        if reason == "entity_gone":
            body = f"{cfg.entity_id} went away; incident closed ({duration / 60:.0f}m)"
        else:
            body = f"recovered after {duration / 60:.0f}m (peak {severity_name(core.severity)})"
        note = Notification(
            incident_id=core.incident_id or 0,
            kind="recover",
            severity=0,
            title=f"recovered: {cfg.monitor} — {cfg.entity_id}",
            body=body[:_BODY_MAX],
            created_ts=now,
        )
        effects.append(NotifyEffect(note))
    return cleared, effects

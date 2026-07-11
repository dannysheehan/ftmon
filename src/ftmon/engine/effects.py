"""Effect executor: turns the pure engine's effects into store writes
(IN-06's other half — all I/O the state machine refused to do).

Everything here lands in the tick's single transaction via TickWriter;
notification *delivery* is not here at all — effects only enqueue outbox
rows (NO-04), and the daemon flushes the outbox after commit.

Actions are queued with their committed incident id and drained by the daemon
after commit. Running a subprocess inside the tick transaction would violate
PM-03 and let a 30-second timeout block every reader and writer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ftmon.engine.incidents import GroupConfig
from ftmon.model import ActionEffect, Effect, GroupState, NotifyEffect, RecordEffect, severity_name


@dataclass(frozen=True)
class PendingAction:
    """Post-commit action request carrying only the AC-02 allowlisted context."""

    incident_id: int
    action: str
    env: dict[str, str]


class EffectExecutor:
    def __init__(self, writer):  # store.writer.TickWriter (untyped: layering)
        self._writer = writer
        self._pending_actions: list[PendingAction] = []

    def drain_actions(self) -> tuple[PendingAction, ...]:
        """Return and clear action requests after their incident commit."""
        pending = tuple(self._pending_actions)
        self._pending_actions.clear()
        return pending

    def apply(
        self, cfg: GroupConfig, state: GroupState, effects: tuple[Effect, ...], now: float
    ) -> GroupState:
        """Persist the transition. Returns state with a real incident_id
        assigned (the pure engine leaves it None on open)."""
        core = state.core
        if core is None:
            return state
        if core.incident_id is None:
            core = replace(core, incident_id=self._writer.alloc_incident_id())
            state = GroupState(rungs=state.rungs, core=core)

        for effect in effects:
            if isinstance(effect, RecordEffect):
                self._writer.add_incident_history(core.incident_id, now, effect.kind,
                                                  effect.detail)
            elif isinstance(effect, NotifyEffect):
                n = effect.notification
                self._writer.add_outbox(
                    core.incident_id,
                    n.kind,
                    {"severity": n.severity, "title": n.title, "body": n.body},
                    now,
                )
                self._writer.add_incident_history(
                    core.incident_id, now, "notified", {"kind": n.kind}
                )
            elif isinstance(effect, ActionEffect):
                env = {
                    "FTMON_MONITOR": cfg.monitor,
                    "FTMON_RULE": core.owning_rule,
                    "FTMON_ENTITY": cfg.entity_id,
                    "FTMON_SEVERITY": severity_name(core.severity),
                    "FTMON_MESSAGE": effect.env.get("FTMON_MESSAGE", ""),
                    "FTMON_INCIDENT_ID": str(core.incident_id),
                    "FTMON_VALUE": effect.env.get("FTMON_VALUE", "true"),
                }
                self._pending_actions.append(PendingAction(
                    core.incident_id, effect.action, env
                ))

        self._writer.upsert_incident(
            core.incident_id,
            cfg.monitor,
            cfg.group,
            cfg.entity_id,
            state=core.state,
            severity=core.severity,
            owning_rule=core.owning_rule,
            opened_ts=core.opened_ts,
            last_change_ts=now,
            cleared_ts=now if core.state == "cleared" else None,
            clear_reason=_clear_reason(effects) if core.state == "cleared" else None,
            ack_by=None,
            ack_ts=None,
            notify_count=core.notify_count,
            occurrences=core.occurrences,
            flapping=len(core.flap_clears) >= 3,
        )
        return state


def _clear_reason(effects: tuple[Effect, ...]) -> str:
    for e in effects:
        if isinstance(e, RecordEffect) and e.kind == "clear":
            return str(e.detail.get("reason", "recovered"))
    return "recovered"

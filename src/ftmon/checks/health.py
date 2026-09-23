"""Runtime collection-health ownership without author naming conventions (EC-11)."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ftmon.definitions.loader import MonitorDef, RuleDef
from ftmon.expr import CONSTANTS, NameEnv, compile_expr

_STATUS_METRICS = frozenset({"plugin_state", "plugin_ok"})
_FALLBACK = RuleDef(
    id="@check-health", group="@check-health",
    when=compile_expr("plugin_state == 3", NameEnv(metrics=_STATUS_METRICS)),
    severity=2, confirm_cycles=2, clear_cycles=2,
    message="Collection unavailable: {plugin_message}",
    action=None, notify_recovery=True, cooldown_s=None, clear_after_s=None,
    confirm_count=1, confirm_window_s=None,
)


@dataclass(frozen=True)
class _StatusContext:
    state: int
    parameters: dict[str, float]

    def metric_last(self, name: str) -> float | None:
        if name not in _STATUS_METRICS:
            return None
        return float(self.state if name == "plugin_state" else self.state == 0)

    def param(self, name: str) -> float:
        return self.parameters[name]


def collection_health_rules(mdef: MonitorDef) -> tuple[RuleDef, ...]:
    """Prove coverage of UNKNOWN using only static status evidence.

    Other measurements are UNKNOWN during this proof. Kleene logic makes a
    TRUE result independent of their values, so an OR with a threshold can
    still prove coverage; an AND requiring that threshold cannot. Calls and
    attributes are excluded because their availability is not static.
    The reserved internal identifier cannot collide with validated user IDs.
    """
    if mdef.source != "external":
        return ()
    owners = []
    for rule in mdef.rules:
        if not set(rule.when.metric_names) & _STATUS_METRICS:
            continue
        allowed = set(rule.when.metric_names) | mdef.parameters.keys() | CONSTANTS.keys()
        tree = ast.parse(rule.when.source, mode="eval")
        if any(isinstance(node, ast.Call) for node in ast.walk(tree)):
            continue
        if any(isinstance(node, ast.Name) and node.id not in allowed for node in ast.walk(tree)):
            continue
        if (rule.when.eval(_StatusContext(3, mdef.parameters)) is True
                and rule.when.eval(_StatusContext(0, mdef.parameters)) is not True):
            owners.append(rule)
    return tuple(owners) or (_FALLBACK,)


def runtime_rules(mdef: MonitorDef) -> tuple[RuleDef, ...]:
    """Add the fallback only when no declared condition owns collection health."""
    owners = collection_health_rules(mdef)
    return mdef.rules + ((_FALLBACK,) if owners == (_FALLBACK,) else ())

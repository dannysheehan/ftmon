"""Core frozen types (DESIGN.md section 4). FROZEN: implementers must not alter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from ftmon.expr.tribool import TriBool  # re-export for engine use

__all__ = [
    "TriBool",
    "MetricDecl",
    "AttrDecl",
    "SourceDecl",
    "EntitySample",
    "Snapshot",
    "EventRecord",
    "Notification",
    "RungState",
    "IncidentCore",
    "GroupState",
    "NotifyEffect",
    "ActionEffect",
    "RecordEffect",
    "Effect",
    "SEVERITIES",
    "severity_name",
]

# DM-08 severity scale
SEVERITIES = ("info", "notice", "warning", "error", "critical")


def severity_name(level: int) -> str:
    return SEVERITIES[level] if 0 <= level < len(SEVERITIES) else f"sev{level}"


@dataclass(frozen=True)
class MetricDecl:
    name: str
    unit: str
    kind: Literal["gauge", "counter"]
    doc: str


@dataclass(frozen=True)
class AttrDecl:
    name: str
    doc: str


@dataclass(frozen=True)
class SourceDecl:  # PL-05
    name: str
    kind: Literal["sampler", "events"]
    entity_kind: str
    metrics: tuple[MetricDecl, ...]
    attrs: tuple[AttrDecl, ...]

    def metric_names(self) -> frozenset[str]:
        return frozenset(m.name for m in self.metrics)

    def attr_names(self) -> frozenset[str]:
        return frozenset(a.name for a in self.attrs)


@dataclass(frozen=True)
class EntitySample:
    entity_id: str
    attrs: Mapping[str, str]
    metrics: Mapping[str, float]


@dataclass(frozen=True)
class Snapshot:  # SA-06: one ts for all entities of one source run
    source: str
    ts: float
    entities: tuple[EntitySample, ...]


@dataclass(frozen=True)
class EventRecord:  # DM-07/DM-08
    ts: float
    ingest_ts: float
    source: str
    provider: str
    event_id: str | None
    severity: int
    message: str
    attrs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Notification:  # NO-01
    incident_id: int
    kind: Literal["open", "escalate", "renotify", "recover", "digest"]
    severity: int
    title: str
    body: str
    created_ts: float


# --- incident engine I/O (IN-06, DESIGN 10.4) ---


@dataclass(frozen=True)
class RungState:
    confirmed: bool = False
    confirm_count: int = 0
    clear_count: int = 0


@dataclass(frozen=True)
class IncidentCore:
    incident_id: int | None
    state: Literal["open", "acked", "cleared"]
    severity: int
    owning_rule: str
    opened_ts: float
    last_notify_ts: float | None
    notify_count: int
    backoff_tier: int
    flap_clears: tuple[float, ...]
    occurrences: int
    # Highest severity ever held (IN-03): silent downgrades lower `severity`
    # in place, so the recovery message's "peak" needs its own memory.
    # Default 0 = "no better information than current severity" (restarts
    # rebuild from a DB that only stores the current value).
    peak_severity: int = 0


@dataclass(frozen=True)
class GroupState:
    rungs: Mapping[str, RungState]
    core: IncidentCore | None


@dataclass(frozen=True)
class NotifyEffect:
    notification: Notification


@dataclass(frozen=True)
class ActionEffect:
    action: str
    env: Mapping[str, str]


@dataclass(frozen=True)
class RecordEffect:
    kind: str
    detail: Mapping[str, object]


Effect = NotifyEffect | ActionEffect | RecordEffect

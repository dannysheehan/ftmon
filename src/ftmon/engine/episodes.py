"""Pure episode state machine (IN-08, SPEC section 7.7.3).

Episodes are the event-rule flavor of incidents. Same discipline as
incidents.py: `step_episode` is a pure function so every lifecycle branch is
table-testable; all I/O goes through the same EffectExecutor (episodes emit
GroupState with an empty rungs map, which the executor persists exactly like
a ladder incident — shared ack/history/outbox for free, per IN-08).

IN-08 fixes exactly three differences from ladder incidents:
- a matching event opens (after `confirm_count` events within
  `confirm_window` when configured; default 1 = immediate) and every further
  match refreshes `last_seen` and increments `occurrences`;
- renotification is by `cooldown` since the last notification, not the
  IN-02 backoff ladder — and the renotify body carries the occurrence count,
  which is the whole point ("OOM killer fired (12x)" instead of 12 popups);
- clearing is a quiet period (`clear_after` with no matching event),
  clear_reason = quiet_period, silent by default (notify_recovery=False for
  event rules): "the log went quiet" is not a recovery worth a popup.

Decisions this module fixes that the SPEC leaves open:
- Flap guard (IN-05) maps to cooldown: a flapping episode (3 clears within
  10 m before this open) opens with a "(flapping)" prefix and its cooldown
  quadrupled — episodes have no backoff tier to max out, so stretching the
  cooldown is the equivalent noise brake.
- An acked episode accumulates occurrences silently and still quiet-clears;
  matching events do not un-ack it (only escalation clears acks, and
  episodes have a single severity — no escalation path).

Episode identity — (rule, provider, event_id|msg_hash) — is the *caller's*
job (engine/events.py builds the key with msg_hash below); this module sees
one already-keyed episode at a time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ftmon.model import (
    Effect,
    GroupState,
    IncidentCore,
    Notification,
    NotifyEffect,
    RecordEffect,
    severity_name,
)

FLAP_WINDOW_S = 600.0  # IN-05, same constants as incidents.py
FLAP_COUNT = 3
_FLAP_COOLDOWN_FACTOR = 4.0
_BODY_MAX = 200  # NO-01

_HEX_RUN = re.compile(r"[0-9a-f]{8,}")
_DIGIT_RUN = re.compile(r"[0-9]+")
_WS_RUN = re.compile(r"\s+")


def msg_hash(message: str) -> str:
    """SPEC 7.7.3 normative normalization: lowercase, collapse whitespace,
    replace hex runs (>=8 chars) then digit runs with '#', SHA-256, first 16
    hex chars. Hex before digits, or '0x1a2b3c4d' would decay into mixed
    fragments instead of one '#'. Collisions only group unrelated events
    into one episode, which is harmless by design."""
    text = _WS_RUN.sub(" ", message.lower()).strip()
    text = _HEX_RUN.sub("#", text)
    text = _DIGIT_RUN.sub("#", text)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass(frozen=True)
class EpisodeConfig:
    monitor: str
    rule_id: str
    entity_key: str  # "provider:event_id-or-msg_hash" — identity, display, DB entity_id
    severity: int
    cooldown_s: float
    clear_after_s: float
    confirm_count: int = 1
    confirm_window_s: float | None = None
    notify_recovery: bool = False


@dataclass(frozen=True)
class EpisodeState:
    core: IncidentCore | None = None
    last_seen_ts: float | None = None  # last matching event (quiet-period anchor)
    pending_ts: tuple[float, ...] = ()  # pre-open matches inside confirm_window
    flap_clears: tuple[float, ...] = ()  # kept outside core: survives core=None
    cooldown_s: float | None = None  # effective cooldown (stretched when flapping)


def step_episode(
    cfg: EpisodeConfig,
    st: EpisodeState,
    matches: tuple[tuple[float, str], ...],  # (event ingest ts, rendered message)
    now: float,
) -> tuple[EpisodeState, tuple[Effect, ...]]:
    """One tick for one episode. `matches` are this tick's matching events
    (possibly empty — quiet-period clearing needs the tick even without
    events). Ordering is ingest order (DM-15): late source timestamps are
    deliberately not honored here."""
    core = st.core
    if core is not None and core.state != "cleared":
        if matches:
            return _refresh(cfg, st, matches, now)
        if st.last_seen_ts is not None and now - st.last_seen_ts >= cfg.clear_after_s:
            return _clear(cfg, st, now)
        return st, ()
    if matches:
        return _maybe_open(cfg, st, matches, now)
    if st.pending_ts and cfg.confirm_window_s is not None:
        # expire stale pre-open matches so an old lone event can't combine
        # with one far in the future to fake a burst
        keep = tuple(t for t in st.pending_ts if now - t <= cfg.confirm_window_s)
        if keep != st.pending_ts:
            return replace(st, pending_ts=keep), ()
    return st, ()


def _maybe_open(
    cfg: EpisodeConfig,
    st: EpisodeState,
    matches: tuple[tuple[float, str], ...],
    now: float,
) -> tuple[EpisodeState, tuple[Effect, ...]]:
    pending = (*st.pending_ts, *(t for t, _ in matches))
    if cfg.confirm_window_s is not None:
        pending = tuple(t for t in pending if now - t <= cfg.confirm_window_s)
    if len(pending) < cfg.confirm_count:
        return replace(st, pending_ts=pending), ()

    flap_clears = tuple(t for t in st.flap_clears if now - t <= FLAP_WINDOW_S)
    flapping = len(flap_clears) >= FLAP_COUNT
    core = IncidentCore(
        incident_id=None,  # executor assigns
        state="open",
        severity=cfg.severity,
        owning_rule=cfg.rule_id,
        opened_ts=now,
        last_notify_ts=now,
        notify_count=1,
        backoff_tier=0,  # unused by episodes; cooldown governs (IN-08)
        flap_clears=flap_clears,
        occurrences=len(pending),
    )
    body = matches[-1][1]  # newest event's rendered message speaks for the episode
    if flapping:
        body = f"(flapping) {body}"
    st2 = EpisodeState(
        core=core,
        last_seen_ts=now,
        pending_ts=(),
        flap_clears=flap_clears,
        cooldown_s=cfg.cooldown_s * (_FLAP_COOLDOWN_FACTOR if flapping else 1.0),
    )
    effects: tuple[Effect, ...] = (
        RecordEffect("open", {"rule": cfg.rule_id, "severity": cfg.severity,
                              "flapping": flapping, "occurrences": len(pending)}),
        _notify(cfg, core, "open", body, now),
    )
    return st2, effects


def _refresh(
    cfg: EpisodeConfig,
    st: EpisodeState,
    matches: tuple[tuple[float, str], ...],
    now: float,
) -> tuple[EpisodeState, tuple[Effect, ...]]:
    core = st.core
    assert core is not None
    core = replace(core, occurrences=core.occurrences + len(matches))
    effects: list[Effect] = []
    cooldown = st.cooldown_s if st.cooldown_s is not None else cfg.cooldown_s
    if (
        core.state == "open"  # acked episodes stay silent
        and core.last_notify_ts is not None
        and now - core.last_notify_ts >= cooldown
    ):
        core = replace(core, last_notify_ts=now, notify_count=core.notify_count + 1)
        body = f"{matches[-1][1]} ({core.occurrences}x since open)"
        effects.append(_notify(cfg, core, "renotify", body, now))
    # occurrences changed even without a notification; a record keeps the
    # DB row honest for `ftmon incident <id>` without spamming history
    effects.append(RecordEffect("refresh", {"count": len(matches),
                                            "occurrences": core.occurrences}))
    return replace(st, core=core, last_seen_ts=now), tuple(effects)


def _clear(
    cfg: EpisodeConfig, st: EpisodeState, now: float
) -> tuple[EpisodeState, tuple[Effect, ...]]:
    core = st.core
    assert core is not None
    duration = max(0.0, now - core.opened_ts)
    cleared = replace(core, state="cleared")
    st2 = EpisodeState(
        core=cleared,
        last_seen_ts=None,
        pending_ts=(),
        flap_clears=(*st.flap_clears, now)[-5:],  # bounded, as in incidents.py
        cooldown_s=None,
    )
    effects: list[Effect] = [
        RecordEffect("clear", {"reason": "quiet_period", "duration_s": duration,
                               "occurrences": core.occurrences})
    ]
    if cfg.notify_recovery:  # default False for event rules (7.7.3)
        effects.append(NotifyEffect(Notification(
            incident_id=core.incident_id or 0,
            kind="recover",
            severity=0,
            title=f"quiet: {cfg.monitor} — {cfg.entity_key}",
            body=(f"no matching events for {cfg.clear_after_s / 60:.0f}m "
                  f"({core.occurrences}x total)")[:_BODY_MAX],
            created_ts=now,
        )))
    return st2, tuple(effects)


def _notify(
    cfg: EpisodeConfig, core: IncidentCore, kind: str, body: str, now: float
) -> NotifyEffect:
    return NotifyEffect(Notification(
        incident_id=core.incident_id or 0,
        kind=kind,  # type: ignore[arg-type]
        severity=cfg.severity,
        title=f"{severity_name(cfg.severity)}: {cfg.monitor} — {cfg.entity_key}",
        body=body[:_BODY_MAX],
        created_ts=now,
    ))


def as_group_state(st: EpisodeState) -> GroupState:
    """Adapter for EffectExecutor.apply, which persists GroupState.core and
    ignores rungs — episodes have none."""
    return GroupState(rungs={}, core=st.core)

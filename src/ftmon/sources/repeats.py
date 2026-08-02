"""Bounded adjacent-event coalescing shared by platform adapters (DM-20).

Only contiguous equal records are merged.  That deliberately narrow rule
keeps opaque journal cursors and bookmarks in ingest order: coalescing an
older run across an intervening event could checkpoint past that event before
it was drained.
"""

from __future__ import annotations


def merge_adjacent(previous: dict, current: dict) -> bool:
    """Merge ``current`` into an equal preceding event and return True.

    Equality includes origin and normalized severity, not just message text,
    so unrelated producers cannot conceal one another.  Repeat metadata uses
    string values because EventRecord attrs are a string mapping.
    """
    fields = ("source", "provider", "event_id", "severity", "message")
    if any(previous.get(name) != current.get(name) for name in fields):
        return False

    attrs = dict(previous.get("attrs", {}))
    count = _positive_int(attrs.get("repeat_count"), 1) + _repeat_count(current)
    first_ts = attrs.get("repeat_first_ts", _timestamp(previous.get("ts")))
    current_attrs = current.get("attrs", {})
    last_ts = current_attrs.get("repeat_last_ts", _timestamp(current.get("ts")))
    attrs.update(
        repeat_count=str(count),
        repeat_first_ts=str(first_ts),
        repeat_last_ts=str(last_ts),
    )
    previous["attrs"] = attrs
    return True


def occurrence_count(fields: dict) -> int:
    """Return represented raw occurrences for source and engine accounting."""
    return _repeat_count(fields)


def _repeat_count(fields: dict) -> int:
    attrs = fields.get("attrs", {})
    return _positive_int(attrs.get("repeat_count"), 1)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _timestamp(value: object) -> str:
    try:
        return format(float(value), ".6f")
    except (TypeError, ValueError):
        return "0.000000"

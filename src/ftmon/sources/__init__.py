"""EventSource platform dispatch (PL-01): the seam daemon.py plugs into
instead of importing a concrete implementation directly.
"""

from __future__ import annotations

from ftmon.sources.base import EventSource


def event_source_for_platform(platform: str | None = None) -> EventSource | None:
    """One EventSource implementation per platform. None where no adapter is
    registered yet — daemon.py already treats a missing event source as
    "events disabled" (DaemonCore.event_source is EventSource | None)."""
    from ftmon.paths import current_platform

    current = platform or current_platform()
    if current == "linux":
        from ftmon.sources.journald import JournaldEventSource

        return JournaldEventSource()
    return None

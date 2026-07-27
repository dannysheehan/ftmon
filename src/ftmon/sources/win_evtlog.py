"""Windows Event Log EventSource (SA-03, DM-07/08/15, SA-08, PL-01/02).

win32evtlog.EvtSubscribe is the seam validated by the feature/windows-support
spike (spikes/windows-support/evtlog_spike.py, NOTES.md SS1): one EvtSubscribe
per channel, each with its own EvtBookmark cursor, all feeding one shared
bounded queue -- the same shape as JournaldEventSource's single subprocess
reader thread, just N of them. A structured multi-channel query (one
EvtSubscribe call covering several channels) was never validated, so this
deliberately avoids relying on it.

Parsing is a pure function (parse_event_xml) so the DM-08 Level mapping and
malformed-XML tolerance are unit-testable without a live Windows box (TS-02);
only the EvtSubscribe plumbing needs a real system.

Known, deliberately unsolved gap (see NOTES.md SS1): a per-channel bookmark
that is well-formed XML but references a RecordId no longer in that channel
(log rolled over) does not raise anywhere in the Win32 Event Log API -- it
silently falls through to "future events only" for that channel. There is no
exception to catch and turn into a gap self-event the way an invalid
journald cursor would produce one.

No clock reads (TS-03): ingest_ts comes in through drain(now, ...).
"""

from __future__ import annotations

import collections
import json
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from ftmon.model import EventRecord, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

__all__ = ["ChannelSpec", "WindowsEventSource", "parse_event_xml", "LEVEL_TO_SEVERITY"]


@dataclass(frozen=True)
class ChannelSpec:
    """One EvtSubscribe target: a channel path plus an optional XPath filter
    query (the same query language EvtQuery/wevtutil/Get-WinEvent share --
    not WEC/WEF-specific). `query=None` subscribes to everything on the
    channel, same as today's behavior."""

    path: str
    query: str | None = None

QUEUE_MAX = 10_000  # SA-08, same bound as JournaldEventSource
_MSG_MAX = 2048  # DM-13: event messages truncate at 2 KB
_DEFAULT_CHANNELS = ("System", "Application")

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

# DM-08: Windows Event Log Level -> ftmon severity 0-4. 1 Critical, 2 Error,
# 3 Warning, 4 Information, 5 Verbose -- Windows has no "notice" analogue, so
# unlike journald's PRIORITY table nothing maps to severity 1. An absent or
# unrecognized Level defaults to 0 (info), same fallback style as
# PRIORITY_TO_SEVERITY.get(priority, 0) in journald.py.
LEVEL_TO_SEVERITY = {1: 4, 2: 3, 3: 2, 4: 0, 5: 0}


def parse_event_xml(xml_text: str) -> dict | None:
    """One EvtRenderEventXml document -> EventRecord fields (everything
    except ingest_ts, stamped at drain time same as journald.py's
    parse_line), or None if unparseable (caller counts it; never fatal,
    SA-08)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    system = root.find(f"{_NS}System")
    if system is None:
        return None

    provider_el = system.find(f"{_NS}Provider")
    provider = (provider_el.get("Name") if provider_el is not None else None) or "unknown"

    event_id_el = system.find(f"{_NS}EventID")
    event_id = (
        event_id_el.text.strip() if event_id_el is not None and event_id_el.text else None
    )

    level_el = system.find(f"{_NS}Level")
    try:
        level = int(level_el.text) if level_el is not None and level_el.text else 4
    except ValueError:
        level = 4
    severity = LEVEL_TO_SEVERITY.get(level, 0)

    ts = 0.0
    time_el = system.find(f"{_NS}TimeCreated")
    raw_time = time_el.get("SystemTime") if time_el is not None else None
    if raw_time:
        try:
            ts = datetime.fromisoformat(raw_time).timestamp()
        except ValueError:
            ts = 0.0

    message = _synthesize_message(root, event_id, provider)
    fields = {
        "ts": ts,
        "source": "eventlog",
        "provider": provider,
        "event_id": event_id,
        "severity": severity,
        "message": message[:_MSG_MAX],
    }
    return fields


def _synthesize_message(root: ET.Element, event_id: str | None, provider: str) -> str:
    """No EvtFormatMessage call (a per-provider message-table lookup that can
    be slow or fail for providers without one registered) -- join the raw
    EventData values instead, same trust-the-raw-field philosophy as
    journald.py's parse_line."""
    fallback = f"EventID {event_id} ({provider})" if event_id else f"({provider})"
    event_data = root.find(f"{_NS}EventData")
    if event_data is None:
        return fallback
    parts = []
    for data in event_data.findall(f"{_NS}Data"):
        text = (data.text or "").strip()
        if not text:
            continue
        name = data.get("Name")
        parts.append(f"{name}={text}" if name else text)
    return "; ".join(parts) if parts else fallback


class WindowsEventSource:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["events"]

    def __init__(self, channels: tuple[str, ...] = _DEFAULT_CHANNELS):
        self._channels: tuple[ChannelSpec, ...] = tuple(
            ChannelSpec(path=c) for c in channels
        )
        self._subs: list = []
        self._channel_ok: dict[str, bool] = {}
        # deque(maxlen=N) drops from the head on overflow -- SA-08's "oldest
        # are dropped"; the lock also guards the bookmark dict and counters.
        self._queue: collections.deque[dict] = collections.deque(maxlen=QUEUE_MAX)
        self._lock = threading.Lock()
        self._bookmarks: dict[str, str] = {}
        self.dropped = 0
        self.malformed = 0
        # channel -> str(exception) for the most recent EvtSubscribe failure;
        # EventEngine.tick() surfaces each once as a self-event.
        self.subscribe_errors: dict[str, str] = {}

    def configure(self, channels: tuple[ChannelSpec, ...]) -> None:
        """Pre-start channel/query override (daemon.py's _start_events(),
        called once before the first start() -- see win_evtlog.py's module
        docstring: there is exactly one EvtSubscribe pass per daemon
        lifetime, not a hot-reconfigure path). A no-op once subscriptions
        already exist: changing channels after that needs a restart, same
        as the rest of this class's one-shot-subscribe design."""
        if self._subs:
            return
        self._channels = channels

    def start(self, cursor: str | None) -> None:
        import pywintypes
        import win32evtlog

        self._subs = []
        self._channel_ok = {}
        self.subscribe_errors = {}

        prior: dict[str, str] = {}
        if cursor:
            try:
                decoded = json.loads(cursor)
                if isinstance(decoded, dict):
                    prior = {k: v for k, v in decoded.items() if isinstance(v, str)}
            except json.JSONDecodeError:
                with self._lock:
                    self.malformed += 1  # foreign/corrupt cursor; every channel starts fresh

        for spec in self._channels:
            channel = spec.path
            bookmark = None
            bookmark_xml = prior.get(channel)
            if bookmark_xml:
                try:
                    bookmark = win32evtlog.EvtCreateBookmark(bookmark_xml)
                except pywintypes.error:
                    with self._lock:
                        self.malformed += 1  # stale/foreign bookmark; this channel starts fresh

            flags = (
                win32evtlog.EvtSubscribeStartAfterBookmark
                if bookmark is not None
                else win32evtlog.EvtSubscribeToFutureEvents
            )
            # One bad channel (unknown name, or -- once callers pass Query --
            # a malformed XPath filter) must not abort subscription setup for
            # the rest: isolate per channel, same swallow-the-whole-exception
            # convention as the bookmark decode above, no win32 error-code
            # special-casing.
            try:
                sub = win32evtlog.EvtSubscribe(
                    channel, flags, Callback=self._make_callback(channel),
                    Bookmark=bookmark, Query=spec.query,
                )
            except pywintypes.error as exc:
                with self._lock:
                    self._channel_ok[channel] = False
                    self.subscribe_errors[channel] = str(exc)
                continue
            self._subs.append(sub)
            self._channel_ok[channel] = True

    def _make_callback(self, channel: str):
        import win32evtlog

        def callback(action, _context, event):
            if action == win32evtlog.EvtSubscribeActionError:
                with self._lock:
                    self._channel_ok[channel] = False
                return 0
            if action != win32evtlog.EvtSubscribeActionDeliver:
                return 0
            xml_text = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            fields = parse_event_xml(xml_text)
            # Bookmark inside the callback: the Event handle is only valid
            # for this call (spikes/windows-support/evtlog_spike.py's
            # hard-won finding -- bookmarking after the callback returns
            # raises EvtUpdateBookmark/'invalid handle').
            bookmark = win32evtlog.EvtCreateBookmark(None)
            win32evtlog.EvtUpdateBookmark(bookmark, event)
            bookmark_xml = win32evtlog.EvtRender(bookmark, win32evtlog.EvtRenderBookmark)
            with self._lock:
                self._bookmarks[channel] = bookmark_xml
                if fields is None:
                    self.malformed += 1
                else:
                    if len(self._queue) == self._queue.maxlen:
                        self.dropped += 1
                    self._queue.append(fields)
            return 0

        return callback

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        out: list[EventRecord] = []
        with self._lock:
            while self._queue and len(out) < max_items:
                out.append(EventRecord(ingest_ts=now, **self._queue.popleft()))
            cursor = json.dumps(self._bookmarks) if self._bookmarks else None
        return out, cursor

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def configured_paths(self) -> frozenset[str]:
        """Channels this instance is (or, before start(), will be)
        configured for -- daemon.py uses this on rescan to notice a newly
        loaded monitor requesting a channel nobody subscribed to yet
        (start() only ever runs once per daemon lifetime, see configure())."""
        return frozenset(spec.path for spec in self._channels)

    def alive(self) -> bool:
        with self._lock:
            return bool(self._subs) and any(self._channel_ok.values())

    def stop(self) -> None:
        self._subs = []  # drop the last reference; pywin32 handles close on GC
        self._channel_ok = {}

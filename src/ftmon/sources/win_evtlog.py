"""Windows Event Log EventSource (SA-03/10, DM-07/08/15/19/20, SA-08, PL-01/02).

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
from ftmon.sources.repeats import merge_adjacent

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
    cursor_name = "eventlog"

    def __init__(self, channels: tuple[str, ...] = _DEFAULT_CHANNELS):
        self._channels: tuple[ChannelSpec, ...] = tuple(
            ChannelSpec(path=c) for c in channels
        )
        self._subs: list = []
        self._channel_ok: dict[str, bool] = {}
        # deque(maxlen=N) drops from the head on overflow -- SA-08's "oldest
        # are dropped"; the lock also guards the committed-bookmark dict and
        # counters. Each queue entry is {"fields": dict | None, "bookmarks":
        # {channel: bookmark_xml}} -- fields is None for a malformed record
        # (still occupies a slot so drain() passes over it in order instead
        # of replaying it forever), and "bookmarks" carries every channel's
        # position represented by that entry (more than one after a
        # cross-channel merge, DM-20).
        self._queue: collections.deque[dict] = collections.deque(maxlen=QUEUE_MAX)
        self._lock = threading.Lock()
        # DM-15: only drain() may move a channel's entry into _committed --
        # never the callback -- so a crash never checkpoints past an event
        # still sitting undrained in the queue.
        self._committed: dict[str, str] = {}
        self.dropped = 0
        self.malformed = 0
        self.received = 0
        self.repeated = 0
        # channel -> str(exception) for the most recent EvtSubscribe failure;
        # EventEngine.tick() surfaces each once as a self-event.
        self.subscribe_errors: dict[str, str] = {}

    def configure(self, channels: tuple[ChannelSpec, ...]) -> None:
        """Pre-start channel/query *extension* (daemon.py's _start_events(),
        called once before the first start() -- see win_evtlog.py's module
        docstring: there is exactly one EvtSubscribe pass per daemon
        lifetime, not a hot-reconfigure path). Adds to the existing channel
        set (the constructor default, System/Application in production)
        rather than replacing it -- a monitor declaring `channels =
        [{path="Security"}]` must gain Security, not lose System/Application
        and the built-in rules that depend on them (e.g. events.toml's
        unexpected-shutdown rule needs System). A path already present is
        replaced by the incoming spec (so an explicit query can narrow a
        default channel); a new path is appended. A no-op once subscriptions
        already exist: changing channels after that needs a restart, same
        as the rest of this class's one-shot-subscribe design."""
        if self._subs:
            return
        merged = {spec.path: spec for spec in self._channels}
        for spec in channels:
            merged[spec.path] = spec
        self._channels = tuple(merged.values())

    def start(self, cursor: str | None) -> None:
        import pywintypes
        import win32evtlog

        self._subs = []
        self._channel_ok = {}
        # New reader epoch: any undrained entries from a prior epoch on this
        # same instance (e.g. a reconnect) belong to a subscription that no
        # longer exists, and _committed is about to be reseeded from this
        # cursor's own evidence -- neither may leak across the boundary.
        with self._lock:
            self._queue.clear()
            self._committed = {}
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

            if bookmark is not None:
                # DM-15: seed the committed cursor with this channel's valid
                # prior position *before* subscribing it -- EvtSubscribe may
                # deliver on a callback thread the instant it's called, and
                # a concurrent drain(max_items=0) must never see this
                # channel missing from the composite cursor just because
                # subscription hadn't gotten around to recording it yet.
                with self._lock:
                    self._committed[channel] = bookmark_xml

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
                # Async, post-subscribe failure (e.g. the channel becomes
                # unavailable after a successful initial EvtSubscribe) --
                # `event` is a Win32 error code here, not a real event
                # handle. Must land in subscribe_errors too, the same as a
                # synchronous EvtSubscribe() failure: alive() only goes
                # False when *every* channel is down, so if others stay
                # healthy this is the only signal this channel ever gets.
                with self._lock:
                    self._channel_ok[channel] = False
                    self.subscribe_errors[channel] = f"async subscribe error (code {event})"
                return 0
            if action != win32evtlog.EvtSubscribeActionDeliver:
                return 0
            xml_text = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            fields = parse_event_xml(xml_text)
            # Bookmark inside the callback: the Event handle is only valid
            # for this call (spikes/windows-support/evtlog_spike.py's
            # hard-won finding -- bookmarking after the callback returns
            # raises EvtUpdateBookmark/'invalid handle'). This is evidence
            # only, though -- DM-15 forbids committing it until drain()
            # actually removes the entry it belongs to.
            bookmark = win32evtlog.EvtCreateBookmark(None)
            win32evtlog.EvtUpdateBookmark(bookmark, event)
            bookmark_xml = win32evtlog.EvtRender(bookmark, win32evtlog.EvtRenderBookmark)
            with self._lock:
                self._offer_locked(channel, bookmark_xml, fields)
            return 0

        return callback

    def _offer_locked(self, channel: str, bookmark_xml: str, fields: dict | None) -> None:
        """Queue one channel/bookmark-evidence pair. A malformed record
        (fields=None) still takes a slot so its bookmark can only commit
        via drain(), in order with everything ahead of it -- never merged,
        since a malformed record breaks the adjacency run."""
        if fields is None:
            self.malformed += 1
        else:
            self.received += 1
            if self._queue:
                top = self._queue[-1]
                if top["fields"] is not None and merge_adjacent(top["fields"], fields):
                    self.repeated += 1
                    top["bookmarks"][channel] = bookmark_xml  # latest position for this channel
                    return
        if len(self._queue) == self._queue.maxlen:
            # SA-08: the evicted entry's bookmark evidence is discarded, not
            # committed -- it may only be passed later, by a subsequent
            # accepted event for the same channel advancing further.
            self.dropped += 1
        self._queue.append({"fields": fields, "bookmarks": {channel: bookmark_xml}})

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        out: list[EventRecord] = []
        with self._lock:
            popped = 0
            while self._queue and popped < max_items:
                entry = self._queue.popleft()
                popped += 1
                self._committed.update(entry["bookmarks"])  # DM-15: commit only what's removed
                if entry["fields"] is not None:
                    out.append(EventRecord(ingest_ts=now, **entry["fields"]))
            cursor = json.dumps(self._committed) if self._committed else None
        return out, cursor

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def queue_capacity(self) -> int:
        return int(self._queue.maxlen or 0)

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

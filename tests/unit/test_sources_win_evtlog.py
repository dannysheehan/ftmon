"""[DM-07][DM-08][DM-13][DM-15][PL-01][PL-02][PL-03][PL-05][SA-08] Windows
Event Log parsing, severity mapping, and EventSource queue/cursor mechanics.

Fixture XML in TestParseEventXml is real EvtRenderEventXml output captured
from this machine's System and Application channels (SPEC.md's "captured
real samples as fixtures" guidance for DM-08 tables), not hand-authored.
"""

from __future__ import annotations

import json
import sys

import pytest

from ftmon.model import EventRecord
from ftmon.sources.base import SOURCE_DECLS
from ftmon.sources.repeats import occurrence_count
from ftmon.sources.win_evtlog import (
    LEVEL_TO_SEVERITY,
    WindowsEventSource,
    parse_event_xml,
)


def _fields(**overrides) -> dict:
    base = {
        "ts": 1.0, "source": "eventlog", "provider": "p",
        "event_id": "42", "severity": 2, "message": "same",
    }
    base.update(overrides)
    return base

# --- real captured samples (spikes/windows-support/NOTES.md-adjacent capture) ---

SYS_SAMPLE_DCOM_WARNING = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
    "<System><Provider Name='Microsoft-Windows-DistributedCOM' "
    "Guid='{1B562E86-B7AA-4131-BADC-B6F3A001407E}' EventSourceName='DCOM'/>"
    "<EventID Qualifiers='0'>10016</EventID><Version>0</Version><Level>3</Level>"
    "<Task>0</Task><Opcode>0</Opcode><Keywords>0x8080000000000000</Keywords>"
    "<TimeCreated SystemTime='2026-07-25T07:57:19.8931204Z'/>"
    "<EventRecordID>106610</EventRecordID>"
    "<Correlation ActivityID='{e9e18570-19ac-0008-827f-c3ebac19dd01}'/>"
    "<Execution ProcessID='1980' ThreadID='10292'/><Channel>System</Channel>"
    "<Computer>4070TI</Computer>"
    "<Security UserID='S-1-5-21-1313809561-7131544-1056195617-1002'/></System>"
    "<EventData><Data Name='param1'>application-specific</Data>"
    "<Data Name='param2'>Local</Data><Data Name='param3'>Activation</Data>"
    "</EventData></Event>"
)

SYS_SAMPLE_KERNEL_GENERAL_INFO = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
    "<System><Provider Name='Microsoft-Windows-Kernel-General' "
    "Guid='{a68ca8b7-004f-d7b6-a698-07e2de0f1f5d}'/><EventID>15</EventID>"
    "<Version>0</Version><Level>4</Level><Task>10</Task><Opcode>0</Opcode>"
    "<Keywords>0x8000000000000000</Keywords>"
    "<TimeCreated SystemTime='2026-07-25T07:48:35.1319514Z'/>"
    "<EventRecordID>106609</EventRecordID><Correlation/>"
    "<Execution ProcessID='24116' ThreadID='58024'/><Channel>System</Channel>"
    "<Computer>4070TI</Computer><Security UserID='S-1-5-18'/></System>"
    "<EventData><Data Name='HiveNameLength'>171</Data>"
    "<Data Name='OriginalSize'>147009536</Data></EventData></Event>"
)

APP_SAMPLE_SPP_UNNAMED_DATA = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
    "<System><Provider Name='Microsoft-Windows-Security-SPP' "
    "Guid='{E23B33B0-C8C9-472C-A5F9-F2BDFEA0F156}' "
    "EventSourceName='Software Protection Platform Service'/>"
    "<EventID Qualifiers='16384'>16384</EventID><Version>0</Version><Level>4</Level>"
    "<Task>0</Task><Opcode>0</Opcode><Keywords>0x80000000000000</Keywords>"
    "<TimeCreated SystemTime='2026-07-25T08:13:15.6353751Z'/>"
    "<EventRecordID>77702</EventRecordID><Correlation/>"
    "<Execution ProcessID='55560' ThreadID='0'/><Channel>Application</Channel>"
    "<Computer>4070TI</Computer><Security/></System>"
    "<EventData><Data>2126-07-01T08:13:15Z</Data><Data>RulesEngine</Data>"
    "</EventData></Event>"
)

APP_SAMPLE_SPP_EMPTY_EVENTDATA = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
    "<System><Provider Name='Microsoft-Windows-Security-SPP' "
    "Guid='{E23B33B0-C8C9-472C-A5F9-F2BDFEA0F156}' "
    "EventSourceName='Software Protection Platform Service'/>"
    "<EventID Qualifiers='49152'>16394</EventID><Version>0</Version><Level>4</Level>"
    "<Task>0</Task><Opcode>0</Opcode><Keywords>0x80000000000000</Keywords>"
    "<TimeCreated SystemTime='2026-07-25T08:12:45.4058740Z'/>"
    "<EventRecordID>77701</EventRecordID><Correlation/>"
    "<Execution ProcessID='55560' ThreadID='0'/><Channel>Application</Channel>"
    "<Computer>4070TI</Computer><Security/></System><EventData></EventData></Event>"
)


class TestParseEventXml:
    def test_level_to_severity_table_is_exact(self):
        """[DM-08] Windows Level 1-5 -> ftmon severity, no surprise entries."""
        assert LEVEL_TO_SEVERITY == {1: 4, 2: 3, 3: 2, 4: 0, 5: 0}

    def test_real_system_dcom_warning_golden(self):
        """[DM-07][DM-08] Real captured System-channel sample, named Data."""
        fields = parse_event_xml(SYS_SAMPLE_DCOM_WARNING)
        assert fields["source"] == "eventlog"
        assert fields["provider"] == "Microsoft-Windows-DistributedCOM"
        assert fields["event_id"] == "10016"
        assert fields["severity"] == 2  # Level 3 (Warning) -> warning
        assert fields["ts"] == pytest.approx(1784966239.893, abs=0.01)
        assert "param1=application-specific" in fields["message"]

    def test_real_system_kernel_general_info_golden(self):
        """[DM-08] Level 4 (Information) -> info; unqualified EventID."""
        fields = parse_event_xml(SYS_SAMPLE_KERNEL_GENERAL_INFO)
        assert fields["event_id"] == "15"
        assert fields["severity"] == 0
        assert "HiveNameLength=171" in fields["message"]

    def test_real_application_unnamed_data_joined(self):
        """[DM-07] Data elements without a Name attribute join as bare values."""
        fields = parse_event_xml(APP_SAMPLE_SPP_UNNAMED_DATA)
        assert fields["message"] == "2126-07-01T08:13:15Z; RulesEngine"

    def test_real_application_empty_eventdata_falls_back(self):
        """No EventData content -> synthesized EventID/provider fallback message."""
        fields = parse_event_xml(APP_SAMPLE_SPP_EMPTY_EVENTDATA)
        assert fields["message"] == "EventID 16394 (Microsoft-Windows-Security-SPP)"

    def test_malformed_xml_returns_none(self):
        """[SA-08] Unparseable input is skipped, never fatal."""
        assert parse_event_xml("not xml at all") is None

    def test_missing_system_element_returns_none(self):
        assert parse_event_xml(
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'/>"
        ) is None

    def test_missing_level_defaults_to_info(self):
        """[DM-08] Absent Level, same fallback style as PRIORITY_TO_SEVERITY.get."""
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            "<System><Provider Name='Test'/><EventID>1</EventID>"
            "<TimeCreated SystemTime='2026-01-01T00:00:00.000000Z'/></System>"
            "<EventData/></Event>"
        )
        fields = parse_event_xml(xml)
        assert fields["severity"] == 0
        assert fields["provider"] == "Test"

    def test_missing_provider_defaults_to_unknown(self):
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            "<System><EventID>1</EventID><Level>1</Level></System></Event>"
        )
        fields = parse_event_xml(xml)
        assert fields["provider"] == "unknown"
        assert fields["severity"] == 4  # Level 1 Critical -> critical

    def test_missing_event_id_is_none_pl_02(self):
        """[PL-02] event_id is optional; absent EventID -> None, not '0' or ''."""
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            "<System><Provider Name='NoIdProvider'/><Level>2</Level></System>"
            "</Event>"
        )
        fields = parse_event_xml(xml)
        assert fields["event_id"] is None
        assert fields["message"] == "(NoIdProvider)"

    def test_unparseable_timecreated_defaults_to_zero(self):
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            "<System><Provider Name='P'/><EventID>1</EventID>"
            "<TimeCreated SystemTime='not-a-timestamp'/></System></Event>"
        )
        fields = parse_event_xml(xml)
        assert fields["ts"] == 0.0

    def test_message_truncated_at_2kb_dm_13(self):
        """[DM-13] Event messages truncate at 2 KB, same bound as journald."""
        long_value = "x" * 5000
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            "<System><Provider Name='P'/><EventID>1</EventID><Level>4</Level></System>"
            f"<EventData><Data Name='d'>{long_value}</Data></EventData></Event>"
        )
        fields = parse_event_xml(xml)
        assert len(fields["message"]) == 2048


class TestWindowsEventSourceQueueMechanics:
    """Pure queue/cursor logic -- exercised without any live EvtSubscribe
    call, by driving the same lock-protected internals the real callback
    uses (test_notify_desktop.py asserts on adapter internals the same way).

    DM-15's contract: a channel's bookmark evidence may commit into the
    returned cursor only when drain() actually removes the queue entry
    carrying it -- never on arrival in the callback."""

    def test_source_decl_matches_events_schema_pl_05(self):
        assert WindowsEventSource.decl is SOURCE_DECLS["events"]

    def test_not_alive_before_start(self):
        assert WindowsEventSource().alive() is False

    def test_adjacent_run_is_coalesced_before_queue_admission_dm_18(self):
        """[DM-18] Windows uses the same origin-aware repeat contract."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "bookmark-1", _fields())
            src._offer_locked("System", "bookmark-2", _fields(ts=2.0))
        records, cursor = src.drain(now=42.0, max_items=10)
        assert len(records) == 1
        assert records[0].attrs["repeat_count"] == "2"
        assert src.received == 2 and src.repeated == 1
        assert json.loads(cursor) == {"System": "bookmark-2"}

    def test_drain_stamps_ingest_ts_and_serializes_composite_cursor(self):
        """[DM-15] cursor is a per-channel bookmark map, committed by drain."""
        src = WindowsEventSource()
        with src._lock:
            src._queue.append({
                "fields": _fields(event_id=None, message="m"),
                "bookmarks": {"System": "<BookmarkList/>"},
            })
            src._queue.append({
                "fields": _fields(event_id=None, message="n"),
                "bookmarks": {"Application": "<BookmarkList/>"},
            })
        records, cursor = src.drain(now=42.0, max_items=10)
        assert len(records) == 2
        assert isinstance(records[0], EventRecord)
        assert records[0].ingest_ts == 42.0
        assert json.loads(cursor) == {
            "System": "<BookmarkList/>", "Application": "<BookmarkList/>",
        }

    def test_drain_respects_max_items(self):
        src = WindowsEventSource()
        with src._lock:
            for i in range(5):
                src._offer_locked(
                    "System", f"bookmark-{i}",
                    _fields(ts=float(i), event_id=None, message=str(i)),
                )
        records, _ = src.drain(now=0.0, max_items=2)
        assert len(records) == 2
        assert src.queue_depth() == 3

    def test_drain_with_no_bookmarks_returns_none_cursor(self):
        src = WindowsEventSource()
        records, cursor = src.drain(now=0.0, max_items=10)
        assert records == []
        assert cursor is None

    def test_queue_overflow_increments_dropped_sa_08(self):
        """[SA-08] Bounded queue drops the oldest on overflow."""
        src = WindowsEventSource()
        from ftmon.sources.win_evtlog import QUEUE_MAX

        with src._lock:
            for i in range(QUEUE_MAX):
                src._offer_locked(
                    "System", f"bookmark-{i}",
                    _fields(ts=float(i), event_id=None, message=f"m{i}"),
                )
            src._offer_locked(
                "System", "overflow",
                _fields(ts=99.0, event_id=None, message="overflow"),
            )
        assert src.dropped == 1
        assert src.queue_depth() == QUEUE_MAX

    # --- DM-15 checkpoint-correctness ---

    def test_partial_drain_commits_only_the_drained_bookmark_dm_15(self):
        """Three queued events in one channel, drain one: the cursor must
        contain bookmark 1's evidence, not bookmark 3's -- the two
        undrained events exist only in memory and must be replayable."""
        src = WindowsEventSource()
        with src._lock:
            for i in (1, 2, 3):
                src._offer_locked(
                    "System", f"bookmark-{i}",
                    _fields(ts=float(i), event_id=str(i), message=f"m{i}"),
                )
        records, cursor = src.drain(now=0.0, max_items=1)
        assert len(records) == 1
        assert json.loads(cursor) == {"System": "bookmark-1"}
        assert src.queue_depth() == 2

    def test_drain_zero_does_not_advance_checkpoint_dm_15(self):
        """drain(max_items=0) must not commit anything or fabricate a
        cursor, and must not disturb an already-committed one."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "bookmark-1", _fields(event_id="1", message="m"))
        records, cursor = src.drain(now=0.0, max_items=0)
        assert records == []
        assert cursor is None  # nothing has ever been drained/committed
        assert src.queue_depth() == 1

        records, cursor = src.drain(now=0.0, max_items=1)
        assert len(records) == 1
        assert json.loads(cursor) == {"System": "bookmark-1"}

        records, cursor2 = src.drain(now=0.0, max_items=0)
        assert records == []
        assert cursor2 == cursor  # unchanged, not re-derived or advanced

    def test_partial_drains_across_multiple_channels_dm_15(self):
        """Committing one channel's bookmark must not disturb another
        channel's still-undrained position."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "sys-1", _fields(event_id="1", message="s1"))
            src._offer_locked("Application", "app-1", _fields(event_id="2", message="a1"))
            src._offer_locked("System", "sys-2", _fields(event_id="3", message="s2"))

        records, cursor = src.drain(now=0.0, max_items=1)
        assert len(records) == 1
        assert json.loads(cursor) == {"System": "sys-1"}  # Application untouched

        records, cursor = src.drain(now=0.0, max_items=1)
        assert len(records) == 1
        assert json.loads(cursor) == {"System": "sys-1", "Application": "app-1"}

        records, cursor = src.drain(now=0.0, max_items=1)
        assert len(records) == 1
        assert json.loads(cursor) == {"System": "sys-2", "Application": "app-1"}

    def test_adjacent_duplicates_one_channel_latest_bookmark_dm_15(self):
        """One EventRecord, correct repeat_count, and the bookmark committed
        on drain is the *latest* of the coalesced run, not the first."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "bookmark-1", _fields())
            src._offer_locked("System", "bookmark-2", _fields(ts=2.0))
            src._offer_locked("System", "bookmark-3", _fields(ts=3.0))
        assert src.queue_depth() == 1

        records, cursor = src.drain(now=0.0, max_items=10)
        assert len(records) == 1
        assert records[0].attrs["repeat_count"] == "3"
        assert json.loads(cursor) == {"System": "bookmark-3"}

    def test_identical_events_two_channels_one_aggregate_both_bookmarks_dm_15(self):
        """Identical canonical events arriving from different channels
        coalesce into one aggregate that retains each represented channel's
        own latest bookmark."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "sys-1", _fields())
            src._offer_locked("Application", "app-1", _fields(ts=2.0))
        assert src.queue_depth() == 1

        records, cursor = src.drain(now=0.0, max_items=10)
        assert len(records) == 1
        assert records[0].attrs["repeat_count"] == "2"
        assert json.loads(cursor) == {"System": "sys-1", "Application": "app-1"}

    def test_malformed_entry_consumed_without_replay_loop_dm_15_sa_08(self):
        """A malformed-but-consumed entry still occupies its queue slot so
        drain() advances past it in order once it's actually drained -- it
        must neither block nor be skipped ahead of an earlier, still
        undrained, valid event."""
        src = WindowsEventSource()
        with src._lock:
            src._offer_locked("System", "sys-1", _fields(event_id="1", message="valid-1"))
            src._offer_locked("System", "sys-2", None)  # malformed: no fields
            src._offer_locked("System", "sys-3", _fields(event_id="3", message="valid-3"))
        assert src.queue_depth() == 3

        records, cursor = src.drain(now=0.0, max_items=1)
        assert [r.message for r in records] == ["valid-1"]
        assert json.loads(cursor) == {"System": "sys-1"}  # not sys-2 or sys-3

        records, cursor = src.drain(now=0.0, max_items=1)
        assert records == []  # the malformed entry produces no EventRecord
        assert json.loads(cursor) == {"System": "sys-2"}  # but its slot commits

        records, cursor = src.drain(now=0.0, max_items=1)
        assert [r.message for r in records] == ["valid-3"]
        assert json.loads(cursor) == {"System": "sys-3"}

    def test_overflow_dropped_entry_does_not_commit_its_bookmark_dm_15_sa_08(self):
        """An overflow-evicted entry's bookmark is discarded, not committed
        -- only a later accepted event for that channel may pass it, per
        the intentional SA-08 loss policy."""
        src = WindowsEventSource()
        from ftmon.sources.win_evtlog import QUEUE_MAX

        with src._lock:
            for i in range(QUEUE_MAX):
                src._offer_locked(
                    "System", f"bookmark-{i}",
                    _fields(ts=float(i), event_id=str(i), message="m"),
                )
            # bookmark-0's entry is about to be evicted by this append.
            src._offer_locked(
                "System", "bookmark-overflow",
                _fields(ts=99.0, event_id="over", message="overflow"),
            )
        assert src.dropped == 1
        assert src.queue_depth() == QUEUE_MAX

        records, cursor = src.drain(now=0.0, max_items=1)
        assert records[0].event_id == "1"  # bookmark-0 was dropped, never queued
        assert json.loads(cursor) == {"System": "bookmark-1"}

    def test_episode_occurrence_count_reflects_all_coalesced_events_dm_15(self):
        """A merged aggregate's repeat_count is what engine occurrence
        accounting (ftmon.engine.events._occurrence_count) reads, so an
        episode counts every raw event coalesced into the one EventRecord,
        not just one."""
        src = WindowsEventSource()
        with src._lock:
            for i, bookmark in enumerate(("b1", "b2", "b3", "b4")):
                src._offer_locked("System", bookmark, _fields(ts=float(i)))
        records, _cursor = src.drain(now=0.0, max_items=10)
        assert len(records) == 1
        assert occurrence_count({"attrs": records[0].attrs}) == 4


@pytest.mark.skipif(sys.platform != "win32", reason="win32evtlog is Windows-only")
class TestWindowsEventSourceLive:
    """Fast, deterministic checks against the real Win32 Event Log API (no
    waiting for live event delivery -- that's covered by manual smoke
    testing per PLAN's two-tier testing philosophy, not the unit gate)."""

    def test_start_stop_against_real_application_channel(self):
        src = WindowsEventSource(channels=("Application",))
        try:
            src.start(None)
            assert src.alive() is True
        finally:
            src.stop()
        assert src.alive() is False

    def test_malformed_cursor_json_is_counted_not_fatal(self):
        """[SA-08][PL-03] A corrupt/foreign persisted cursor degrades
        gracefully instead of crashing the daemon."""
        src = WindowsEventSource(channels=())  # no real subscriptions needed
        src.start("not valid json{{{")
        assert src.malformed == 1

    def test_stale_channel_bookmark_is_counted_not_fatal(self):
        """A well-formed but bogus bookmark for a channel starts that
        channel fresh rather than raising (EvtCreateBookmark rejects
        non-well-formed XML with a catchable pywintypes.error)."""
        src = WindowsEventSource(channels=("Application",))
        try:
            src.start(json.dumps({"Application": "not valid bookmark xml"}))
            assert src.malformed == 1
            assert src.alive() is True  # still subscribed, just from now
        finally:
            src.stop()

    def test_restart_resumes_from_partial_drain_cursor_dm_15(self):
        """[DM-15] Crash/restart: the cursor produced by a *partial* drain
        must be exactly what a fresh instance resumes from -- real
        EvtCreateBookmark/EvtSubscribe accept it and the channel comes back
        alive from that committed point, not from whatever the callback
        last saw arrive."""
        import win32evtlog

        # EvtCreateBookmark(None) + EvtRender is the same round trip the
        # callback performs per event; it's synchronous and needs no live
        # event to produce valid bookmark XML.
        real_bookmark_xml = win32evtlog.EvtRender(
            win32evtlog.EvtCreateBookmark(None), win32evtlog.EvtRenderBookmark
        )

        src = WindowsEventSource(channels=("Application",))
        with src._lock:
            src._offer_locked(
                "Application", real_bookmark_xml,
                _fields(event_id="1", message="drained"),
            )
            src._offer_locked(
                "Application", "still in flight, never drained",
                _fields(ts=2.0, event_id="2", message="undrained"),
            )
        records, cursor = src.drain(now=0.0, max_items=1)  # partial: 1 of 2
        assert len(records) == 1
        assert json.loads(cursor) == {"Application": real_bookmark_xml}

        resumed = WindowsEventSource(channels=("Application",))
        try:
            resumed.start(cursor)  # simulates the post-crash restart
            assert resumed.malformed == 0  # the committed bookmark is well-formed
            assert resumed.alive() is True
        finally:
            resumed.stop()

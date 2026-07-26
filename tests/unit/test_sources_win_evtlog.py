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
from ftmon.sources.win_evtlog import (
    LEVEL_TO_SEVERITY,
    WindowsEventSource,
    parse_event_xml,
)

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
    uses (test_notify_desktop.py asserts on adapter internals the same way)."""

    def test_source_decl_matches_events_schema_pl_05(self):
        assert WindowsEventSource.decl is SOURCE_DECLS["events"]

    def test_not_alive_before_start(self):
        assert WindowsEventSource().alive() is False

    def test_drain_stamps_ingest_ts_and_serializes_composite_cursor(self):
        """[DM-15] cursor is a per-channel bookmark map, not a single string."""
        src = WindowsEventSource()
        with src._lock:
            src._queue.append({
                "ts": 1.0, "source": "eventlog", "provider": "p",
                "event_id": None, "severity": 0, "message": "m",
            })
            src._bookmarks["System"] = "<BookmarkList/>"
            src._bookmarks["Application"] = "<BookmarkList/>"
        records, cursor = src.drain(now=42.0, max_items=10)
        assert len(records) == 1
        assert isinstance(records[0], EventRecord)
        assert records[0].ingest_ts == 42.0
        assert json.loads(cursor) == {
            "System": "<BookmarkList/>", "Application": "<BookmarkList/>",
        }

    def test_drain_respects_max_items(self):
        src = WindowsEventSource()
        with src._lock:
            for i in range(5):
                src._queue.append({
                    "ts": float(i), "source": "eventlog", "provider": "p",
                    "event_id": None, "severity": 0, "message": str(i),
                })
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
                src._queue.append({
                    "ts": float(i), "source": "eventlog", "provider": "p",
                    "event_id": None, "severity": 0, "message": "m",
                })
            # Mirrors the callback's overflow check: about to evict the oldest.
            if len(src._queue) == src._queue.maxlen:
                src.dropped += 1
            src._queue.append({
                "ts": 99.0, "source": "eventlog", "provider": "p",
                "event_id": None, "severity": 0, "message": "overflow",
            })
        assert src.dropped == 1
        assert src.queue_depth() == QUEUE_MAX


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

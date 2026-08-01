"""[DM-07][DM-08][DM-15][SA-08][PL-01] macOS unified-log adapter."""

from __future__ import annotations

import collections
import json

from ftmon.sources.oslog import (
    IDENTITY_MAX,
    OPERATIONAL_PREDICATE,
    QUEUE_MAX,
    MacOSLogEventSource,
    _decode_checkpoint,
    event_identity,
    parse_line,
)


def _line(**changes) -> bytes:
    raw = {
        "eventType": "logEvent",
        "timestamp": "2026-07-26 10:11:12.123456+1000",
        "eventMessage": "disk failed",
        "subsystem": "com.example.storage",
        "category": "io",
        "messageType": "Error",
        "bootUUID": "boot",
        "machTimestamp": 123,
        "traceID": 456,
        "processID": 789,
        "senderProgramCounter": 1011,
    }
    raw.update(changes)
    return json.dumps(raw).encode()


def test_parse_real_shaped_log_event_and_severity_dm_08():
    parsed = parse_line(_line())
    assert parsed is not None
    fields, identity = parsed
    assert fields["source"] == "oslog"
    assert fields["provider"] == "com.example.storage/io"
    assert fields["severity"] == 3
    assert fields["message"] == "disk failed"
    assert len(identity) == 64


def test_operational_classes_are_normalized_before_rule_evaluation_dm_08():
    third_party = parse_line(_line(
        subsystem="",
        category="",
        messageType="Fault",
        processImagePath="/opt/homebrew/bin/postgres",
    ))
    assert third_party is not None
    assert third_party[0]["provider"] == "postgres"
    assert third_party[0]["event_id"] == "third-party-fault"

    storage = parse_line(_line(
        subsystem="",
        category="",
        messageType="Error",
        process="kernel",
        processImagePath="/System/Library/Kernels/kernel",
        eventMessage="APFS filesystem corruption detected",
    ))
    assert storage is not None
    assert storage[0]["event_id"] == "storage-integrity"

    routine_kernel_fault = parse_line(_line(
        subsystem="",
        category="",
        messageType="Fault",
        process="kernel",
        processImagePath="/System/Library/Kernels/kernel",
        eventMessage="IOAccelDisplayPipe display change completed",
    ))
    assert routine_kernel_fault is not None
    assert routine_kernel_fault[0]["event_id"] is None


def test_default_reader_filters_before_ingestion_sa_08():
    """[SA-08] macOS never starts an ambient debug-level unified-log tail."""
    args = MacOSLogEventSource()._args("stream")
    assert args[:5] == ["/usr/bin/log", "stream", "--style", "ndjson", "--level"]
    assert args[5] == "default"
    assert args[-2:] == ["--predicate", OPERATIONAL_PREDICATE]
    assert 'messageType == fault' in OPERATIONAL_PREDICATE
    assert 'process == "kernel"' in OPERATIONAL_PREDICATE
    assert 'processImagePath BEGINSWITH "/opt/"' in OPERATIONAL_PREDICATE
    assert 'process == "kernel" AND (messageType == fault' not in OPERATIONAL_PREDICATE


def test_parser_ignores_filter_text_blank_and_terminal_count():
    assert parse_line(b"Filtering the log data using ...") is None
    assert parse_line(b"") is None
    assert parse_line(b'{"count":2,"finished":1}') is None


def test_identity_survives_stream_archive_timestamp_drift_dm_15():
    first = json.loads(_line())
    later = dict(first, timestamp="2026-07-26 10:11:12.139999+1000")
    assert event_identity(first) == event_identity(later)


def test_checkpoint_is_bounded_and_advances_only_for_drained_records():
    src = MacOSLogEventSource()
    parsed = parse_line(_line())
    assert parsed is not None
    fields, identity = parsed
    src._offer(parsed)
    assert src.drain(100.0, 0) == ([], None)
    records, cursor = src.drain(100.0, 1)
    assert len(records) == 1
    assert cursor is not None
    watermark, identities = _decode_checkpoint(cursor)
    assert watermark == fields["ts"]
    assert identities == [(fields["ts"], identity)]

    oversized = json.dumps(
        {"watermark": 10, "identities": [[10, str(i)] for i in range(IDENTITY_MAX + 4)]}
    )
    _, bounded = _decode_checkpoint(oversized)
    assert len(bounded) == IDENTITY_MAX


def test_retention_gap_is_an_observable_self_event_dm_15():
    src = MacOSLogEventSource()
    src._enqueue_gap(10.0, 20.0)
    records, _ = src.drain(21.0, 1)
    assert records[0].source == "self"
    assert records[0].event_id == "retention-gap"
    assert records[0].severity == 2


def test_overflow_drops_oldest_without_leaking_identity_state_sa_08():
    """[SA-08][DM-15] A burst retains the newest bounded queue and forgets
    identities evicted before they could advance the durable checkpoint."""
    src = MacOSLogEventSource()
    src._queue = collections.deque(maxlen=3)

    for i in range(5):
        parsed = parse_line(
            _line(machTimestamp=i, traceID=i, eventMessage=f"message {i}")
        )
        assert parsed is not None
        src._offer(parsed)

    assert src.dropped == 2
    assert src.queue_depth() == 3
    assert src.dedup_size() == 5
    records, _cursor = src.drain(now=100.0, max_items=10)
    assert [record.message for record in records] == ["message 2", "message 3", "message 4"]
    assert src.dedup_size() == 5
    _watermark, committed = _decode_checkpoint(_cursor)
    expected = {
        parse_line(_line(machTimestamp=i, traceID=i, eventMessage=f"message {i}"))[1]
        for i in range(2, 5)
    }
    assert {identity for _ts, identity in committed} == expected


def test_adjacent_duplicate_run_is_coalesced_before_queue_admission_dm_18():
    """[DM-18] Raw rate/count stay honest while one ordered aggregate is queued."""
    src = MacOSLogEventSource()
    for i in range(3):
        parsed = parse_line(_line(
            machTimestamp=100 + i,
            traceID=200 + i,
            timestamp=f"2026-07-26 10:11:1{2 + i}.123456+1000",
        ))
        assert parsed is not None
        src._offer(parsed)

    assert src.queue_depth() == 1
    assert src.received == 3
    assert src.repeated == 2
    records, cursor = src.drain(now=100.0, max_items=10)
    assert cursor is not None
    assert len(records) == 1
    assert records[0].attrs["repeat_count"] == "3"
    assert records[0].attrs["repeat_first_ts"] != records[0].attrs["repeat_last_ts"]
    watermark, identities = _decode_checkpoint(cursor)
    assert watermark == float(records[0].attrs["repeat_last_ts"])
    assert len(identities) == 3

    replay = MacOSLogEventSource()
    replay_watermark, replay_identities = _decode_checkpoint(cursor)
    replay._watermark = replay_watermark
    for source_ts, identity in replay_identities:
        replay._remember_committed_locked(source_ts, identity)
    for i in range(3):
        parsed = parse_line(_line(
            machTimestamp=100 + i,
            traceID=200 + i,
            timestamp=f"2026-07-26 10:11:1{2 + i}.123456+1000",
        ))
        assert parsed is not None
        replay._offer(parsed)
    assert replay.queue_depth() == 0


def test_only_contiguous_duplicates_merge_to_preserve_cursor_order_dm_18():
    """[DM-18][DM-15] An intervening event starts a new run."""
    src = MacOSLogEventSource()
    for i, message in enumerate(("same", "other", "same")):
        parsed = parse_line(_line(
            machTimestamp=100 + i, traceID=200 + i, eventMessage=message,
        ))
        assert parsed is not None
        src._offer(parsed)
    records, _cursor = src.drain(now=100.0, max_items=10)
    assert [record.message for record in records] == ["same", "other", "same"]


def test_committed_replay_identity_window_is_explicitly_bounded_dm_15():
    """[DM-15][SA-08] Draining more than IDENTITY_MAX unique records cannot
    grow replay-deduplication state for the lifetime of the daemon."""
    src = MacOSLogEventSource()
    total = IDENTITY_MAX + 7
    for i in range(total):
        parsed = parse_line(
            _line(machTimestamp=i, traceID=i, eventMessage=f"message {i}")
        )
        assert parsed is not None
        src._offer(parsed)

    records, cursor = src.drain(now=100.0, max_items=total)
    assert len(records) == total
    assert cursor is not None
    assert src.queue_depth() == 0
    assert src.dedup_size() == IDENTITY_MAX
    _watermark, identities = _decode_checkpoint(cursor)
    assert len(identities) == IDENTITY_MAX


def test_duplicate_storm_identity_state_is_globally_bounded_dm_15_sa_08():
    """[DM-15][SA-08][DM-18] One huge run cannot allocate IDs per queue slot."""
    src = MacOSLogEventSource()
    total = IDENTITY_MAX + 200
    for i in range(total):
        parsed = parse_line(_line(machTimestamp=i, traceID=i))
        assert parsed is not None
        src._offer(parsed)

    assert src.queue_depth() == 1
    assert src.received == total
    assert src.repeated == total - 1
    assert src.dedup_size() <= src.dedup_capacity()
    records, cursor = src.drain(now=100.0, max_items=1)
    assert records[0].attrs["repeat_count"] == str(total)
    assert cursor is not None
    _watermark, identities = _decode_checkpoint(cursor)
    assert len(identities) == IDENTITY_MAX
    assert src.dedup_size() == IDENTITY_MAX


def test_default_queue_and_dedup_state_have_finite_combined_bound_sa_08():
    """[SA-08] The production bound is queue plus recent checkpoint IDs."""
    src = MacOSLogEventSource()
    assert src.queue_capacity() == QUEUE_MAX
    assert src.dedup_capacity() == QUEUE_MAX + (2 * IDENTITY_MAX)

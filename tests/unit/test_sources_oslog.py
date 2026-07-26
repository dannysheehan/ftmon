"""[DM-07][DM-08][DM-15][SA-08][PL-01] macOS unified-log adapter."""

from __future__ import annotations

import json

from ftmon.sources.oslog import (
    IDENTITY_MAX,
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
    src._queue.append((fields, identity))
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

"""macOS unified-log EventSource (SA-03/08, DM-07/08/15, PL-01/02).

Unified log has no bookmark. A JSON checkpoint therefore combines a wall-time
high-water mark with bounded event identities. Restart uses an overlapping
``log show`` replay, then hands off to ``log stream`` with the same dedup set.
"""

from __future__ import annotations

import collections
import hashlib
import json
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from ftmon.model import EventRecord, SourceDecl
from ftmon.sources.base import SOURCE_DECLS
from ftmon.sources.repeats import merge_adjacent

QUEUE_MAX = 10_000
IDENTITY_MAX = 2048
REPLAY_OVERLAP_S = 5
_MSG_MAX = 2048
MESSAGE_TYPE_TO_SEVERITY = {
    "debug": 0,
    "default": 0,
    "info": 0,
    "notice": 1,
    "error": 3,
    "fault": 4,
}

# SA-08: unified-log severity is not an admission policy. Routine Apple
# components emit error/fault records at rates that can overwhelm a user-level
# monitor, so macOS applies a fixed operational allowlist in `log(1)` before
# Python reads a byte. The same predicate is used for replay and streaming.
_THIRD_PARTY_ROOTS = ("/Applications/", "/Library/", "/opt/", "/usr/local/", "/Users/")
_STORAGE_TERMS = (
    "I/O error",
    "media error",
    "disk corruption",
    "filesystem corruption",
    "APFS corruption",
)
_THIRD_PARTY_PREDICATE = " OR ".join(
    f'processImagePath BEGINSWITH "{root}"' for root in _THIRD_PARTY_ROOTS
)
_STORAGE_PREDICATE = " OR ".join(
    f'eventMessage CONTAINS[c] "{term}"' for term in _STORAGE_TERMS
)
OPERATIONAL_PREDICATE = (
    f'(messageType == fault AND ({_THIRD_PARTY_PREDICATE}) '
    'AND NOT processImagePath BEGINSWITH "/Library/Apple/") OR '
    f'(process == "kernel" AND ({_STORAGE_PREDICATE}))'
)


@dataclass
class _QueuedEvent:
    fields: dict
    identity: str
    run_id: int
    latest_seq: int


def parse_line(line: bytes) -> tuple[dict, str] | None:
    """Normalize one NDJSON logEvent; status, count and malformed lines are ignored."""
    try:
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("eventType") != "logEvent":
        return None
    message = raw.get("eventMessage", "")
    if not isinstance(message, str):
        message = str(message)
    subsystem = raw.get("subsystem")
    process_path = str(raw.get("processImagePath", ""))
    process_name = process_path.rsplit("/", 1)[-1] or str(raw.get("process", ""))
    provider = str(subsystem) if subsystem else process_name or "unknown"
    category = raw.get("category")
    if category:
        provider = f"{provider}/{category}"
    message_type = str(raw.get("messageType", "default")).lower()
    fields = {
        "ts": _timestamp(raw.get("timestamp")),
        "source": "oslog",
        "provider": provider,
        "event_id": _event_class(raw, message_type, message),
        "severity": MESSAGE_TYPE_TO_SEVERITY.get(message_type, 0),
        "message": message[:_MSG_MAX],
    }
    return fields, event_identity(raw, fields)


def _event_class(raw: dict, message_type: str, message: str) -> str | None:
    process_path = str(raw.get("processImagePath", ""))
    process_name = str(raw.get("process", "")) or process_path.rsplit("/", 1)[-1]
    if process_name == "kernel":
        if any(term.casefold() in message.casefold() for term in _STORAGE_TERMS):
            return "storage-integrity"
    if (
        message_type == "fault"
        and process_path.startswith(_THIRD_PARTY_ROOTS)
        and not process_path.startswith("/Library/Apple/")
    ):
        return "third-party-fault"
    return None


def event_identity(raw: dict, fields: dict | None = None) -> str:
    preferred = tuple(
        str(raw.get(key, ""))
        for key in (
            "bootUUID",
            "machTimestamp",
            "traceID",
            "processID",
            "senderProgramCounter",
        )
    )
    if any(preferred):
        payload = "\x1f".join(preferred)
    else:
        normalized = fields or {
            "timestamp": raw.get("timestamp"),
            "message": raw.get("eventMessage"),
            "subsystem": raw.get("subsystem"),
            "category": raw.get("category"),
            "process": raw.get("processImagePath"),
            "pid": raw.get("processID"),
            "type": raw.get("messageType"),
        }
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _timestamp(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


class MacOSLogEventSource:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["events"]
    cursor_name = "oslog"

    def __init__(
        self,
        log_binary: str = "/usr/bin/log",
        predicate: str | None = OPERATIONAL_PREDICATE,
    ):
        self._log = log_binary
        self._predicate = predicate
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._queue: collections.deque[_QueuedEvent] = collections.deque(maxlen=QUEUE_MAX)
        self._queued_ids: set[str] = set()
        # A coalesced queue entry still represents multiple replay identities.
        # Keep one global recent window rather than an ID list per entry, which
        # would turn 10,000 repeated runs into unbounded auxiliary memory.
        self._pending_recent: collections.deque[tuple[int, int, float, str]] = (
            collections.deque(maxlen=IDENTITY_MAX)
        )
        self._pending_ids: set[str] = set()
        self._committed: collections.deque[tuple[float, str]] = collections.deque(
            maxlen=IDENTITY_MAX
        )
        self._committed_ids: set[str] = set()
        self._lock = threading.Lock()
        self._watermark = 0.0
        self._next_seq = 0
        self.dropped = 0
        self.malformed = 0
        self.received = 0
        self.repeated = 0

    def start(self, cursor: str | None) -> None:
        self.stop()
        self._stop_requested.clear()
        watermark, identities = _decode_checkpoint(cursor)
        with self._lock:
            self._watermark = watermark
            # A reader restart replays every uncommitted item from the durable
            # checkpoint. Keeping the old in-memory queue here would duplicate
            # that replay and make its identities impossible to bound.
            self._queue.clear()
            self._queued_ids.clear()
            self._pending_recent.clear()
            self._pending_ids.clear()
            self._committed.clear()
            self._committed_ids.clear()
            for source_ts, identity in identities:
                self._remember_committed_locked(source_ts, identity)
        self._thread = threading.Thread(
            target=self._run, args=(watermark,), daemon=True, name="ftmon-oslog"
        )
        self._thread.start()

    def _run(self, watermark: float) -> None:
        if watermark:
            args = self._args("show")
            args.extend(["--start", str(max(0, int(watermark) - REPLAY_OVERLAP_S))])
            if not self._consume(args, replay_watermark=watermark):
                return
        if not self._stop_requested.is_set():
            self._consume(self._args("stream"))

    def _args(self, operation: str) -> list[str]:
        args = [self._log, operation, "--style", "ndjson", "--level", "default"]
        if self._predicate:
            args.extend(["--predicate", self._predicate])
        return args

    def _consume(self, args: list[str], replay_watermark: float = 0.0) -> bool:
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        self._proc = proc
        if self._stop_requested.is_set():
            proc.terminate()
            proc.wait()
            return False
        assert proc.stdout is not None
        first_ts: float | None = None
        for line in proc.stdout:
            parsed = parse_line(line)
            if parsed is None:
                with self._lock:
                    self.malformed += 1
                continue
            fields, identity = parsed
            if first_ts is None:
                first_ts = fields["ts"]
            self._offer((fields, identity))
        if replay_watermark and first_ts is not None and first_ts > replay_watermark:
            self._enqueue_gap(replay_watermark, first_ts)
        return proc.returncode in (None, 0)

    def _enqueue_gap(self, watermark: float, earliest: float) -> None:
        fields = {
            "ts": earliest,
            "source": "self",
            "provider": "ftmon.oslog",
            "event_id": "retention-gap",
            "severity": 2,
            "message": (
                "macOS unified-log replay boundary is no longer retained; "
                f"checkpoint={watermark:.3f}, earliest={earliest:.3f}"
            ),
        }
        identity = hashlib.sha256(repr(fields).encode()).hexdigest()
        with self._lock:
            if self._known_identity_locked(identity):
                return
            self._drop_oldest_locked()
            seq = self._new_seq_locked()
            self._queue.appendleft(_QueuedEvent(fields, identity, seq, seq))
            self._queued_ids.add(identity)

    def _offer(self, parsed: tuple[dict, str]) -> None:
        """Add one parsed event while keeping queue and dedup state bounded."""
        fields, identity = parsed
        with self._lock:
            if self._known_identity_locked(identity):
                return
            self.received += 1
            source_ts = float(fields["ts"])
            seq = self._remember_pending_locked(source_ts, identity)
            if self._queue and merge_adjacent(self._queue[-1].fields, fields):
                tail = self._queue[-1]
                self._pending_recent[-1] = (seq, tail.run_id, source_ts, identity)
                self._queued_ids.discard(tail.identity)
                tail.identity = identity
                tail.latest_seq = seq
                self._queued_ids.add(identity)
                self.repeated += 1
                return
            self._pending_recent[-1] = (seq, seq, source_ts, identity)
            self._drop_oldest_locked()
            self._queue.append(_QueuedEvent(fields, identity, seq, seq))
            self._queued_ids.add(identity)

    def _known_identity_locked(self, identity: str) -> bool:
        return (
            identity in self._queued_ids
            or identity in self._pending_ids
            or identity in self._committed_ids
        )

    def _new_seq_locked(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    def _remember_pending_locked(self, source_ts: float, identity: str) -> int:
        seq = self._new_seq_locked()
        if len(self._pending_recent) == self._pending_recent.maxlen:
            _old_seq, _old_run, _old_ts, old_identity = self._pending_recent.popleft()
            self._pending_ids.discard(old_identity)
        # The first event's sequence is also its run ID. A caller that merges
        # it into the current tail rewrites this provisional ID below.
        run_id = self._queue[-1].run_id if self._queue else seq
        self._pending_recent.append((seq, run_id, source_ts, identity))
        self._pending_ids.add(identity)
        return seq

    def _drop_oldest_locked(self) -> None:
        if len(self._queue) != self._queue.maxlen:
            return
        item = self._queue.popleft()
        self._queued_ids.discard(item.identity)
        self.dropped += 1

    def _remember_committed_locked(self, source_ts: float, identity: str) -> None:
        if identity in self._committed_ids:
            return
        if len(self._committed) == self._committed.maxlen:
            _old_ts, old_identity = self._committed.popleft()
            self._committed_ids.discard(old_identity)
        self._committed.append((source_ts, identity))
        self._committed_ids.add(identity)

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        batch: list[dict] = []
        with self._lock:
            drained: list[_QueuedEvent] = []
            while self._queue and len(batch) < max_items:
                item = self._queue.popleft()
                self._queued_ids.discard(item.identity)
                drained.append(item)
                batch.append(item.fields)
                source_ts = float(
                    item.fields.get("attrs", {}).get("repeat_last_ts", item.fields["ts"])
                )
                self._watermark = max(self._watermark, source_ts)
            self._commit_drained_identities_locked(drained)
            cursor = _encode_checkpoint(self._watermark, self._committed) if batch else None
        # Constructing up to DRAIN_BUDGET records and encoding their strings
        # must not hold the producer lock during a unified-log burst.
        out = [EventRecord(ingest_ts=now, **fields) for fields in batch]
        return out, cursor

    def _commit_drained_identities_locked(self, drained: list[_QueuedEvent]) -> None:
        if not drained:
            return
        run_ids = {item.run_id for item in drained}
        candidates = [
            (seq, source_ts, identity)
            for seq, run_id, source_ts, identity in self._pending_recent
            if run_id in run_ids
        ]
        recent_ids = {identity for _seq, _ts, identity in candidates}
        for item in drained:
            if item.identity not in recent_ids:
                source_ts = float(
                    item.fields.get("attrs", {}).get("repeat_last_ts", item.fields["ts"])
                )
                candidates.append((item.latest_seq, source_ts, item.identity))
        for _seq, source_ts, identity in sorted(candidates):
            self._remember_committed_locked(source_ts, identity)

        self._pending_recent = collections.deque(
            (entry for entry in self._pending_recent if entry[1] not in run_ids),
            maxlen=IDENTITY_MAX,
        )
        self._pending_ids = {entry[3] for entry in self._pending_recent}

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def queue_capacity(self) -> int:
        return int(self._queue.maxlen or 0)

    def dedup_size(self) -> int:
        with self._lock:
            return len(self._queued_ids | self._pending_ids | self._committed_ids)

    def dedup_capacity(self) -> int:
        return (
            self.queue_capacity()
            + int(self._pending_recent.maxlen or 0)
            + int(self._committed.maxlen or 0)
        )

    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self) -> None:
        proc = self._proc
        self._stop_requested.set()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self._proc = None
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._thread = None


def _decode_checkpoint(cursor: str | None) -> tuple[float, list[tuple[float, str]]]:
    if not cursor:
        return 0.0, []
    try:
        raw = json.loads(cursor)
        watermark = float(raw["watermark"])
        identities = [
            (float(item[0]), str(item[1]))
            for item in raw.get("identities", [])
            if isinstance(item, list) and len(item) == 2
        ]
        return watermark, identities[-IDENTITY_MAX:]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0.0, []


def _encode_checkpoint(
    watermark: float, identities: collections.deque[tuple[float, str]]
) -> str:
    recent = [
        [ts, identity]
        for ts, identity in identities
        if ts >= watermark - REPLAY_OVERLAP_S
    ][-IDENTITY_MAX:]
    return json.dumps({"watermark": watermark, "identities": recent}, separators=(",", ":"))

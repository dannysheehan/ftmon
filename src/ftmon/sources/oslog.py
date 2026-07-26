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
from datetime import datetime
from typing import ClassVar

from ftmon.model import EventRecord, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

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
    provider = str(subsystem) if subsystem else "unknown"
    category = raw.get("category")
    if category:
        provider = f"{provider}/{category}"
    message_type = str(raw.get("messageType", "default")).lower()
    fields = {
        "ts": _timestamp(raw.get("timestamp")),
        "source": "oslog",
        "provider": provider,
        "event_id": None,
        "severity": MESSAGE_TYPE_TO_SEVERITY.get(message_type, 0),
        "message": message[:_MSG_MAX],
    }
    return fields, event_identity(raw, fields)


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

    def __init__(self, log_binary: str = "/usr/bin/log", predicate: str | None = None):
        self._log = log_binary
        self._predicate = predicate
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._queue: collections.deque[tuple[dict, str]] = collections.deque(maxlen=QUEUE_MAX)
        self._seen: set[str] = set()
        self._committed: collections.deque[tuple[float, str]] = collections.deque(
            maxlen=IDENTITY_MAX
        )
        self._lock = threading.Lock()
        self._watermark = 0.0
        self.dropped = 0
        self.malformed = 0

    def start(self, cursor: str | None) -> None:
        self.stop()
        watermark, identities = _decode_checkpoint(cursor)
        with self._lock:
            self._watermark = watermark
            self._committed = collections.deque(identities, maxlen=IDENTITY_MAX)
            self._seen = {identity for _, identity in identities}
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
        self._consume(self._args("stream"))

    def _args(self, operation: str) -> list[str]:
        args = [self._log, operation, "--style", "ndjson", "--level", "debug"]
        if self._predicate:
            args.extend(["--predicate", self._predicate])
        return args

    def _consume(self, args: list[str], replay_watermark: float = 0.0) -> bool:
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        self._proc = proc
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
            with self._lock:
                if identity in self._seen:
                    continue
                self._seen.add(identity)
                if len(self._queue) == self._queue.maxlen:
                    self.dropped += 1
                self._queue.append((fields, identity))
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
            if identity not in self._seen:
                self._seen.add(identity)
                self._queue.appendleft((fields, identity))

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        out: list[EventRecord] = []
        with self._lock:
            while self._queue and len(out) < max_items:
                fields, identity = self._queue.popleft()
                out.append(EventRecord(ingest_ts=now, **fields))
                source_ts = float(fields["ts"])
                self._watermark = max(self._watermark, source_ts)
                self._committed.append((source_ts, identity))
            cursor = _encode_checkpoint(self._watermark, self._committed) if out else None
        return out, cursor

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None


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

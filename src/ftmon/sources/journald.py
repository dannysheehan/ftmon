"""journald EventSource (SA-03, DM-07/08/15, SA-08).

Reads `journalctl -f -o json` as a supervised subprocess: journalctl is the
one interface that works identically for the user journal and (with group
membership) the system journal, needs no libsystemd binding, and hands us
the cursor for free. A reader thread moves stdout lines into a bounded
in-process queue; the daemon thread drains it each tick — the daemon never
blocks on the journal, and a journal burst cannot stall a tick (SA-08).

Parsing is a pure function (`parse_line`) so the DM-08 severity mapping and
malformed-line tolerance are unit-testable without journald anywhere near
the test (TS-02); only the subprocess plumbing needs a real system.

No clock reads (TS-03): ingest_ts comes in through drain(now, ...).
"""

from __future__ import annotations

import collections
import json
import subprocess
import threading
from typing import ClassVar

from ftmon.model import EventRecord, SourceDecl
from ftmon.sources.base import SOURCE_DECLS

__all__ = ["JournaldEventSource", "parse_line", "PRIORITY_TO_SEVERITY"]

QUEUE_MAX = 10_000  # SA-08
_MSG_MAX = 2048  # DM-13: event messages truncate at 2 KB

# DM-08: journald PRIORITY (syslog 0-7) -> ftmon severity 0-4.
# emerg/alert/crit are all "wake a human" -> critical; debug folds into info.
PRIORITY_TO_SEVERITY = {
    0: 4, 1: 4, 2: 4,  # emerg, alert, crit -> critical
    3: 3,              # err               -> error
    4: 2,              # warning           -> warning
    5: 1,              # notice            -> notice
    6: 0, 7: 0,        # info, debug       -> info
}


def parse_line(line: bytes) -> tuple[dict, str] | None:
    """One journalctl JSON line -> (record fields, cursor), or None if the
    line is malformed or unusable (caller counts it; never fatal, SA-08).

    Returned fields are everything an EventRecord needs except ingest_ts,
    which is stamped at drain time (ingest order is the rule-evaluation
    order, DM-15 — the source timestamp must not decide ordering).
    """
    try:
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    cursor = raw.get("__CURSOR")
    if not isinstance(cursor, str) or not cursor:
        return None  # a cursor-less entry cannot be resumed past; skip it

    message = raw.get("MESSAGE", "")
    if isinstance(message, list):  # journald encodes non-UTF-8 as byte arrays
        message = bytes(b for b in message if isinstance(b, int)).decode(
            "utf-8", errors="replace")
    elif not isinstance(message, str):
        message = str(message)

    try:
        priority = int(raw.get("PRIORITY", 6))
    except (TypeError, ValueError):
        priority = 6
    try:
        ts = int(raw.get("__REALTIME_TIMESTAMP", 0)) / 1e6  # microseconds
    except (TypeError, ValueError):
        ts = 0.0

    provider = raw.get("SYSLOG_IDENTIFIER") or raw.get("_SYSTEMD_UNIT") or "unknown"
    fields = {
        "ts": ts,
        "source": "journald",
        "provider": str(provider),
        "event_id": None,  # journald has no numeric event ids (PL-02)
        "severity": PRIORITY_TO_SEVERITY.get(priority, 0),
        "message": message[:_MSG_MAX],
    }
    return fields, cursor


class JournaldEventSource:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["events"]

    def __init__(self, journalctl: str = "journalctl"):
        self._journalctl = journalctl
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        # deque(maxlen=N) drops from the head on overflow — exactly SA-08's
        # "oldest are dropped"; the lock also guards the drop counter.
        self._queue: collections.deque[tuple[dict, str]] = collections.deque(
            maxlen=QUEUE_MAX)
        self._lock = threading.Lock()
        self.dropped = 0  # cumulative, read by the event engine for self-metrics
        self.malformed = 0

    def start(self, cursor: str | None) -> None:
        args = [self._journalctl, "-f", "-o", "json", "--no-pager"]
        if cursor:
            args.append(f"--after-cursor={cursor}")
        else:
            args.extend(["-n", "0"])  # DM-15: first run starts at now, no backfill
        self._proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(
            target=self._read_loop, args=(self._proc,), daemon=True,
            name="ftmon-journald",
        )
        self._thread.start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:  # ends when the process dies / is stopped
            parsed = parse_line(line)
            with self._lock:
                if parsed is None:
                    self.malformed += 1
                    continue
                if len(self._queue) == self._queue.maxlen:
                    self.dropped += 1  # deque is about to evict the oldest
                self._queue.append(parsed)

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        out: list[EventRecord] = []
        cursor: str | None = None
        with self._lock:
            while self._queue and len(out) < max_items:
                fields, cursor = self._queue.popleft()
                out.append(EventRecord(ingest_ts=now, **fields))
        return out, cursor

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

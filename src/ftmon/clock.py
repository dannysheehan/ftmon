"""Clock abstraction (TS-03). The ONLY module allowed to touch the time module.

SystemClock: production. ControlledClock: tier-1 e2e determinism (TS-05) -
a unix-socket server the test harness drives with line-JSON commands:

    {"op": "step", "s": 5}                  advance both clocks by s seconds
    {"op": "set", "wall": W, "mono": M}     absolute set (suspend simulation)

The daemon acks {"ok": true, "tick": N} when it next *enters* sleep_until,
i.e. after completing all work for the previous step - making harness steps
synchronous with tick completion.
"""

from __future__ import annotations

import json
import os
import socket
import time  # noqa: TID251  - permitted here only (TS-03)
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...  # wall, UTC epoch seconds
    def monotonic(self) -> float: ...
    def sleep_until(self, mono_deadline: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep_until(self, mono_deadline: float) -> None:
        while True:
            remaining = mono_deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))


class FakeClock:
    """In-process test clock; advance explicitly. Wall and mono can diverge
    to exercise SA-07 (suspend/NTP scenarios)."""

    def __init__(self, wall: float = 1_700_000_000.0, mono: float = 1000.0):
        self._wall = wall
        self._mono = mono

    def now(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def sleep_until(self, mono_deadline: float) -> None:
        if mono_deadline > self._mono:
            self._wall += mono_deadline - self._mono
            self._mono = mono_deadline

    def advance(self, seconds: float, wall_seconds: float | None = None) -> None:
        self._mono += seconds
        self._wall += seconds if wall_seconds is None else wall_seconds

    def set_wall(self, wall: float) -> None:
        self._wall = wall


class ControlledClock:
    """Socket-driven clock for the e2e harness (DESIGN section 5)."""

    def __init__(self, sock_path: str | None = None):
        path = sock_path or os.environ["FTMON_CLOCK_SOCK"]
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self._srv.bind(path)
        self._srv.listen(1)
        self._conn: socket.socket | None = None
        self._buf = b""
        self._wall = 1_700_000_000.0
        self._mono = 1000.0
        self._tick = 0

    def now(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def sleep_until(self, mono_deadline: float) -> None:
        self._ack()
        while self._mono < mono_deadline:
            cmd = self._read_command()
            if cmd["op"] == "step":
                s = float(cmd["s"])
                self._mono += s
                self._wall += float(cmd.get("wall_s", s))
            elif cmd["op"] == "set":
                self._wall = float(cmd["wall"])
                self._mono = float(cmd["mono"])

    def _ack(self) -> None:
        self._tick += 1
        if self._conn is not None:
            try:
                self._conn.sendall(
                    json.dumps({"ok": True, "tick": self._tick}).encode() + b"\n"
                )
            except OSError:
                self._conn = None

    def _read_command(self) -> dict:
        if self._conn is None:
            self._conn, _ = self._srv.accept()
            self._conn.sendall(
                json.dumps({"ok": True, "tick": self._tick}).encode() + b"\n"
            )
        while b"\n" not in self._buf:
            chunk = self._conn.recv(4096)
            if not chunk:
                self._conn = None
                return self._read_command()
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

"""[TS-05] Tier-1 e2e harness: the real `ftmon daemon` binary as a
subprocess, deterministic via ControlledClock over a unix socket.

Why a subprocess and not DaemonCore-in-process (which test_fixtures already
covers): TS-05 exists to test what only a real process can — argv/env
wiring, the single-instance lock, signal handling, and above all crash
behavior (SIGKILL mid-cycle, NO-04). Time is the harness's: the daemon's
clock only moves when step() says so, making every run bit-identical.

Protocol (DESIGN section 5 / clock.py): the daemon acks {"ok":true,"tick":N}
each time it *enters* sleep_until — i.e. after finishing all work for the
previous step — so step() returning means the tick fully completed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ftmon.paths import get_paths


class DaemonHarness:
    def __init__(self, root: Path, monitor_defs: dict[str, str], fixtures: str):
        self.root = root
        self.fixtures = fixtures
        # AF_UNIX paths are limited to ~108 bytes; pytest tmp paths routinely
        # exceed that, so the socket lives in a short mkdtemp dir instead.
        self._sockdir = tempfile.mkdtemp(prefix="ftmon-e2e-")
        self.sock_path = os.path.join(self._sockdir, "clock.sock")
        self.env = {
            **os.environ,
            "FTMON_CONFIG_DIR": str(root / "cfg"),
            "FTMON_DATA_DIR": str(root / "data"),
            "FTMON_STATE_DIR": str(root / "state"),
            "FTMON_RUNTIME_DIR": str(root / "run"),
            "FTMON_CLOCK_SOCK": self.sock_path,
        }
        self.paths = get_paths(self.env)
        self.paths.ensure()
        for name, text in monitor_defs.items():
            (self.paths.monitors_dir / f"{name}.toml").write_text(text)
        self.proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._buf = b""
        self.log = root / "daemon.log"

    def start(self) -> None:
        log = open(self.log, "ab")  # appended across restarts: one crash story
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ftmon", "daemon",
             "--clock", "controlled", "--fixtures", self.fixtures],
            env=self.env, stdout=log, stderr=log,
        )
        log.close()
        self._connect()

    def _connect(self) -> None:
        deadline = time.monotonic() + 20.0
        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                s.close()
                assert self.proc and self.proc.poll() is None, (
                    f"daemon exited rc={self.proc.returncode}; log:\n"
                    + self.log.read_text()
                )
                assert time.monotonic() < deadline, "daemon never bound clock socket"
                time.sleep(0.05)
        s.settimeout(15.0)
        self._sock = s
        self._buf = b""
        self._recv_ack()  # greeting sent on accept

    def step(self, s: float = 5.0) -> int:
        """Advance sim time by s seconds; returns the daemon's tick counter
        after the resulting work completed."""
        assert self._sock is not None
        self._sock.sendall(json.dumps({"op": "step", "s": s}).encode() + b"\n")
        return self._recv_ack()

    def step_until(self, predicate, max_steps: int, s: float = 5.0) -> int:
        """Step until predicate() is true. Returns steps taken; asserts the
        budget wasn't exhausted (a hung condition should fail loudly, with
        the daemon log, not time out silently)."""
        for i in range(max_steps):
            if predicate():
                return i
            self.step(s)
        assert predicate(), (
            f"condition not reached in {max_steps} steps; log:\n" + self.log.read_text()
        )
        return max_steps

    def _recv_ack(self) -> int:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            assert chunk, "daemon closed clock socket; log:\n" + self.log.read_text()
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        ack = json.loads(line)
        assert ack.get("ok") is True
        return int(ack["tick"])

    def notifications(self) -> list[dict]:
        f = self.paths.notifications_file
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines()]

    def kill9(self) -> None:
        """SIGKILL — no cleanup, no atexit, mid-whatever-it-was-doing."""
        assert self.proc is not None
        self.proc.kill()
        self.proc.wait(timeout=10)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def stop(self) -> None:
        """Graceful-if-possible teardown. A controlled-clock daemon blocks in
        recv() between steps, so SIGTERM only takes effect at the next
        sleep_until entry — nudge it with one step (no ack: the loop exits
        before acking), then escalate to SIGKILL if it still won't die."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                if self._sock is not None:
                    self._sock.sendall(b'{"op": "step", "s": 5}\n')
            except OSError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

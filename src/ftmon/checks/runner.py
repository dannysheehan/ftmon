"""No-shell, process-group-bounded external check runner (EC-02)."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading
from pathlib import Path

from ftmon.checks import jsoncheck, nagios
from ftmon.checks.model import CheckSpec, RawCheckResult, unknown
from ftmon.clock import Clock, SystemClock

_STDOUT_LIMIT = 64 * 1024
_STDERR_LIMIT = 8 * 1024


def _read_bounded(stream: object, limit: int, output: bytearray, overflow: list[bool]) -> None:
    while True:
        chunk = stream.read(8192)  # type: ignore[attr-defined]
        if not chunk:
            return
        remaining = limit - len(output)
        output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            overflow[0] = True


def trusted_executable(executable: str) -> bool:
    """Reject symlinks, non-regular files, and group/world-writable targets."""
    path = Path(executable)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_info = resolved.lstat()
    except (OSError, RuntimeError):
        return False
    return (
        path.is_absolute()
        and not stat.S_ISLNK(info.st_mode)
        and stat.S_ISREG(info.st_mode)
        and resolved == path
        and resolved_info.st_uid in {0, os.geteuid()}
        and not resolved_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and bool(resolved_info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    )


class CheckRunner:
    def __init__(self, state_dir: Path, clock: Clock | None = None):
        self._state_dir = state_dir
        self._clock = clock or SystemClock()

    def run(self, spec: CheckSpec, deadline_mono: float) -> RawCheckResult:
        started = self._clock.monotonic()
        if not spec.argv or not trusted_executable(spec.argv[0]):
            return unknown(0.0, "executable")
        timeout = min(spec.timeout_s, max(0.0, deadline_mono - started))
        if timeout <= 0:
            return unknown(0.0, "timeout")
        env = {
            "PATH": os.defpath,
            "FTMON_CHECK_ALIAS": spec.alias,
            "FTMON_CHECK_TIMEOUT": str(spec.timeout_s),
        }
        try:
            process = subprocess.Popen(
                spec.argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._state_dir,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            return unknown(self._clock.monotonic() - started, "launch")
        stdout, stderr = bytearray(), bytearray()
        stdout_overflow, stderr_overflow = [False], [False]
        readers = [
            threading.Thread(
                target=_read_bounded,
                args=(process.stdout, _STDOUT_LIMIT, stdout, stdout_overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded,
                args=(process.stderr, _STDERR_LIMIT, stderr, stderr_overflow),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for reader in readers:
            reader.join()
        duration = max(0.0, self._clock.monotonic() - started)
        if timed_out:
            return unknown(duration, "timeout")
        if stdout_overflow[0]:
            return unknown(duration, "output_limit")
        if process.returncode is None or process.returncode < 0:
            return unknown(duration, "signal")
        if spec.protocol == "nagios":
            return nagios.parse(bytes(stdout), process.returncode, duration)
        if spec.protocol == "ftmon-json":
            if process.returncode != 0:
                return unknown(duration, "exit_status")
            return jsoncheck.parse(bytes(stdout), duration)
        return unknown(duration, "protocol")

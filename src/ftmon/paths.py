"""All filesystem paths (FS-01) and the atomic-write helper (PM-06).

Nothing else in ftmon may construct config/data/state paths. Tests override
via FTMON_* environment variables (set before first get_paths() call).

Also the PL-01 home for the other OS-identity primitives that don't warrant
their own seam module: which platform is running (`current_platform`) and
the PM-02 single-instance lock (`try_lock_exclusive`), both platform-specific
but not one of the four named seams.
"""

from __future__ import annotations

import os
import platform
import signal
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import platformdirs

_APP = "ftmon"

# PM-11 reload-equivalent on Windows: no SIGHUP there, so daemon.py and
# cli.py rendezvous through this well-known named Event instead. Session-
# local ("Local\") since ftmon is single-user; spiked and confirmed on
# feature/windows-support (spikes/windows-support/reload_signal_spike.py).
_RELOAD_EVENT_PREFIX = "Local\\ftmon-reload-signal-"


def _reload_event_name(pid: int) -> str:
    return f"{_RELOAD_EVENT_PREFIX}{pid}"


def current_platform() -> str:
    """`platform.system()` ("Linux"/"Windows"/"Darwin") lowercased to exactly
    the `monitor.platforms` vocabulary (PL-02, schema.PLATFORMS)."""
    return platform.system().lower()


_WIN_LOCK_BYTE = 1 << 20  # see try_lock_exclusive: kept clear of the pid text


def set_private_permissions(path: Path, mode: int) -> None:
    """Apply PM-06/SE-04 private permissions using the host's real model.

    Windows ``chmod`` only toggles the read-only attribute, so relying on it
    leaves files and directories with whatever broad DACL they inherited.
    A protected owner/SYSTEM/Administrators DACL is the Windows equivalent of
    the 0600/0700 contract and requires no elevation for user-owned paths.
    """
    if os.name != "nt":
        os.chmod(path, mode)
        return

    import ntsecuritycon
    import win32security

    owner_sid = win32security.GetFileSecurity(
        str(path), win32security.OWNER_SECURITY_INFORMATION
    ).GetSecurityDescriptorOwner()
    trusted_sids = (
        owner_sid,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid),
    )
    inheritance = 0
    if path.is_dir():
        inheritance = (
            win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        )
    dacl = win32security.ACL()
    for sid in trusted_sids:
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inheritance,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )


def external_process_group_options() -> dict[str, object]:
    """Return host-specific ``Popen`` options for a separately killable tree."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_external_process(process: subprocess.Popen) -> None:
    """Stop a timed-out external process and its descendants (EC-02)."""
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        subprocess.run(
            [
                str(system_root / "System32" / "taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process.wait()
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def replace_with_readonly_file(source: Path, target: Path) -> None:
    """Atomically install *source* and leave the destination read-only.

    Windows refuses to replace an existing file carrying its read-only
    attribute. Temporarily clearing that owner-controlled attribute preserves
    the old contents until ``os.replace`` succeeds; it is restored if the
    replacement fails. POSIX can replace a read-only destination directly.
    """
    restore_target = False
    if os.name == "nt" and target.exists():
        restore_target = not bool(target.stat().st_mode & stat.S_IWRITE)
        if restore_target:
            os.chmod(target, stat.S_IWRITE)
    try:
        os.replace(source, target)
    except BaseException:
        if restore_target:
            os.chmod(target, 0o444)
        raise
    os.chmod(target, 0o444)


def fsync_directory(path: Path) -> None:
    """Persist directory metadata where the host exposes that operation."""
    if os.name == "nt":
        # Windows does not expose POSIX directory fsync through Python; the
        # same-volume os.replace above remains atomic.
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

def try_lock_exclusive(file) -> bool:
    """PM-02 single-instance lock, platform seam: True if this process now
    holds the exclusive advisory lock, False if another process holds it.
    `fcntl`/`msvcrt` are imported locally so this module stays importable on
    every platform regardless of which branch runs (PL-01).

    Windows byte-range locking is mandatory, not advisory like POSIX flock —
    a locked region blocks *reads* of that same range from other handles,
    not just competing lock attempts (found while wiring CL-07's rescan
    through this: cli.py's read of the pid text raised PermissionError when
    it shared byte 0 with the lock). Locking a fixed high offset instead of
    byte 0 keeps the pid text freely readable by every caller regardless of
    who holds the lock; the seek back to 0 after (successful or not) means
    callers who write/read from the file start don't need to know this."""
    if os.name == "nt":
        import msvcrt

        file.seek(_WIN_LOCK_BYTE)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        finally:
            file.seek(0)
    else:
        import fcntl

        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def start_reload_watcher(on_reload: Callable[[], None]) -> None:
    """PM-11 reload-equivalent, platform seam: POSIX gets this for free from
    `signal.SIGHUP` (registered in daemon.py::run()), so this is a no-op
    there. Windows has no per-process signal delivery, so a background
    thread polls the named Event instead, calling `on_reload` exactly the
    way the SIGHUP handler calls `core.request_reload` — same "handler only
    records the request, no I/O" contract PM-11 requires, just via a poll
    loop instead of async delivery."""
    if os.name != "nt":
        return
    import threading

    import win32event

    handle = win32event.CreateEvent(None, False, False, _reload_event_name(os.getpid()))

    def _watch() -> None:
        while True:
            if win32event.WaitForSingleObject(handle, 1000) == win32event.WAIT_OBJECT_0:
                on_reload()

    threading.Thread(target=_watch, daemon=True, name="ftmon-reload-watch").start()


def signal_reload(pid: int) -> None:
    """CL-07 cross-process reload signal, platform seam. POSIX: SIGHUP the
    pid (unchanged behavior). Windows: `pid` is unused -- there is no
    per-process signal delivery there -- instead the well-known named Event
    from `start_reload_watcher` is set. Raises `ProcessLookupError` if that
    event doesn't exist (no daemon running with the watcher, or it predates
    this feature) so callers built around POSIX's `os.kill` exceptions (e.g.
    cli.py::_monitor_rescan's existing `except (ProcessLookupError,
    PermissionError)`) need no platform-specific handling of their own."""
    if os.name == "nt":
        import pywintypes
        import win32event

        try:
            handle = win32event.OpenEvent(
                win32event.EVENT_MODIFY_STATE, False, _reload_event_name(pid)
            )
        except pywintypes.error as exc:
            raise ProcessLookupError(str(exc)) from exc
        win32event.SetEvent(handle)
        return
    import signal

    os.kill(pid, signal.SIGHUP)


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    monitors_dir: Path
    drafts_dir: Path
    actions_dir: Path
    config_file: Path
    check_registry_file: Path
    data_dir: Path
    db_file: Path
    state_dir: Path
    log_file: Path
    notifications_file: Path
    runtime_dir: Path
    lock_file: Path

    def ensure(self) -> None:
        """Create all directories with 0700 (FS-02, SE-04)."""
        for d in (
            self.config_dir,
            self.monitors_dir,
            self.drafts_dir,
            self.actions_dir,
            self.data_dir,
            self.state_dir,
            self.runtime_dir,
        ):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            set_private_permissions(d, 0o700)


def get_paths(env: dict[str, str] | None = None) -> Paths:
    e = os.environ if env is None else env
    config = Path(e.get("FTMON_CONFIG_DIR", platformdirs.user_config_dir(_APP)))
    data = Path(e.get("FTMON_DATA_DIR", platformdirs.user_data_dir(_APP)))
    state = Path(e.get("FTMON_STATE_DIR", platformdirs.user_state_dir(_APP)))
    runtime = Path(
        e.get("FTMON_RUNTIME_DIR", platformdirs.user_runtime_dir(_APP))
    )
    return Paths(
        config_dir=config,
        monitors_dir=config / "monitors",
        drafts_dir=config / "monitors" / "drafts",
        actions_dir=config / "actions",
        config_file=config / "config.toml",
        check_registry_file=Path(
            e.get("FTMON_CHECK_REGISTRY", str(config / "checks.toml"))
        ),
        data_dir=data,
        db_file=data / "ftmon.db",
        state_dir=state,
        log_file=state / "daemon.log",
        notifications_file=state / "notifications.jsonl",
        runtime_dir=runtime,
        lock_file=runtime / "daemon.lock",
    )


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """PM-06(a/b): tmp file in same dir + fsync + rename; 0600 by default."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    set_private_permissions(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        if hasattr(os, "fchmod"):  # POSIX only; Windows has no bit mode to set
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        set_private_permissions(Path(tmp), mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def reject_symlink(path: Path) -> None:
    """PM-06(c): definition files must not be symlinks."""
    if path.is_symlink():
        raise OSError(f"symlinked definition file rejected: {path}")

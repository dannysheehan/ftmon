"""All filesystem paths (FS-01) and the atomic-write helper (PM-06).

Nothing else in ftmon may construct config/data/state paths. Tests override
via FTMON_* environment variables (set before first get_paths() call).

Also the PL-01 home for the other OS-identity primitives that don't warrant
their own seam module: which platform is running (`current_platform`) and
the PM-02 single-instance lock (`try_lock_exclusive`), both platform-specific
but not one of the four named seams.
"""

from __future__ import annotations

import errno
import os
import platform
import signal
import socket
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import platformdirs

_APP = "ftmon"

# PM-11 reload-equivalent on Windows: no SIGHUP there, so daemon.py and
# cli.py rendezvous through this well-known named Event instead. Scheduled
# Tasks and interactive/SSH clients can occupy different Terminal Services
# sessions, so the event must use the cross-session Global namespace. Its
# access remains governed by the daemon token's default DACL; the system-wide
# PID suffix prevents concurrent-user name collisions.
_RELOAD_EVENT_PREFIX = "Global\\ftmon-reload-signal-"


def _reload_event_name(pid: int) -> str:
    return f"{_RELOAD_EVENT_PREFIX}{pid}"


def current_platform() -> str:
    """`platform.system()` ("Linux"/"Windows"/"Darwin") lowercased to exactly
    the `monitor.platforms` vocabulary (PL-02, schema.PLATFORMS)."""
    return platform.system().lower()


@dataclass(frozen=True)
class ControlledClockEndpoint:
    """Platform-selected TS-05 endpoint consumed by clock and test harness."""

    transport: str
    address: str | tuple[str, int]
    cleanup_path: Path | None


def controlled_clock_endpoint(
    env: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> ControlledClockEndpoint:
    """Resolve the test-only clock transport behind the PL-01 paths seam."""
    selected_env = os.environ if env is None else env
    host = current_platform() if platform_name is None else platform_name
    if host == "windows":
        port = int(selected_env["FTMON_CLOCK_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("FTMON_CLOCK_PORT must be between 1 and 65535")
        return ControlledClockEndpoint("tcp", ("127.0.0.1", port), None)
    path = Path(selected_env["FTMON_CLOCK_SOCK"])
    return ControlledClockEndpoint("unix", str(path), path)


def open_controlled_clock_socket(endpoint: ControlledClockEndpoint) -> socket.socket:
    """Create a socket for a platform-selected controlled-clock endpoint."""
    if endpoint.transport == "tcp":
        result = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return result
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


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
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
    )
    owner_sid, _attrs = win32security.GetTokenInformation(
        token, win32security.TokenUser
    )
    trusted_sids = (
        owner_sid,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid),
    )
    access = ntsecuritycon.READ_CONTROL | ntsecuritycon.WRITE_DAC
    handle, attributes = _open_windows_path_nofollow(path, access)
    try:
        existing_owner_sid = win32security.GetSecurityInfo(
            handle,
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION,
        ).GetSecurityDescriptorOwner()
    finally:
        handle.Close()

    # WRITE_OWNER is not implied merely by owning an object. Request it only
    # when the legacy behavior genuinely needs to replace a foreign owner,
    # then re-open/revalidate so no pathname race enters the mutation.
    if existing_owner_sid != owner_sid:
        access |= ntsecuritycon.WRITE_OWNER
    handle, attributes = _open_windows_path_nofollow(path, access)
    try:
        verified_owner_sid = win32security.GetSecurityInfo(
            handle,
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION,
        ).GetSecurityDescriptorOwner()
        if verified_owner_sid != existing_owner_sid:
            raise OSError(errno.ESTALE, "managed path changed during permission setup", str(path))
        inheritance = 0
        if attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
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
        security_information = (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        )
        replacement_owner = None
        if existing_owner_sid != owner_sid:
            replacement_owner = owner_sid
            security_information |= win32security.OWNER_SECURITY_INFORMATION
        win32security.SetSecurityInfo(
            handle,
            win32security.SE_FILE_OBJECT,
            security_information,
            replacement_owner,
            None,
            dacl,
            None,
        )
    finally:
        handle.Close()


def _open_windows_path_nofollow(path: Path, desired_access: int):
    """Open a file/directory itself and reject every final reparse point."""
    import win32con
    import win32file

    handle = win32file.CreateFile(
        str(path),
        desired_access,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
        None,
        win32file.OPEN_EXISTING,
        win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    try:
        attributes = win32file.GetFileInformationByHandle(handle)[0]
        if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to modify a reparse point", str(path))
        return handle, attributes
    except BaseException:
        handle.Close()
        raise


def open_readonly_nofollow(path: Path) -> int:
    """Open a read-only file handle without traversing a final symlink."""
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags)

    import msvcrt

    import pywintypes
    import win32con
    import win32file

    try:
        handle = win32file.CreateFile(
            str(path),
            win32file.GENERIC_READ,
            win32file.FILE_SHARE_READ,
            None,
            win32file.OPEN_EXISTING,
            win32file.FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    except pywintypes.error as exc:
        # Keep the paths seam's documented OSError contract even when the
        # native API refuses a directory/junction before attributes can be
        # inspected (commonly ERROR_ACCESS_DENIED for a directory handle).
        winerror = getattr(exc, "winerror", exc.args[0] if exc.args else errno.EACCES)
        raise OSError(
            winerror, "cannot open path without following reparse point", str(path)
        ) from exc
    try:
        attributes = win32file.GetFileInformationByHandle(handle)[0]
        if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to follow a reparse point", str(path))
        return msvcrt.open_osfhandle(handle.Detach(), os.O_RDONLY)
    except BaseException:
        handle.Close()
        raise


def external_process_group_options() -> dict[str, object]:
    """Return host-specific ``Popen`` options for a separately killable tree."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def external_check_environment(
    alias: str,
    timeout_s: float,
    environ: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> dict[str, str]:
    """Build the intentionally small environment for an external check.

    Windows runtimes commonly require a handful of OS-root/temp variables
    even when launched by absolute path. Preserve only that explicit set;
    arbitrary daemon environment does not cross the check boundary (EC-02).
    """
    source = os.environ if environ is None else environ
    host = current_platform() if platform_name is None else platform_name
    result = {
        "PATH": os.defpath,
        "FTMON_CHECK_ALIAS": alias,
        "FTMON_CHECK_TIMEOUT": str(timeout_s),
    }
    if host != "windows":
        return result

    # Environment keys are case-insensitive on Windows, but an injected test
    # mapping need not be. Emit stable canonical spellings to child processes.
    folded = {key.casefold(): value for key, value in source.items()}
    essentials = (
        ("SystemRoot", "systemroot"),
        ("SystemDrive", "systemdrive"),
        ("windir", "windir"),
        ("TEMP", "temp"),
        ("TMP", "tmp"),
        ("PATHEXT", "pathext"),
    )
    for output_key, folded_key in essentials:
        value = folded.get(folded_key)
        if value:
            result[output_key] = value
    # SystemRoot is the one documented fallback: taskkill and core Windows
    # runtime discovery both depend on it even in a stripped service account.
    result.setdefault("SystemRoot", r"C:\Windows")
    return result


def terminate_external_process(process: subprocess.Popen) -> None:
    """Stop a timed-out external process and its descendants (EC-02)."""
    if os.name == "nt":
        _terminate_windows_process(process)
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _terminate_windows_process(process: subprocess.Popen) -> None:
    """Best-effort tree termination without letting a stuck child stall a tick."""
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        # taskkill is an optimization for descendants. Popen.kill remains
        # the bounded fallback for the direct child if taskkill is absent,
        # denied, or itself stalls.
        pass
    try:
        process.wait(timeout=0.25)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        # Nothing stronger is available through Popen; return control to the
        # daemon rather than turning an external-check timeout into a stall.
        pass


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
    thread polls the cross-session named Event instead, calling `on_reload`
    exactly the way the SIGHUP handler calls `core.request_reload` — same
    "handler only records the request, no I/O" contract PM-11 requires, just
    via a poll loop instead of async delivery."""
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
    pid (unchanged behavior). Windows has no per-process signal delivery, so
    the pid selects the global named Event from `start_reload_watcher` instead.
    Raises `ProcessLookupError` if that event doesn't exist (no daemon running
    with the watcher, or it predates this feature) so callers built around
    POSIX's `os.kill` exceptions (e.g. cli.py::_monitor_rescan's existing
    `except (ProcessLookupError, PermissionError)`) need no platform-specific
    handling of their own."""
    if os.name == "nt":
        import pywintypes
        import win32api
        import win32event

        try:
            handle = win32event.OpenEvent(
                win32event.EVENT_MODIFY_STATE, False, _reload_event_name(pid)
            )
        except pywintypes.error as exc:
            raise ProcessLookupError(str(exc)) from exc
        try:
            win32event.SetEvent(handle)
        finally:
            win32api.CloseHandle(handle)
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

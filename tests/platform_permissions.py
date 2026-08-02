"""Cross-platform permission fixtures using each host's real security model."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ftmon.paths import set_private_permissions


def make_private(path: Path, mode: int) -> Path:
    """Make a fixture genuinely owner-private on POSIX or Windows."""
    set_private_permissions(path, mode)
    return path


def toml_path(path: Path | str) -> str:
    """Render an absolute path without TOML basic-string backslash escapes."""
    return Path(path).as_posix()


def assert_private(path: Path, mode: int) -> None:
    """Assert octal mode on POSIX and the equivalent protected DACL on Windows."""
    info = path.stat()
    if os.name != "nt":
        assert stat.S_IMODE(info.st_mode) == mode
        return
    from ftmon.checks.trust import accessible_beyond_owner, owned_by_self

    assert owned_by_self(path, info)
    assert not accessible_beyond_owner(path, info)


def make_broadly_writable(path: Path, posix_mode: int) -> Path:
    """Grant Everyone write on Windows, matching group/other write on POSIX."""
    if os.name != "nt":
        path.chmod(posix_mode)
        return path

    import ntsecuritycon
    import win32security

    owner_sid = win32security.GetFileSecurity(
        str(path), win32security.OWNER_SECURITY_INFORMATION
    ).GetSecurityDescriptorOwner()
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    admins_sid = win32security.CreateWellKnownSid(
        win32security.WinBuiltinAdministratorsSid
    )
    everyone_sid = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    inheritance = 0
    if path.is_dir():
        inheritance = (
            win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        )
    dacl = win32security.ACL()
    for sid in (owner_sid, system_sid, admins_sid):
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inheritance,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        inheritance,
        ntsecuritycon.FILE_GENERIC_WRITE,
        everyone_sid,
    )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )
    return path

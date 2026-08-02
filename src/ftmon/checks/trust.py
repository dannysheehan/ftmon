"""Shared file/executable trust policy (EC-01 check registry/runner; also
backs config.py's SE-04 secret-file check and web/demo_app.py's SE-06
demo-database check -- same ownership/writability shape, one evaluator so
none of them can quietly diverge).

Registry validation and the external-check runner must apply one ownership and
path contract; diverging copies would let a trusted load race an untrusted run.
NoNewPrivileges also masks distro plugin ownership to overflow uids (nobody).

Windows has no POSIX uid/mode bits (os.stat().st_uid is always 0 there, and
st_mode's write bits are a fixed synthesized value, not real permissions --
confirmed empirically on feature/windows-support, see NOTES.md) -- SE-07's
"owned by the service user ... or root" / "not writable by group or other"
become owner-SID and DACL checks instead, behind the same os.name=="nt"
seam style as paths.py::try_lock_exclusive. The masked_system_executable
NoNewPrivileges escape hatch is POSIX-only by design (a narrow systemd
sandboxing workaround, not a general "system binaries are trusted" rule) and
is not given a Windows equivalent.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_OVERFLOW_UIDS = frozenset({65533, 65534})  # nfsnobody / nobody when ownership is masked
_SYSTEM_EXECUTABLE_PREFIXES = ("/bin/", "/lib/", "/sbin/", "/usr/")


def masked_system_executable(path: Path, info: os.stat_result) -> bool:
    """NoNewPrivileges can report distro executables with the overflow uid."""
    if info.st_uid not in _OVERFLOW_UIDS:
        return False
    return str(path.resolve()).startswith(_SYSTEM_EXECUTABLE_PREFIXES)


def _win_root_equivalent_sids() -> frozenset[str]:
    """SE-07's "root": SYSTEM and Administrators, the closest Windows
    analogues to a POSIX uid-0 owner."""
    import win32security

    return frozenset({
        win32security.ConvertSidToStringSid(
            win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
        ),
        win32security.ConvertSidToStringSid(
            win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)
        ),
    })


def _win_current_user_sid() -> str:
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    user_sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
    return win32security.ConvertSidToStringSid(user_sid)


def _win_owner_sid(path: Path) -> str | None:
    import pywintypes
    import win32security

    try:
        sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
    except pywintypes.error:
        return None
    return win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())


def _win_trusted_owner(path: Path) -> bool:
    """SE-07: owner SID is the current process's user, or SYSTEM/Administrators."""
    owner = _win_owner_sid(path)
    if owner is None:
        return False
    return owner in _win_root_equivalent_sids() or owner == _win_current_user_sid()


def _win_write_mask() -> int:
    import ntsecuritycon

    return (
        ntsecuritycon.FILE_WRITE_DATA
        | ntsecuritycon.FILE_APPEND_DATA
        | ntsecuritycon.WRITE_DAC
        | ntsecuritycon.WRITE_OWNER
        | ntsecuritycon.GENERIC_WRITE
    )


def _win_grants_beyond_owner(path: Path, mask_filter: int | None) -> bool:
    """Shared DACL walk behind writable_beyond_owner and the stricter
    accessible_beyond_owner: True if any ACCESS_ALLOWED ACE (masked by
    mask_filter, or any non-zero mask when mask_filter is None) grants
    access to a trustee beyond the owner/SYSTEM/Administrators. Unreadable
    ACL fails closed (True), same posture as an unreadable stat elsewhere in
    this module."""
    import pywintypes
    import win32security

    try:
        sd = win32security.GetFileSecurity(
            str(path),
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
    except pywintypes.error:
        return True
    owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
    trusted = _win_root_equivalent_sids() | {owner}
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        # A missing or NULL DACL grants full access to everyone; it is the
        # opposite of an empty DACL. Treat it as broadly accessible for both
        # executable and credential-file trust decisions.
        return True
    for i in range(dacl.GetAceCount()):
        (ace_type, _flags), mask, sid = dacl.GetAce(i)
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            continue
        if mask_filter is not None and not (mask & mask_filter):
            continue
        if win32security.ConvertSidToStringSid(sid) not in trusted:
            return True
    return False


def writable_beyond_owner(path: Path, info: os.stat_result) -> bool:
    """True if the file is writable by more than its owner/root. Shared by
    trust_failures and checks/registry.py's own validation so write-authority
    can't diverge between load-time and run-time checks -- this module's
    whole reason to exist, per the module docstring. Also reused by
    web/demo_app.py's demo-database check, same predicate."""
    if os.name == "nt":
        return _win_grants_beyond_owner(path, _win_write_mask())
    return bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def accessible_beyond_owner(path: Path, info: os.stat_result) -> bool:
    """Stricter than writable_beyond_owner: True if any principal beyond the
    owner/root has *any* access at all, not just write -- config.py's SE-04
    secret-credential-file posture (no group/world read either). Windows:
    SYSTEM/Administrators are still exempted -- unlike POSIX root they
    cannot be excluded from a file's ACL by construction (default NTFS
    inheritance always grants them access), so treating their presence as a
    violation would reject every ordinary Windows file; the
    security-relevant question this answers is "can another *user account*
    reach this," which the exemption preserves."""
    if os.name == "nt":
        return _win_grants_beyond_owner(path, None)
    return bool(stat.S_IMODE(info.st_mode) & 0o077)


def owned_by_self(path: Path, info: os.stat_result) -> bool:
    """Stricter than trusted_owner: True only if owned by exactly the
    current identity -- no root/SYSTEM/Administrators exception, since SE-04
    secrets have no "or root" clause the way EC-01/SE-07 checks do."""
    if os.name == "nt":
        owner = _win_owner_sid(path)
        return owner is not None and owner == _win_current_user_sid()
    return info.st_uid == os.geteuid()


def trusted_owner(path: Path, info: os.stat_result, *, system_executable: bool = False) -> bool:
    if os.name == "nt":
        if _win_trusted_owner(path):
            return True
        return system_executable and masked_system_executable(path, info)
    uid = info.st_uid
    # SE-07: trust against the executing identity, not only the real uid.
    if uid in {0, os.geteuid()}:
        return True
    return system_executable and masked_system_executable(path, info)


def trust_failures(executable: str) -> list[str]:
    """Every failed trust condition, by name (CL-08 diagnostics).

    Single evaluator behind trusted_executable_path(): a separate explain
    path would inevitably diverge from the enforcement path, which is the
    exact failure mode this module exists to prevent.
    """
    path = Path(executable)
    if not path.is_absolute():
        return [f"not_absolute: {executable!r} must be an absolute path"]
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_info = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        return [f"unreadable: cannot stat {executable!r} ({exc.__class__.__name__})"]
    failures: list[str] = []
    if stat.S_ISLNK(info.st_mode):
        failures.append("symlink: the executable itself must not be a symlink")
    elif not stat.S_ISREG(info.st_mode):
        failures.append("not_regular_file: must be a regular file")
    if resolved != path and not stat.S_ISLNK(info.st_mode):
        failures.append(
            f"symlinked_parent: path traverses a symlink (resolves to {resolved})"
        )
    if not trusted_owner(resolved, resolved_info, system_executable=True):
        if os.name == "nt":
            failures.append(
                f"untrusted_owner: {resolved} is not owned by the current user, "
                "SYSTEM, or Administrators"
            )
        else:
            failures.append(
                f"untrusted_owner: uid {resolved_info.st_uid} is neither root nor "
                f"the executing uid {os.geteuid()}"
            )
    if writable_beyond_owner(resolved, resolved_info):
        detail = (
            "an ACL grants write access beyond the owner/SYSTEM/Administrators"
            if os.name == "nt"
            else f"mode {stat.filemode(resolved_info.st_mode)}"
        )
        failures.append(f"group_or_other_writable: {detail}")
    if not resolved_info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        failures.append(f"not_executable: mode {stat.filemode(resolved_info.st_mode)}")
    return failures


def trusted_executable_path(executable: str) -> bool:
    """Reject symlinks, non-regular files, and untrusted ownership."""
    return not trust_failures(executable)

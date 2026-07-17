"""Shared executable trust policy for registry load and check execution (EC-01).

Registry validation and the external-check runner must apply one ownership and
path contract; diverging copies would let a trusted load race an untrusted run.
NoNewPrivileges also masks distro plugin ownership to overflow uids (nobody).
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


def trusted_owner(path: Path, info: os.stat_result, *, system_executable: bool = False) -> bool:
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
        failures.append(
            f"untrusted_owner: uid {resolved_info.st_uid} is neither root nor "
            f"the executing uid {os.geteuid()}"
        )
    if resolved_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        failures.append(
            f"group_or_other_writable: mode {stat.filemode(resolved_info.st_mode)}"
        )
    if not resolved_info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        failures.append(f"not_executable: mode {stat.filemode(resolved_info.st_mode)}")
    return failures


def trusted_executable_path(executable: str) -> bool:
    """Reject symlinks, non-regular files, and untrusted ownership."""
    return not trust_failures(executable)

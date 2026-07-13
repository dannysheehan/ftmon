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
    if uid in {0, os.getuid()}:
        return True
    return system_executable and masked_system_executable(path, info)


def trusted_executable_path(executable: str) -> bool:
    """Reject symlinks, non-regular files, and untrusted ownership."""
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
        and trusted_owner(resolved, resolved_info, system_executable=True)
        and not resolved_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and bool(resolved_info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    )

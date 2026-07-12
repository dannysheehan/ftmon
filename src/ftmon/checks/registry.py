"""Load the administrator-owned external-check authority (EC-01/06/07)."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ftmon.checks.model import CheckSpec
from ftmon.definitions.schema import valid_name
from ftmon.expr import ExprSyntaxError, parse_duration
from ftmon.paths import Paths

MAX_CHECKS = 64
MAX_ARGS = 32
MAX_ARG_BYTES = 512
MAX_ARGV_BYTES = 8192
PROTOCOLS = frozenset({"ftmon-json", "nagios"})


class RegistryError(ValueError):
    """A stable, redacted registry failure suitable for self-events."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class CheckRegistry(Mapping[str, CheckSpec]):
    """Immutable registry published only after every entry validates."""

    _entries: Mapping[str, CheckSpec]

    def __getitem__(self, alias: str) -> CheckSpec:
        return self._entries[alias]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def empty() -> CheckRegistry:
    """Return an immutable no-authority registry for missing/invalid setup."""
    return CheckRegistry(MappingProxyType({}))


_OVERFLOW_UIDS = frozenset({65533, 65534})  # nfsnobody / nobody when ownership is masked
_SYSTEM_EXECUTABLE_PREFIXES = ("/bin/", "/lib/", "/sbin/", "/usr/")


def _masked_system_executable(path: Path, info: os.stat_result) -> bool:
    """NoNewPrivileges can report distro executables with the overflow uid."""
    if info.st_uid not in _OVERFLOW_UIDS:
        return False
    resolved = str(path.resolve())
    return resolved.startswith(_SYSTEM_EXECUTABLE_PREFIXES)


def _trusted_owner(path: Path, info: os.stat_result, *, executable: bool = False) -> bool:
    if info.st_uid in {0, os.getuid()}:
        return True
    return executable and _masked_system_executable(path, info)


def _regular_protected(path: Path, category: str, *, executable: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RegistryError(category) from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise RegistryError(category)
    if not _trusted_owner(path, info, executable=executable) or info.st_mode & 0o022:
        raise RegistryError(category)
    return info


def _validate_registry_file(path: Path) -> None:
    _regular_protected(path, "registry_untrusted")
    # The selected registry's directory is the trust root: checking above it
    # would incorrectly reject safe user registries merely because /tmp or a
    # shared home mount is writable outside FTMON's authority boundary.
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise RegistryError("registry_untrusted") from exc
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o022:
        raise RegistryError("registry_untrusted")


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _timeout(value: object) -> float:
    if value is None:
        return 10.0
    if not isinstance(value, str):
        raise RegistryError("invalid_timeout")
    try:
        seconds = parse_duration(value)
    except ExprSyntaxError as exc:
        raise RegistryError("invalid_timeout") from exc
    if not 1.0 <= seconds <= 30.0:
        raise RegistryError("invalid_timeout")
    return seconds


def _entry(alias: object, value: object, paths: Paths | None) -> CheckSpec:
    if not valid_name(alias):
        raise RegistryError("invalid_alias")
    if not isinstance(value, dict) or set(value) - {"argv", "protocol", "timeout"}:
        raise RegistryError("invalid_entry")
    argv = value.get("argv")
    protocol = value.get("protocol")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= MAX_ARGS
        or not all(isinstance(arg, str) and arg for arg in argv)
    ):
        raise RegistryError("invalid_argv")
    encoded = [arg.encode("utf-8") for arg in argv]
    if any(len(arg) > MAX_ARG_BYTES for arg in encoded) or sum(map(len, encoded)) > MAX_ARGV_BYTES:
        raise RegistryError("invalid_argv")
    executable = Path(argv[0])
    if not executable.is_absolute():
        raise RegistryError("invalid_executable")
    if paths is not None:
        resolved = executable.resolve(strict=False)
        forbidden_roots = (paths.data_dir, paths.state_dir, paths.runtime_dir)
        if any(_under(resolved, root.resolve()) for root in forbidden_roots):
            raise RegistryError("invalid_executable")
    info = _regular_protected(executable, "executable_unready", executable=True)
    if not info.st_mode & 0o111:
        raise RegistryError("executable_unready")
    if protocol not in PROTOCOLS:
        raise RegistryError("invalid_protocol")
    return CheckSpec(alias, tuple(argv), protocol, _timeout(value.get("timeout")))


def load(path: Path, *, paths: Paths | None = None) -> CheckRegistry:
    """Validate and return a complete immutable registry.

    Callers retain their previous object when this raises, which makes reload
    publication atomic without this loader owning daemon lifecycle state.
    """
    _validate_registry_file(path)
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError("invalid_toml") from exc
    if set(document) != {"check"} or not isinstance(document["check"], dict):
        raise RegistryError("invalid_schema")
    checks = document["check"]
    if len(checks) > MAX_CHECKS:
        raise RegistryError("too_many_checks")
    entries = {alias: _entry(alias, value, paths) for alias, value in checks.items()}
    return CheckRegistry(MappingProxyType(entries))

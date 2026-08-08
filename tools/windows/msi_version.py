"""PEP 440 → MSI ProductVersion mapping for the Windows installer (issue #95).

MSI versions are ``major.minor.build`` with:
  build = patch * 1000 + channel
  alpha N → N
  beta N → 200 + N
  rc N → 400 + N
  final → 800
  post N → 800 + N

Examples: ``2.0.0a15`` → ``2.0.15``; ``2.0.0`` → ``2.0.800``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

_CHANNEL_ALPHA = 0
_CHANNEL_BETA = 200
_CHANNEL_RC = 400
_CHANNEL_FINAL = 800


@dataclass(frozen=True)
class MappedWindowsVersion:
    """Paired PEP 440 (authoritative) and MSI/file versions."""

    pep440: str
    msi: str  # major.minor.build
    file_version: tuple[int, int, int, int]  # VERSIONINFO numeric


def map_pep440_to_msi(pep440: str) -> MappedWindowsVersion:
    """Translate a PEP 440 version into a monotonically increasing MSI version."""
    try:
        ver = Version(pep440)
    except InvalidVersion as exc:
        raise ValueError(f"unsupported version (not PEP 440): {pep440!r}") from exc

    if ver.epoch:
        raise ValueError(f"unsupported version (epoch not allowed): {pep440!r}")
    if ver.dev is not None:
        raise ValueError(f"unsupported version (dev releases rejected): {pep440!r}")
    if ver.local is not None:
        raise ValueError(f"unsupported version (local label rejected): {pep440!r}")

    release = list(ver.release) + [0, 0, 0]
    major, minor, patch = release[0], release[1], release[2]
    if any(part < 0 for part in (major, minor, patch)):
        raise ValueError(f"unsupported version (negative component): {pep440!r}")

    if ver.is_prerelease:
        assert ver.pre is not None
        tag, num = ver.pre
        if num > 199:
            raise ValueError(f"unsupported version (prerelease number > 199): {pep440!r}")
        if tag == "a":
            channel = _CHANNEL_ALPHA + num
        elif tag == "b":
            channel = _CHANNEL_BETA + num
        elif tag == "rc":
            channel = _CHANNEL_RC + num
        else:
            raise ValueError(f"unsupported prerelease tag {tag!r} in {pep440!r}")
        if ver.post is not None:
            raise ValueError(f"unsupported version (prerelease+post rejected): {pep440!r}")
    elif ver.post is not None:
        if ver.post > 199:
            raise ValueError(f"unsupported version (post number > 199): {pep440!r}")
        channel = _CHANNEL_FINAL + ver.post
    else:
        channel = _CHANNEL_FINAL

    build = patch * 1000 + channel
    # Windows Installer ProductVersion: major and minor are 0..255; build is
    # 0..65535 (https://learn.microsoft.com/windows/win32/msi/productversion).
    if major > 255 or minor > 255:
        raise ValueError(
            f"unsupported version (major/minor must be <= 255): {pep440!r}"
        )
    if build > 65535:
        raise ValueError(
            f"unsupported version (build {build} exceeds MSI 16-bit limit): {pep440!r}"
        )

    msi = f"{major}.{minor}.{build}"
    # File version uses the same three fields plus a zero revision.
    file_version = (major, minor, build, 0)
    return MappedWindowsVersion(pep440=str(ver), msi=msi, file_version=file_version)


_PEP440_RE = re.compile(r"^[0-9].*")


def assert_versions_agree(*, tag: str | None, package: str, frozen: str, msi: str) -> None:
    """Release-gate helper: tag/package/frozen/MSI must describe one release."""
    mapped = map_pep440_to_msi(package)
    if tag is not None:
        expected_tag = f"v{package}"
        if tag != expected_tag:
            raise ValueError(f"tag {tag!r} != package version {expected_tag!r}")
    if frozen != package:
        raise ValueError(f"frozen executable version {frozen!r} != package {package!r}")
    if msi != mapped.msi:
        raise ValueError(f"MSI version {msi!r} != mapped {mapped.msi!r} for {package!r}")

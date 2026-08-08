"""Parse ``ftmon --version`` / MSI ProductVersion and agree release versions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ftmon import __version__  # noqa: E402
from tools.windows.msi_version import assert_versions_agree, map_pep440_to_msi  # noqa: E402

_VERSION_RE = re.compile(r"(?i)\bftmon\s+(\S+)|^\s*(\d+\S*)\s*$")


def parse_ftmon_version(text: str) -> str:
    """Extract the PEP 440 version from ``ftmon --version`` stdout."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _VERSION_RE.search(line)
        if match:
            return match.group(1) or match.group(2)
    raise ValueError(f"could not parse ftmon version from {text!r}")


def frozen_version(exe: Path) -> str:
    completed = subprocess.run(
        [str(exe), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_ftmon_version(completed.stdout or completed.stderr)


def read_msi_product_version(msi: Path) -> str:
    """Read ProductVersion from a built MSI via WindowsInstaller COM."""
    if not msi.is_file():
        raise FileNotFoundError(msi)
    ps = f"""
$ErrorActionPreference = 'Stop'
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $installer.OpenDatabase('{str(msi).replace("'", "''")}', 0)
$view = $db.OpenView("SELECT `Value` FROM `Property` WHERE `Property`='ProductVersion'")
$view.Execute() | Out-Null
$record = $view.Fetch()
if (-not $record) {{ throw 'ProductVersion property missing' }}
$record.StringData(1)
"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = (completed.stdout or "").strip()
    if not value:
        raise ValueError(f"empty ProductVersion from {msi}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exe",
        type=Path,
        default=ROOT / "dist" / "windows" / "ftmon" / "ftmon.exe",
        help="frozen ftmon.exe to query",
    )
    parser.add_argument(
        "--msi",
        type=Path,
        default=None,
        help="built MSI whose ProductVersion is inspected (required)",
    )
    parser.add_argument("--tag", default=None, help="optional git tag (vX.Y.Z)")
    args = parser.parse_args(argv)
    if not args.exe.is_file():
        raise SystemExit(f"frozen executable not found: {args.exe}")
    if args.msi is None:
        raise SystemExit("--msi is required so ProductVersion is read from the built package")
    if not args.msi.is_file():
        raise SystemExit(f"MSI not found: {args.msi}")

    frozen = frozen_version(args.exe)
    msi_version = read_msi_product_version(args.msi)
    mapped = map_pep440_to_msi(__version__)
    assert_versions_agree(
        tag=args.tag,
        package=__version__,
        frozen=frozen,
        msi=msi_version,
    )
    print(
        f"version agreement OK tag={args.tag!r} package={__version__} "
        f"frozen={frozen} msi_file={msi_version} mapped={mapped.msi}",
        flush=True,
    )
    # Emit machine-readable summary for CI logs.
    print(json.dumps({"package": __version__, "frozen": frozen, "msi": msi_version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

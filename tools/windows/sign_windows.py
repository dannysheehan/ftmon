"""Optional Authenticode signing for ftmon.exe and the MSI (issue #95).

Signing stays conditional until certificate / Azure Trusted Signing credentials
are configured. Never prints secrets. Prereleases may ship unsigned.

Called from ``build_windows.py`` after freeze (exe) and after MSI construction.
Checksums are written only after both signing steps.

    uv run python tools/windows/sign_windows.py dist/windows/ftmon/ftmon.exe
    uv run python tools/windows/sign_windows.py dist/windows/ftmon-*-windows-x64.msi

Environment:
    FTMON_SIGN_CERT_SHA1   certificate SHA1 thumbprint (signtool /sha1)
    FTMON_SIGN_TIMESTAMP   RFC 3161 timestamp URL (default DigiCert)
    FTMON_SIGN_SKIP=1      no-op success (packaging PR CI without credentials;
                           release leaves this unset so a configured cert signs)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def _signtool() -> str:
    found = shutil.which("signtool")
    if found:
        return found
    # Common Windows SDK location when not on PATH.
    pf = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    matches = sorted(pf.glob(r"Windows Kits\10\bin\*\x64\signtool.exe"), reverse=True)
    if matches:
        return str(matches[0])
    raise SystemExit("signtool.exe not found; install the Windows SDK or set PATH")


def sign(path: Path) -> None:
    if os.environ.get("FTMON_SIGN_SKIP") == "1":
        print(f"FTMON_SIGN_SKIP=1 — not signing {path}")
        return
    thumbprint = os.environ.get("FTMON_SIGN_CERT_SHA1", "").strip()
    if not thumbprint:
        print(
            "FTMON_SIGN_CERT_SHA1 unset — leaving unsigned "
            "(expected for prereleases without Trusted Signing credentials)"
        )
        return
    # GitHub Actions supplies unset secrets as "" — treat blank like unset.
    _default_ts = "http://timestamp.digicert.com"
    timestamp = (os.environ.get("FTMON_SIGN_TIMESTAMP") or "").strip() or _default_ts
    tool = _signtool()
    cmd = [
        tool,
        "sign",
        "/fd",
        "SHA256",
        "/td",
        "SHA256",
        "/tr",
        timestamp,
        "/sha1",
        thumbprint,
        str(path),
    ]
    print("+", " ".join(cmd[:6]), "...", path.name)
    subprocess.run(cmd, check=True)
    verify = [tool, "verify", "/pa", str(path)]
    print("+", " ".join(verify))
    subprocess.run(verify, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for path in args.paths:
        if not path.is_file():
            raise SystemExit(f"not a file: {path}")
        sign(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

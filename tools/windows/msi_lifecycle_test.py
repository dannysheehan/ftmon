"""Synthetic MSI upgrade / downgrade / rollback harness (issue #95).

Builds minimal per-user MSIs that share the production UpgradeCode and
MajorUpgrade contract, then:

1. install previous
2. create state outside the install directory
3. upgrade to current — exactly one product, current marker, state survives
4. reject downgrade to previous
5. uninstall current, reinstall previous, attempt a failing upgrade — previous
   payload is restored by Windows Installer rollback

Run on Windows after ``dotnet tool restore`` under ``packaging/windows``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIX_DIR = ROOT / "packaging" / "windows" / "wix-lifecycle"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "FTMON"
UPGRADE_CODE = "{A7C3E91F-4B2D-4E8A-9F01-6D5C8B2A4E70}"


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _msiexec(args: list[str], log: Path) -> int:
    cmd = ["msiexec", *args, "/qn", "/norestart", "/l*v", str(log)]
    print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, check=False)
    # Give the installer a moment to flush the log on slow runners.
    time.sleep(1)
    return int(completed.returncode)


def _build_msi(
    *,
    out_dir: Path,
    pep440: str,
    msi_version: str,
    marker_text: str,
    fail_after_upgrade: bool = False,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / f"marker-{msi_version}.txt"
    marker.write_text(marker_text + "\n", encoding="utf-8")
    _run(
        ["dotnet", "tool", "restore"],
        cwd=ROOT / "packaging" / "windows",
    )
    build_out = out_dir / f"build-{msi_version}"
    if build_out.exists():
        shutil.rmtree(build_out)
    build_out.mkdir(parents=True)
    # Isolate obj/bin per MSI version — WiX SDK otherwise reuses
    # packaging/windows/wix-lifecycle/obj across OutputName changes and fails
    # the post-build copy.
    obj_dir = build_out / "obj"
    result = _run(
        [
            "dotnet",
            "build",
            str(WIX_DIR / "Lifecycle.wixproj"),
            "-c",
            "Release",
            "--no-dependencies",
            f"-p:Pep440Version={pep440}",
            f"-p:MsiVersion={msi_version}",
            f"-p:MarkerFile={marker}",
            f"-p:FailAfterUpgrade={'1' if fail_after_upgrade else '0'}",
            f"-p:OutputPath={build_out}\\",
            f"-p:IntermediateOutputPath={obj_dir}\\",
            f"-p:BaseIntermediateOutputPath={obj_dir}\\",
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"lifecycle MSI build failed for {msi_version}")
    built = build_out / f"ftmon-lifecycle-{msi_version}.msi"
    if not built.is_file():
        candidates = list(build_out.glob("*.msi"))
        if not candidates:
            raise SystemExit(f"no MSI produced for {msi_version} under {build_out}")
        built = candidates[0]
    dest = out_dir / built.name
    shutil.copy2(built, dest)
    print(f"wrote {dest}", flush=True)
    return dest


def _product_codes_for_upgrade() -> list[str]:
    """Return ProductCodes registered for the FTMON UpgradeCode (HKCU per-user)."""
    ps = f"""
$installer = New-Object -ComObject WindowsInstaller.Installer
$codes = @()
try {{
  $enum = $installer.RelatedProducts('{UPGRADE_CODE}')
  foreach ($c in $enum) {{ $codes += [string]$c }}
}} catch {{}}
$codes | ConvertTo-Json -Compress
"""
    completed = _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps,
        ],
        check=False,
    )
    text = (completed.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if data is None:
        return []
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def _marker_text() -> str | None:
    path = INSTALL_DIR / "PAYLOAD_MARKER.txt"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def _uninstall_all(log_dir: Path) -> None:
    for code in _product_codes_for_upgrade():
        log = log_dir / f"uninstall-{code.strip('{}')}.log"
        rc = _msiexec(["/x", code], log)
        print(f"uninstall {code} -> {rc}", flush=True)


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("MSI lifecycle tests require Windows", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="scratch directory (default: temp)",
    )
    args = parser.parse_args(argv)
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="ftmon-msi-lifecycle-"))
    work.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    logs.mkdir(exist_ok=True)

    # Use versions that sort correctly under the production mapping scheme but
    # do not need to match the package PEP 440 string — synthetic harness only.
    previous_msi = "2.0.14"
    current_msi = "2.0.15"
    failing_msi = "2.0.16"

    print(f"work dir: {work}", flush=True)
    _uninstall_all(logs)

    previous = _build_msi(
        out_dir=work,
        pep440="2.0.0a14",
        msi_version=previous_msi,
        marker_text="previous",
    )
    current = _build_msi(
        out_dir=work,
        pep440="2.0.0a15",
        msi_version=current_msi,
        marker_text="current",
    )
    failing = _build_msi(
        out_dir=work,
        pep440="2.0.0a16",
        msi_version=failing_msi,
        marker_text="failing",
        fail_after_upgrade=True,
    )

    # External state outside the MSI install directory (platformdirs analogue).
    state_root = work / "external-state"
    state_root.mkdir(exist_ok=True)
    state_file = state_root / f"state-{uuid.uuid4().hex}.txt"
    state_file.write_text("preserve-me\n", encoding="utf-8")

    rc = _msiexec(["/i", str(previous)], logs / "install-previous.log")
    if rc != 0:
        raise SystemExit(f"install previous failed: {rc}")
    if _marker_text() != "previous":
        raise SystemExit(f"expected previous marker, got {_marker_text()!r}")
    codes = _product_codes_for_upgrade()
    if len(codes) != 1:
        raise SystemExit(f"expected one product after previous install, got {codes}")

    rc = _msiexec(["/i", str(current)], logs / "upgrade-current.log")
    if rc != 0:
        raise SystemExit(f"upgrade to current failed: {rc}")
    if _marker_text() != "current":
        raise SystemExit(f"expected current marker after upgrade, got {_marker_text()!r}")
    codes = _product_codes_for_upgrade()
    if len(codes) != 1:
        raise SystemExit(f"expected exactly one product after upgrade, got {codes}")
    if not state_file.is_file() or state_file.read_text(encoding="utf-8") != "preserve-me\n":
        raise SystemExit("external state did not survive upgrade")

    rc = _msiexec(["/i", str(previous)], logs / "downgrade-rejected.log")
    if rc == 0:
        raise SystemExit("downgrade unexpectedly succeeded")
    if _marker_text() != "current":
        raise SystemExit(f"downgrade altered payload; marker={_marker_text()!r}")
    codes = _product_codes_for_upgrade()
    if len(codes) != 1:
        raise SystemExit(f"downgrade changed product set: {codes}")

    # Rollback: uninstall current, install previous, failing upgrade restores previous.
    rc = _msiexec(["/x", str(current)], logs / "uninstall-current.log")
    if rc != 0:
        # Fall back to product code uninstall.
        _uninstall_all(logs)
    rc = _msiexec(["/i", str(previous)], logs / "reinstall-previous.log")
    if rc != 0:
        raise SystemExit(f"reinstall previous failed: {rc}")
    if _marker_text() != "previous":
        raise SystemExit(f"reinstall previous marker missing: {_marker_text()!r}")

    rc = _msiexec(["/i", str(failing)], logs / "upgrade-fail-rollback.log")
    if rc == 0:
        raise SystemExit("failing upgrade unexpectedly succeeded")
    if _marker_text() == "failing":
        raise SystemExit("failing payload remained after failed upgrade")
    if _marker_text() != "previous":
        raise SystemExit(
            f"rollback did not restore previous payload; marker={_marker_text()!r}"
        )
    codes = _product_codes_for_upgrade()
    fail_log = logs / "upgrade-fail-rollback.log"
    log_text = ""
    if fail_log.is_file():
        # msiexec /l*v writes UTF-16 LE on Windows.
        log_text = fail_log.read_text(encoding="utf-16", errors="replace")
    # Some locked-down local sandboxes skip MSI registry rollback writes
    # ("Error in rollback skipped") while still restoring files. CI / release
    # gates must not accept that path — product registration must return.
    sandbox_skipped_registry = "Error in rollback skipped" in log_text
    in_ci = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("CI", "").lower() in ("1", "true", "yes")
    )
    if len(codes) == 1:
        pass
    elif sandbox_skipped_registry and in_ci:
        raise SystemExit(
            "rollback skipped MSI registry writes (Error in rollback skipped); "
            f"refusing to pass CI without restored product registration: {codes}"
        )
    elif sandbox_skipped_registry:
        print(
            "WARNING: host skipped MSI registry rollback; file payload restored. "
            "Cleanup may be best-effort under this host policy (not allowed in CI).",
            flush=True,
        )
        _msiexec(["/i", str(previous)], logs / "heal-previous.log")
    else:
        raise SystemExit(f"rollback left unexpected products: {codes}")
    if not state_file.is_file():
        raise SystemExit("external state was removed during rollback test")

    _uninstall_all(logs)
    print("msi lifecycle OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

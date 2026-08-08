"""Build the Windows onedir ZIP and per-user MSI (issue #95).

Run on Windows x64 with the repository uv environment:

    uv sync --group windows-packaging
    uv run python tools/windows/build_windows.py

Optional flags:
    --skip-msi     freeze + zip only
    --skip-freeze  reuse an existing dist/windows/ftmon onedir
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ftmon import __version__  # noqa: E402
from tools.windows.msi_version import map_pep440_to_msi  # noqa: E402
from tools.windows.sign_windows import sign  # noqa: E402


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd or ROOT, check=True, env=env)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_checksums(artifacts: list[Path], out: Path) -> None:
    lines = [f"{_sha256(p)}  {p.name}" for p in artifacts]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def freeze() -> Path:
    versions = tomllib.loads(
        (ROOT / "packaging" / "windows" / "versions.toml").read_text(encoding="utf-8")
    )
    mapped = map_pep440_to_msi(__version__)
    print(
        f"freeze pins: pep440={__version__} msi={mapped.msi} "
        f"python={versions['python']} pyinstaller={versions['pyinstaller']} "
        f"wix={versions['wix']}",
        flush=True,
    )
    _run([sys.executable, str(ROOT / "tools" / "windows" / "gen_file_version_info.py")])
    _run([sys.executable, str(ROOT / "tools" / "windows" / "gen_notices.py")])

    dist_root = ROOT / "dist" / "windows"
    work = ROOT / "build" / "pyinstaller"
    if dist_root.exists():
        shutil.rmtree(dist_root)
    if work.exists():
        shutil.rmtree(work)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            f"--distpath={dist_root}",
            f"--workpath={work}",
            str(ROOT / "packaging" / "windows" / "ftmon.spec"),
        ]
    )
    onedir = dist_root / "ftmon"
    if not (onedir / "ftmon.exe").is_file():
        raise SystemExit(f"missing frozen executable: {onedir / 'ftmon.exe'}")
    # Prefer notices scanned from the frozen onedir (transitive .dist-info).
    _run(
        [
            sys.executable,
            str(ROOT / "tools" / "windows" / "gen_notices.py"),
            "--from-onedir",
            str(onedir),
        ]
    )
    # Ensure helpers land at the install root even if Analysis placed them under _internal.
    for name in (
        "Install-FTMONTasks.ps1",
        "Invoke-FTMONTask.ps1",
        "LICENSE",
        "THIRD_PARTY_NOTICES.txt",
    ):
        src_candidates = [
            onedir / name,
            onedir / "_internal" / name,
            ROOT / "src" / "ftmon" / "windows" / name,
            ROOT / "LICENSE" if name == "LICENSE" else ROOT / "packaging" / "windows" / name,
        ]
        dest = onedir / name
        if dest.is_file():
            continue
        for cand in src_candidates:
            if cand.is_file():
                shutil.copy2(cand, dest)
                break
        if not dest.is_file():
            raise SystemExit(f"missing required payload file: {name}")
    return onedir


def zip_onedir(onedir: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(onedir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path("ftmon") / path.relative_to(onedir)))
    print(f"wrote {dest}", flush=True)


def build_msi(onedir: Path, dest: Path, mapped_msi: str) -> None:
    # Ensure local .NET WiX tool is available.
    _run(
        ["dotnet", "tool", "restore"],
        cwd=ROOT / "packaging" / "windows",
    )
    wix_dir = ROOT / "packaging" / "windows" / "wix"
    out_dir = ROOT / "dist" / "windows" / "msi"
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "dotnet",
            "build",
            str(wix_dir / "Ftmon.wixproj"),
            "-c",
            "Release",
            f"-p:Pep440Version={__version__}",
            f"-p:MsiVersion={mapped_msi}",
            f"-p:PayloadDir={onedir}",
            f"-p:OutputPath={out_dir}\\",
        ]
    )
    built = out_dir / f"ftmon-{__version__}-windows-x64.msi"
    if not built.is_file():
        # SDK may emit without the custom OutputName in some configurations.
        candidates = list(out_dir.glob("*.msi"))
        if not candidates:
            raise SystemExit(f"MSI not produced under {out_dir}")
        built = candidates[0]
    if dest.exists():
        dest.unlink()
    shutil.copy2(built, dest)
    print(f"wrote {dest}", flush=True)


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("Windows packaging must run on Windows x64", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-msi", action="store_true")
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args(argv)

    mapped = map_pep440_to_msi(__version__)
    dist = ROOT / "dist" / "windows"
    dist.mkdir(parents=True, exist_ok=True)

    if args.skip_freeze:
        onedir = dist / "ftmon"
        if not (onedir / "ftmon.exe").is_file():
            raise SystemExit(f"--skip-freeze requires {onedir / 'ftmon.exe'}")
    else:
        onedir = freeze()

    # Sign the frozen executable before ZIP/MSI so both artifacts embed it.
    sign(onedir / "ftmon.exe")

    zip_path = dist / f"ftmon-{__version__}-windows-x64.zip"
    zip_onedir(onedir, zip_path)
    artifacts = [zip_path]

    if not args.skip_msi:
        msi_path = dist / f"ftmon-{__version__}-windows-x64.msi"
        build_msi(onedir, msi_path, mapped.msi)
        sign(msi_path)
        artifacts.append(msi_path)

    # Checksums only after every artifact (including signatures) is final.
    sums = dist / "SHA256SUMS.txt"
    _write_checksums(artifacts, sums)
    print(f"wrote {sums}", flush=True)
    print(f"OK pep440={__version__} msi={mapped.msi}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for FTMON Windows x64 (issue #95).

Console mode stays enabled: CLI and MCP use stdout/stdin. Task Scheduler hides
the window through Invoke-FTMONTask.ps1 rather than a GUI-subsystem binary.
UPX is disabled. Built-in TOML, migrations, web assets, MCP guides, Windows
helpers, and pywin32/windows-toasts runtime bits are collected explicitly.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
VERSIONS = tomllib.loads((ROOT / "packaging" / "windows" / "versions.toml").read_text(encoding="utf-8"))

# Import after path setup so Analysis finds the package.
import sys

sys.path.insert(0, str(SRC))
from ftmon import __version__ as FTMON_VERSION  # noqa: E402
from tools.windows.msi_version import map_pep440_to_msi  # noqa: E402

mapped = map_pep440_to_msi(FTMON_VERSION)

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

# Package data + submodules that importlib.resources / PackageLoader need.
for pkg in (
    "ftmon",
    "ftmon.definitions",
    "ftmon.web",
    "ftmon.scenarios",
    "ftmon.store",
    "mcp",
    "uvicorn",
    "starlette",
    "jinja2",
    "windows_toasts",
    "win32timezone",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# pywin32 runtime DLLs and extensions.
binaries += collect_dynamic_libs("pywin32")
datas += collect_data_files("pywin32")
hiddenimports += [
    "win32api",
    "win32con",
    "win32event",
    "win32file",
    "win32security",
    "win32ts",
    "win32evtlog",
    "pywintypes",
    "pythoncom",
    "ntsecuritycon",
    "win32timezone",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "mcp.server.fastmcp",
]

# Task helpers, licence, and generated notices live beside ftmon.exe in COLLECT.
helper_datas = [
    (str(ROOT / "src" / "ftmon" / "windows" / "Install-FTMONTasks.ps1"), "."),
    (str(ROOT / "src" / "ftmon" / "windows" / "Invoke-FTMONTask.ps1"), "."),
    (str(ROOT / "LICENSE"), "."),
]
notices = ROOT / "packaging" / "windows" / "THIRD_PARTY_NOTICES.txt"
if notices.is_file():
    helper_datas.append((str(notices), "."))

block_cipher = None

a = Analysis(
    [str(SRC / "ftmon" / "__main__.py")],
    pathex=[str(SRC), str(ROOT)],
    binaries=binaries,
    datas=datas + helper_datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "windows" / "hooks")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "windows" / "hooks" / "pyi_rth_ftmon.py")],
    excludes=[
        "extra_monitors",
        "pytest",
        "hypothesis",
        "ruff",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ftmon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "packaging" / "windows" / "file_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ftmon",
)

# Echo pins for CI logs (PyInstaller evaluates the spec as Python).
print(
    f"ftmon-windows-freeze: pep440={FTMON_VERSION} msi={mapped.msi} "
    f"python_pin={VERSIONS['python']} pyinstaller={VERSIONS['pyinstaller']}"
)

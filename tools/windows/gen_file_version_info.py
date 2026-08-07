"""Generate packaging/windows/file_version_info.txt from ftmon.__version__."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ftmon import __version__  # noqa: E402
from tools.windows.msi_version import map_pep440_to_msi  # noqa: E402

TEMPLATE = """\
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={filevers},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'Danny Sheehan'),
            StringStruct(u'FileDescription', u'FTMON systems monitor'),
            StringStruct(u'FileVersion', u'{pep440}'),
            StringStruct(u'InternalName', u'ftmon'),
            StringStruct(u'LegalCopyright', u'Copyright (c) Danny Sheehan'),
            StringStruct(u'OriginalFilename', u'ftmon.exe'),
            StringStruct(u'ProductName', u'FTMON'),
            StringStruct(u'ProductVersion', u'{pep440}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    mapped = map_pep440_to_msi(__version__)
    out = ROOT / "packaging" / "windows" / "file_version_info.txt"
    out.write_text(
        TEMPLATE.format(filevers=mapped.file_version, pep440=mapped.pep440),
        encoding="utf-8",
    )
    print(f"wrote {out} ({mapped.pep440} -> filevers={mapped.file_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Monitor definition schema and loader (DESIGN.md section 7, MD-01..10).

Public API re-exported from `loader.py`:

    load_text(text, filename="<text>") -> MonitorDef      raises ValidationError
    load_file(path) -> MonitorDef                          rejects symlinks (PM-06c)
    load_dir(monitors_dir) -> (list[MonitorDef], list[(Path, ValidationError)])

`schema.py` holds the declarative key inventory; `builtins/*.toml` are the
eight normative built-in monitor definitions (MD-07), shipped as package data.
"""

from ftmon.definitions.loader import (
    MonitorDef,
    RuleDef,
    TrendProfile,
    ValidationError,
    load_dir,
    load_file,
    load_text,
)

__all__ = [
    "MonitorDef",
    "RuleDef",
    "TrendProfile",
    "ValidationError",
    "load_text",
    "load_file",
    "load_dir",
]

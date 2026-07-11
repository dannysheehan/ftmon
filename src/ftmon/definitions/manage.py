"""Monitor lifecycle operations shared by CLI, MCP, and (M5) web (MD-05,
PM-06).

These are the ONLY code paths that write into the config tree besides
`ftmon init` — the CLI approve command and the MCP define_monitor tool call
these same functions, which is what makes PM-06's coordination rules
enforceable at all (UI-03 requires the web UI to reuse them too).

Every operation re-validates before touching monitors/: the config tree is
user-editable at any moment, so trust nothing that was validated earlier
(PM-06e: last-write-wins is fine, a stale validation result is not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ftmon.definitions import loader
from ftmon.paths import Paths, atomic_write, reject_symlink

__all__ = ["ManageError", "write_draft", "approve_draft", "delete_draft", "set_enabled"]


@dataclass(frozen=True)
class ManageError(Exception):
    """Structured failure (MC-04 shape, shared with the CLI)."""

    code: str  # invalid_params | validation_failed | not_found | name_exists
    message: str
    hint: str = ""
    errors: tuple = ()  # loader ValidationError entries when validation failed

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _validate_text(toml_text: str) -> loader.MonitorDef:
    try:
        return loader.load_text(toml_text)
    except loader.ValidationError as e:
        raise ManageError(
            code="validation_failed",
            message=f"{len(e.errors)} validation error(s)",
            hint="fix the listed errors; see the ftmon://docs/definitions resource",
            errors=tuple(e.errors),
        ) from e


def write_draft(paths: Paths, toml_text: str) -> Path:
    """define_monitor's write half (PM-06a/b): validate, then land in
    drafts/ atomically. Overwriting an existing *draft* is allowed — that is
    the normal authoring loop (MC-03) — but a name that exists as a real
    monitor is refused: silently shadowing an enabled monitor is exactly the
    authority the AI must not have."""
    mdef = _validate_text(toml_text)
    target = paths.monitors_dir / f"{mdef.name}.toml"
    if target.exists():
        raise ManageError(
            code="name_exists",
            message=f"monitor {mdef.name!r} already exists at {target}",
            hint="pick a new name, or edit the existing monitor's file directly",
        )
    draft = paths.drafts_dir / f"{mdef.name}.toml"
    paths.drafts_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(draft, toml_text.encode())
    return draft


def approve_draft(paths: Paths, name: str) -> Path:
    """PM-06(d): re-validate the draft, then rename() into monitors/ —
    atomic on the same filesystem, and it FAILS if the target appeared in
    the meantime (the approval race in TS-05) rather than clobbering it."""
    draft = paths.drafts_dir / f"{name}.toml"
    if not draft.exists():
        raise ManageError(
            code="not_found",
            message=f"no draft named {name!r}",
            hint="ftmon monitors lists drafts; define_monitor creates them",
        )
    reject_symlink(draft)  # PM-06c
    _validate_text(draft.read_text())
    target = paths.monitors_dir / f"{name}.toml"
    if target.exists():
        raise ManageError(
            code="name_exists",
            message=f"{target} already exists; approval would overwrite it",
            hint="remove or rename the existing monitor first",
        )
    draft.rename(target)
    return target


def delete_draft(paths: Paths, name: str) -> None:
    """Delete only an unapproved draft (UI-03, PM-06c).

    The name is deliberately constrained before constructing the path.  This
    operation never removes an active monitor definition.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", name):
        raise ManageError("invalid_params", f"invalid monitor name {name!r}")
    draft = paths.drafts_dir / f"{name}.toml"
    if not draft.exists():
        raise ManageError("not_found", f"no draft named {name!r}")
    reject_symlink(draft)
    draft.unlink()


_ENABLED_LINE = re.compile(r"^(\s*enabled\s*=\s*)(true|false)(\s*(#.*)?)$",
                           re.MULTILINE)


def set_enabled(paths: Paths, name: str, enabled: bool) -> Path:
    """MD-05: disabling is a one-line edit with the key retained in place —
    the file (and its git history) stays where the user put it. Done by
    textual substitution, not TOML re-serialization, so comments and
    formatting survive."""
    target = paths.monitors_dir / f"{name}.toml"
    if not target.exists():
        raise ManageError(
            code="not_found",
            message=f"no monitor named {name!r}",
            hint="ftmon monitors lists what exists",
        )
    reject_symlink(target)
    text = target.read_text()
    new_text, n = _ENABLED_LINE.subn(
        lambda m: f"{m.group(1)}{'true' if enabled else 'false'}{m.group(3)}",
        text, count=1)
    if n == 0:
        raise ManageError(
            code="invalid_params",
            message=f"{target} has no `enabled = true|false` line to edit",
            hint="add `enabled = true` under [monitor] and retry",
        )
    _validate_text(new_text)  # never write a file the daemon would reject
    atomic_write(target, new_text.encode())
    return target

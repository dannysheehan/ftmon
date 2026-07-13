"""Install curated extra-monitor recipes into the live config tree (XR-02, EC-01).

Merges recipe ``checks.toml.example`` into the administrator registry and
copies ``monitor.toml`` so operators adopt reviewed integrations without
hand-editing argv. The merged registry is validated on a scratch file before
replacing the live authority file (EC-06).
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from ftmon.checks.registry import RegistryError
from ftmon.checks.registry import load as load_check_registry
from ftmon.definitions import manage
from ftmon.paths import Paths, atomic_write
from ftmon.recipes.catalogue import load_manifest, resolve_recipe_path

_REGISTRY_HEADER = (
    "# Administrator-owned external check registry.\n"
    "# Monitor definitions may reference aliases declared here.\n"
)


class InstallError(Exception):
    """Stable, operator-facing recipe install failure."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(category)


@dataclass(frozen=True)
class InstallResult:
    recipe_id: str
    monitor: str
    aliases: tuple[str, ...]
    monitor_path: Path
    enabled: bool


def _read_registry(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError("invalid_registry", f"{path}: unreadable checks registry") from exc
    checks = document.get("check")
    if checks is None:
        return {}
    if not isinstance(checks, dict):
        raise InstallError("invalid_registry", f"{path}: [check] must be a table")
    if any(not isinstance(entry, dict) for entry in checks.values()):
        raise InstallError("invalid_registry", f"{path}: [check] entries must be tables")
    return dict(checks)


def _commit_registry(paths: Paths, payload: bytes) -> None:
    """Validate merged authority on a scratch file, then replace the live registry."""
    target = paths.check_registry_file
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        tmp_path = Path(tmp)
        try:
            load_check_registry(tmp_path, paths=paths)
        except RegistryError as exc:
            raise InstallError(
                exc.category, f"checks registry rejected after merge: {exc.category}",
            ) from exc
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prepare_monitor(recipe: Path, *, enable: bool) -> tuple[str, str]:
    monitor_src = recipe / "monitor.toml"
    try:
        monitor_text = monitor_src.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(
            "recipe_invalid", f"{recipe.name}: monitor.toml unreadable",
        ) from exc
    if enable:
        monitor_text = re.sub(
            r"(?m)^enabled\s*=\s*false\s*$",
            "enabled = true",
            monitor_text,
            count=1,
        )
    name_match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', monitor_text)
    if not name_match:
        raise InstallError("recipe_invalid", f"{recipe.name}: monitor.toml has no name")
    return name_match.group(1), monitor_text


def merge_recipe_checks(
    paths: Paths,
    ref: str,
    *,
    force: bool = False,
) -> tuple[str, ...]:
    """Merge a recipe's checks.toml.example into the administrator registry.

    Skips aliases already present unless ``force``; never grants argv authority
    from a recipe without the administrator's explicit overwrite (EC-01).
    """
    recipe = resolve_recipe_path(ref)
    example = recipe / "checks.toml.example"
    try:
        document = tomllib.loads(example.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(
            "recipe_invalid", f"{recipe.name}: checks.toml.example unreadable",
        ) from exc
    incoming = document.get("check")
    if not isinstance(incoming, dict) or not incoming:
        raise InstallError(
            "recipe_invalid", f"{recipe.name}: checks.toml.example has no [check] entries",
        )

    current = _read_registry(paths.check_registry_file)
    merged = dict(current)
    added: list[str] = []
    for alias, entry in incoming.items():
        if alias in merged and not force:
            continue
        if alias in merged and force:
            merged[alias] = entry
            added.append(alias)
            continue
        merged[alias] = entry
        added.append(alias)

    if not added and not force:
        return ()

    payload = _REGISTRY_HEADER + tomli_w.dumps({"check": merged})
    # Fail before replacing the live registry: a rejected merge must not discard
    # the administrator's last-good authority file (EC-06).
    _commit_registry(paths, payload.encode("utf-8"))
    return tuple(added)


def install_recipe(
    paths: Paths,
    ref: str,
    *,
    force: bool = False,
    enable: bool = True,
) -> InstallResult:
    """Install monitor TOML and registry entries for a curated recipe.

    When the monitor file already exists and ``force`` is false, only flips
    ``enabled`` when requested — definitions are not silently replaced (PM-04).
    """
    try:
        recipe = resolve_recipe_path(ref)
        load_manifest(ref)
    except FileNotFoundError as exc:
        raise InstallError("recipe_not_found", str(exc)) from exc
    recipe_id = recipe.name

    monitor_name, monitor_text = _prepare_monitor(recipe, enable=enable)
    aliases = merge_recipe_checks(paths, ref, force=force)
    target = paths.monitors_dir / f"{monitor_name}.toml"
    if target.exists() and not force:
        try:
            check_aliases = frozenset(
                load_check_registry(paths.check_registry_file, paths=paths)
            )
        except RegistryError:
            check_aliases = frozenset()
        if enable:
            manage.set_enabled(
                paths, monitor_name, True, check_aliases=check_aliases,
            )
            return InstallResult(recipe_id, monitor_name, aliases, target, True)
        current = target.read_text(encoding="utf-8")
        return InstallResult(
            recipe_id,
            monitor_name,
            aliases,
            target,
            bool(re.search(r"(?m)^enabled\s*=\s*true\s*$", current)),
        )

    paths.monitors_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(target, monitor_text.encode("utf-8"))
    try:
        check_aliases = frozenset(
            load_check_registry(paths.check_registry_file, paths=paths)
        )
    except RegistryError:
        check_aliases = frozenset()
    if enable:
        manage.set_enabled(
            paths, monitor_name, True, check_aliases=check_aliases,
        )
    return InstallResult(recipe_id, monitor_name, aliases, target, enable)

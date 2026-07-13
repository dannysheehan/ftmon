"""Locate extra-monitor recipes on disk (XR-*); nothing is bundled per recipe.

Catalogue roots are discovered from ``FTMON_EXTRA_MONITORS`` (OS path list) and,
when developing from a git checkout, ``<repo>/extra-monitors/``. Adding a recipe
is adding a directory under that tree — no pyproject or package-data edits.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def _repo_extra_monitors() -> Path | None:
    """Walk upward from this module for a repository ``extra-monitors/`` tree."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "extra-monitors"
        if not candidate.is_dir():
            continue
        for child in candidate.iterdir():
            if child.is_dir() and (child / "recipe.toml").is_file():
                return candidate
    return None


def catalogue_roots() -> list[Path]:
    """Ordered search roots; first match wins for a given recipe id."""
    roots: list[Path] = []
    seen: set[Path] = set()
    env = os.environ.get("FTMON_EXTRA_MONITORS", "")
    for part in env.split(os.pathsep):
        if not part.strip():
            continue
        root = Path(part).expanduser().resolve()
        if root.is_dir() and root not in seen:
            seen.add(root)
            roots.append(root)
    repo = _repo_extra_monitors()
    if repo is not None and repo not in seen:
        roots.append(repo)
    return roots


def resolve_recipe_path(ref: str) -> Path:
    """Resolve a recipe id or explicit directory path to a recipe folder."""
    direct = Path(ref).expanduser()
    if direct.is_dir() and (direct / "recipe.toml").is_file():
        return direct.resolve()
    for root in catalogue_roots():
        candidate = root / ref
        if candidate.is_dir() and (candidate / "recipe.toml").is_file():
            return candidate
    hint = (
        "set FTMON_EXTRA_MONITORS to your extra-monitors catalogue root, "
        "or pass the recipe directory path"
    )
    raise FileNotFoundError(f"recipe {ref!r} not found ({hint})")


def list_recipe_ids() -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for root in catalogue_roots():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if not (child / "recipe.toml").is_file():
                continue
            if child.name not in seen:
                seen.add(child.name)
                ids.append(child.name)
    return ids


def load_manifest(ref: str) -> dict:
    with resolve_recipe_path(ref).joinpath("recipe.toml").open("rb") as stream:
        return tomllib.load(stream)

"""Locate shipped extra-monitor recipes (offline catalogue, not Exchange)."""

from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path
from typing import Any


def _packaged_root():
    try:
        return importlib.resources.files("ftmon.recipes")
    except (ImportError, ModuleNotFoundError, TypeError):
        return None


def _repo_root() -> Path:
    # src/ftmon/recipes/catalogue.py -> repository root is three parents up.
    return Path(__file__).resolve().parents[3]


def recipe_roots() -> list[Path | Any]:
    """Packaged catalogue first, then the repository extra-monitors tree in dev."""
    roots: list[Path | Any] = []
    packaged = _packaged_root()
    if packaged is not None:
        roots.append(packaged)
    repo = _repo_root() / "extra-monitors"
    if repo.is_dir():
        roots.append(repo)
    return roots


def list_recipe_ids() -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for root in recipe_roots():
        if isinstance(root, Path):
            candidates = sorted(
                path.name for path in root.iterdir()
                if path.is_dir() and not path.name.startswith("_")
                and (path / "recipe.toml").is_file()
            )
        else:
            try:
                candidates = sorted(
                    item.name for item in root.iterdir()
                    if not item.name.startswith("_")
                    and (item / "recipe.toml").is_file()
                )
            except (AttributeError, FileNotFoundError, TypeError):
                candidates = []
        for recipe_id in candidates:
            if recipe_id not in seen:
                seen.add(recipe_id)
                ids.append(recipe_id)
    return ids


def recipe_dir(recipe_id: str) -> Path:
    """Return a concrete directory for *recipe_id* or raise FileNotFoundError."""
    for root in recipe_roots():
        if isinstance(root, Path):
            candidate = root / recipe_id
            if (candidate / "recipe.toml").is_file():
                return candidate
        else:
            candidate = root / recipe_id
            try:
                if (candidate / "recipe.toml").is_file():
                    return Path(str(candidate))
            except (AttributeError, TypeError):
                continue
    raise FileNotFoundError(recipe_id)


def load_manifest(recipe_id: str) -> dict:
    with recipe_dir(recipe_id).joinpath("recipe.toml").open("rb") as stream:
        return tomllib.load(stream)

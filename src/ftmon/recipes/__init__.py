"""Curated extra-monitor recipe discovery and installation (XR-*, EC-01)."""

from ftmon.recipes.catalogue import list_recipe_ids, recipe_dir
from ftmon.recipes.install import InstallError, install_recipe, merge_recipe_checks

__all__ = [
    "InstallError",
    "install_recipe",
    "list_recipe_ids",
    "merge_recipe_checks",
    "recipe_dir",
]

"""Curated extra-monitor recipe discovery and installation (XR-*, EC-01)."""

from ftmon.recipes.catalogue import catalogue_roots, list_recipe_ids, resolve_recipe_path
from ftmon.recipes.install import InstallError, install_recipe, merge_recipe_checks

__all__ = [
    "InstallError",
    "catalogue_roots",
    "install_recipe",
    "list_recipe_ids",
    "merge_recipe_checks",
    "resolve_recipe_path",
]

"""[EC-01][XR-02] Recipe install and registry merge tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ftmon.checks.registry import load
from ftmon.cli import main
from ftmon.paths import get_paths
from ftmon.recipes.install import InstallError, install_recipe, merge_recipe_checks


def _env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FTMON_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("FTMON_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FTMON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FTMON_RUNTIME_DIR", str(tmp_path / "run"))


def _recipe_tree(tmp_path: Path, recipe_id: str = "test-recipe") -> Path:
    catalogue = tmp_path / "catalogue"
    recipe = catalogue / recipe_id
    recipe.mkdir(parents=True)
    (recipe / "recipe.toml").write_text(
        f'schema = 1\n[recipe]\nid = "{recipe_id}"\ntitle = "test"\n'
    )
    return recipe


def _executable(tmp_path: Path, name: str = "check_http") -> Path:
    executable = tmp_path / name
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return executable


def _monitor_toml() -> str:
    return (
        'schema = 1\n[monitor]\nname = "demo_ftmon_https"\n'
        'description = "d"\nversion = 1\nenabled = false\nplatforms = ["linux"]\n'
        'interval = "60s"\nsource = "external"\n'
        '[source_options]\ncheck = "demo_ftmon_https"\nentity = "https://example.test/"\n'
        '[[rule]]\nid = "down"\nwhen = "plugin_state == 2"\n'
        'severity = "critical"\nconfirm_cycles = 1\nmessage = "down"\n'
    )


def test_merge_recipe_checks_writes_protected_registry(tmp_path, monkeypatch):
    """[EC-01] Recipe install merges administrator authority atomically."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\n'
        f'argv = ["{plugin}", "-H", "example.test"]\n'
        f'protocol = "nagios"\n'
        f'timeout = "9s"\n'
    )
    paths = get_paths()
    paths.ensure()

    aliases = merge_recipe_checks(paths, "test-recipe")

    assert aliases == ("demo_ftmon_https",)
    registry = paths.check_registry_file
    assert registry.stat().st_mode & 0o777 == 0o600
    assert "demo_ftmon_https" in load(registry, paths=paths)


def test_install_recipe_enables_monitor_without_restart(tmp_path, monkeypatch):
    """[PM-04] Installed recipe lands in monitors/ enabled for daemon rescan."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(_monitor_toml())
    paths = get_paths()
    paths.ensure()

    result = install_recipe(paths, "test-recipe")

    assert result.enabled is True
    text = (paths.monitors_dir / "demo_ftmon_https.toml").read_text()
    assert "enabled = true" in text


def test_install_recipe_accepts_explicit_directory_path(tmp_path, monkeypatch):
    """[XR-02] Operators can install from a path without catalogue env setup."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(_monitor_toml())
    paths = get_paths()
    paths.ensure()

    result = install_recipe(paths, str(recipe))

    assert result.recipe_id == "test-recipe"
    assert (paths.monitors_dir / "demo_ftmon_https.toml").exists()


def test_cli_recipe_install_and_check_install_alias(tmp_path, monkeypatch, capsys):
    """[CL-01] recipe install and check install share one implementation."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(_monitor_toml())
    assert main(["recipe", "list"]) == 0
    listed = capsys.readouterr().out
    assert "test-recipe" in listed
    assert main(["check", "install", "test-recipe"]) == 0
    assert (get_paths().monitors_dir / "demo_ftmon_https.toml").exists()


def test_merge_recipe_checks_skips_existing_alias_without_force(tmp_path, monkeypatch):
    """[EC-01] Install never overwrites administrator argv authority unless forced."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    existing = _executable(tmp_path, "existing_check")
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    paths = get_paths()
    paths.ensure()
    paths.check_registry_file.write_text(
        f'[check.demo_ftmon_https]\nargv = ["{existing}"]\nprotocol = "nagios"\n'
    )
    paths.check_registry_file.chmod(0o600)

    aliases = merge_recipe_checks(paths, "test-recipe")

    assert aliases == ()
    assert load(paths.check_registry_file, paths=paths)["demo_ftmon_https"].argv[0] == str(
        existing,
    )


def test_merge_recipe_checks_rejects_invalid_existing_registry(tmp_path, monkeypatch):
    """[EC-01] Merge refuses to rewrite a registry with invalid [check] entries."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    paths = get_paths()
    paths.ensure()
    paths.check_registry_file.write_text('[check]\ndemo_ftmon_https = "oops"\n')
    paths.check_registry_file.chmod(0o600)

    with pytest.raises(InstallError, match="invalid_registry"):
        merge_recipe_checks(paths, "test-recipe")


def test_merge_recipe_checks_leaves_registry_unchanged_on_rejection(tmp_path, monkeypatch):
    """[EC-06] A rejected merge must not replace the last-good checks registry."""
    _env(tmp_path, monkeypatch)
    existing = _executable(tmp_path, "existing_check")
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        '[check.demo_ftmon_https]\nargv = ["/nonexistent/check_http"]\nprotocol = "nagios"\n'
    )
    paths = get_paths()
    paths.ensure()
    before = (
        f'[check.demo_ftmon_https]\nargv = ["{existing}"]\nprotocol = "nagios"\n'
    )
    paths.check_registry_file.write_text(before)
    paths.check_registry_file.chmod(0o600)

    with pytest.raises(InstallError):
        merge_recipe_checks(paths, "test-recipe", force=True)

    assert paths.check_registry_file.read_text() == before


def test_install_recipe_invalid_monitor_leaves_registry_untouched(tmp_path, monkeypatch):
    """[XR-02] Monitor validation runs before registry merge for atomic install."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text('schema = 1\n[monitor]\ndescription = "missing name"\n')
    paths = get_paths()
    paths.ensure()

    with pytest.raises(InstallError, match="recipe_invalid"):
        install_recipe(paths, "test-recipe")

    assert not paths.check_registry_file.exists()


def test_install_recipe_no_enable_leaves_monitor_disabled(tmp_path, monkeypatch):
    """[PM-04] --no-enable registers authority without turning the monitor on."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(_monitor_toml())
    paths = get_paths()
    paths.ensure()

    result = install_recipe(paths, "test-recipe", enable=False)

    assert result.enabled is False
    text = (paths.monitors_dir / "demo_ftmon_https.toml").read_text()
    assert "enabled = false" in text


def test_install_recipe_raises_when_recipe_missing(tmp_path, monkeypatch):
    """[XR-02] Unknown recipe ids fail before touching checks.toml or monitors/."""
    _env(tmp_path, monkeypatch)
    catalogue = tmp_path / "catalogue"
    catalogue.mkdir()
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(catalogue))
    paths = get_paths()
    paths.ensure()

    with pytest.raises(InstallError, match="recipe_not_found"):
        install_recipe(paths, "no-such-recipe")

    assert not paths.check_registry_file.exists()
    assert list(paths.monitors_dir.glob("*.toml")) == []


def test_cli_recipe_install_reports_missing_recipe(tmp_path, monkeypatch, capsys):
    """[CL-01] Operator-facing install errors use stable categories."""
    _env(tmp_path, monkeypatch)
    catalogue = tmp_path / "catalogue"
    catalogue.mkdir()
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(catalogue))
    get_paths().ensure()

    assert main(["recipe", "install", "missing-recipe"]) == 1
    assert "recipe_not_found" in capsys.readouterr().err


def test_registry_accepts_masked_system_executable_owner(tmp_path, monkeypatch):
    """[EC-01] NoNewPrivileges maps distro plugin ownership to the overflow uid."""
    executable = tmp_path / "check_http"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    real_lstat = Path.lstat

    def masked_lstat(self: Path):
        info = real_lstat(self)
        if self == executable:
            return os.stat_result((
                info.st_mode, 0, 0, 0, 65534, 0, info.st_size,
                info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns,
            ))
        return info

    monkeypatch.setattr(Path, "lstat", masked_lstat)
    monkeypatch.setattr(
        "ftmon.checks.trust._SYSTEM_EXECUTABLE_PREFIXES",
        (str(tmp_path) + "/",),
    )
    path = tmp_path / "checks.toml"
    path.write_text(
        f'[check.demo_ftmon_https]\nargv = ["{executable}"]\nprotocol = "nagios"\n'
    )
    path.chmod(0o600)

    registry = load(path)

    assert registry["demo_ftmon_https"].argv[0] == str(executable)

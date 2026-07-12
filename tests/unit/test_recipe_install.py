"""[EC-01][XR-02] Recipe install and registry merge tests."""

from __future__ import annotations

import os
from pathlib import Path

from ftmon.checks.registry import load
from ftmon.cli import main
from ftmon.paths import get_paths
from ftmon.recipes.install import install_recipe, merge_recipe_checks


def _env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FTMON_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("FTMON_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FTMON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FTMON_RUNTIME_DIR", str(tmp_path / "run"))


def _recipe_tree(tmp_path: Path) -> Path:
    catalogue = tmp_path / "catalogue"
    recipe = catalogue / "http-tls"
    recipe.mkdir(parents=True)
    return recipe


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / "check_http"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return executable


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

    aliases = merge_recipe_checks(paths, "http-tls")

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
    (recipe / "recipe.toml").write_text(
        'schema = 1\n[recipe]\nid = "http-tls"\ntitle = "t"\n'
    )
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(
        'schema = 1\n[monitor]\nname = "demo_ftmon_https"\n'
        'description = "d"\nversion = 1\nenabled = false\nplatforms = ["linux"]\n'
        'interval = "60s"\nsource = "external"\n'
        '[source_options]\ncheck = "demo_ftmon_https"\nentity = "https://example.test/"\n'
        '[[rule]]\nid = "down"\nwhen = "plugin_state == 2"\n'
        'severity = "critical"\nconfirm_cycles = 1\nmessage = "down"\n'
    )
    paths = get_paths()
    paths.ensure()

    result = install_recipe(paths, "http-tls")

    assert result.enabled is True
    text = (paths.monitors_dir / "demo_ftmon_https.toml").read_text()
    assert "enabled = true" in text


def test_install_recipe_accepts_explicit_directory_path(tmp_path, monkeypatch):
    """[XR-02] Operators can install from a path without catalogue env setup."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    (recipe / "recipe.toml").write_text('schema = 1\n[recipe]\nid = "http-tls"\n')
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(
        'schema = 1\n[monitor]\nname = "demo_ftmon_https"\n'
        'description = "d"\nversion = 1\nenabled = false\nplatforms = ["linux"]\n'
        'interval = "60s"\nsource = "external"\n'
        '[source_options]\ncheck = "demo_ftmon_https"\nentity = "https://example.test/"\n'
        '[[rule]]\nid = "down"\nwhen = "plugin_state == 2"\n'
        'severity = "critical"\nconfirm_cycles = 1\nmessage = "down"\n'
    )
    paths = get_paths()
    paths.ensure()

    result = install_recipe(paths, str(recipe))

    assert result.recipe_id == "http-tls"
    assert (paths.monitors_dir / "demo_ftmon_https.toml").exists()


def test_cli_recipe_install_and_check_install_alias(tmp_path, monkeypatch, capsys):
    """[CL-01] recipe install and check install share one implementation."""
    _env(tmp_path, monkeypatch)
    plugin = _executable(tmp_path)
    recipe = _recipe_tree(tmp_path)
    monkeypatch.setenv("FTMON_EXTRA_MONITORS", str(recipe.parent))
    (recipe / "recipe.toml").write_text('schema = 1\n[recipe]\nid = "http-tls"\n')
    (recipe / "checks.toml.example").write_text(
        f'[check.demo_ftmon_https]\nargv = ["{plugin}"]\nprotocol = "nagios"\n'
    )
    (recipe / "monitor.toml").write_text(
        'schema = 1\n[monitor]\nname = "demo_ftmon_https"\n'
        'description = "d"\nversion = 1\nenabled = false\nplatforms = ["linux"]\n'
        'interval = "60s"\nsource = "external"\n'
        '[source_options]\ncheck = "demo_ftmon_https"\nentity = "https://example.test/"\n'
        '[[rule]]\nid = "down"\nwhen = "plugin_state == 2"\n'
        'severity = "critical"\nconfirm_cycles = 1\nmessage = "down"\n'
    )
    assert main(["recipe", "list"]) == 0
    assert "http-tls" in capsys.readouterr().out
    assert main(["check", "install", "http-tls"]) == 0
    assert (get_paths().monitors_dir / "demo_ftmon_https.toml").exists()


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
        "ftmon.checks.registry._SYSTEM_EXECUTABLE_PREFIXES",
        (str(tmp_path) + "/",),
    )
    path = tmp_path / "checks.toml"
    path.write_text(
        f'[check.demo_ftmon_https]\nargv = ["{executable}"]\nprotocol = "nagios"\n'
    )
    path.chmod(0o600)

    registry = load(path)

    assert registry["demo_ftmon_https"].argv[0] == str(executable)

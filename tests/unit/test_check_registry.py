"""[EC-01][EC-06][EC-07] External-check registry validation."""

import os
from pathlib import Path

import pytest

from ftmon.checks.registry import RegistryError, load
from ftmon.paths import get_paths
from tests.platform_permissions import (
    make_broadly_writable,
    make_private,
    symlink_or_skip,
    toml_path,
)


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / ("check_test.exe" if os.name == "nt" else "check_test")
    executable.write_text("#!/bin/sh\nexit 0\n")
    make_private(executable, 0o700)
    return executable


def _registry(tmp_path: Path, text: str) -> Path:
    make_private(tmp_path, 0o700)
    path = tmp_path / "checks.toml"
    path.write_text(text)
    make_private(path, 0o600)
    return path


def test_loads_complete_immutable_registry_with_default_timeout(tmp_path):
    executable = _executable(tmp_path)
    executable_arg = toml_path(executable)
    path = _registry(
        tmp_path,
        f'[check.website_https]\nargv = ["{executable_arg}", "--safe"]\n'
        'protocol = "nagios"\n',
    )

    registry = load(path)

    assert registry["website_https"].argv == (executable_arg, "--safe")
    assert registry["website_https"].timeout_s == 10.0
    with pytest.raises(TypeError):
        registry._entries["another_check"] = registry["website_https"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("replacement", "category"),
    [
        ("website_https", "invalid_alias"),
        ('argv = ["EXEC"]\nprotocol = "shell"', "invalid_protocol"),
        ('argv = ["EXEC"]\nprotocol = "nagios"\ntimeout = "31s"', "invalid_timeout"),
        ('argv = ["relative"]\nprotocol = "nagios"', "invalid_executable"),
    ],
)
def test_rejects_invalid_entry_without_disclosing_argv(tmp_path, replacement, category):
    executable = _executable(tmp_path)
    executable_arg = toml_path(executable)
    if replacement == "website_https":
        text = f'[check.X]\nargv = ["{executable_arg}"]\nprotocol = "nagios"\n'
    else:
        text = f"[check.website_https]\n{replacement.replace('EXEC', executable_arg)}\n"
    path = _registry(tmp_path, text)

    with pytest.raises(RegistryError) as caught:
        load(path)

    assert caught.value.category == category
    assert str(executable) not in str(caught.value)


def test_rejects_writable_registry_or_parent(tmp_path):
    """[SE-07] Command authority must be a protected regular file."""
    executable = _executable(tmp_path)
    target = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{toml_path(executable)}"]\n'
        'protocol = "ftmon-json"\n',
    )
    make_broadly_writable(target, 0o620)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(target)
    make_private(target, 0o600)
    make_broadly_writable(tmp_path, 0o770)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(target)


def test_rejects_symlink_registry(tmp_path):
    """[SE-07] Command authority must not be supplied through a symlink."""
    executable = _executable(tmp_path)
    target = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{toml_path(executable)}"]\n'
        'protocol = "ftmon-json"\n',
    )
    link = tmp_path / "linked.toml"
    symlink_or_skip(link, target)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(link)


def test_rejects_unready_executable_and_protected_runtime_location(tmp_path):
    executable = _executable(tmp_path)
    make_broadly_writable(executable, 0o720)
    path = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{toml_path(executable)}"]\nprotocol = "nagios"\n',
    )
    with pytest.raises(RegistryError, match="executable_unready"):
        load(path)

    make_private(executable, 0o700)
    paths = get_paths(
        {
            "FTMON_CONFIG_DIR": str(tmp_path),
            "FTMON_DATA_DIR": str(tmp_path),
            "FTMON_STATE_DIR": str(tmp_path / "state"),
            "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
        }
    )
    with pytest.raises(RegistryError, match="invalid_executable"):
        load(path, paths=paths)


def test_invalid_replacement_does_not_mutate_previous_registry(tmp_path):
    executable = _executable(tmp_path)
    path = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{toml_path(executable)}"]\nprotocol = "nagios"\n',
    )
    previous = load(path)
    path.write_text("[check.test_check]\nprotocol = 'nagios'\n")
    make_private(path, 0o600)

    with pytest.raises(RegistryError):
        load(path)

    assert previous["test_check"].argv == (toml_path(executable),)

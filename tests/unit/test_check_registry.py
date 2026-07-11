"""[EC-01][EC-06][EC-07] External-check registry validation."""

import os
from pathlib import Path

import pytest

from ftmon.checks.registry import RegistryError, load
from ftmon.paths import get_paths


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / "check_test"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return executable


def _registry(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "checks.toml"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_loads_complete_immutable_registry_with_default_timeout(tmp_path):
    executable = _executable(tmp_path)
    path = _registry(
        tmp_path,
        f'[check.website_https]\nargv = ["{executable}", "--safe"]\nprotocol = "nagios"\n',
    )

    registry = load(path)

    assert registry["website_https"].argv == (str(executable), "--safe")
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
    if replacement == "website_https":
        text = f'[check.X]\nargv = ["{executable}"]\nprotocol = "nagios"\n'
    else:
        text = f"[check.website_https]\n{replacement.replace('EXEC', str(executable))}\n"
    path = _registry(tmp_path, text)

    with pytest.raises(RegistryError) as caught:
        load(path)

    assert caught.value.category == category
    assert str(executable) not in str(caught.value)


def test_rejects_symlink_and_writable_registry_or_parent(tmp_path):
    """[SE-07] Command authority must be a protected regular file."""
    executable = _executable(tmp_path)
    target = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{executable}"]\nprotocol = "ftmon-json"\n',
    )
    link = tmp_path / "linked.toml"
    link.symlink_to(target)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(link)

    target.chmod(0o620)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(target)
    target.chmod(0o600)
    tmp_path.chmod(0o770)
    with pytest.raises(RegistryError, match="registry_untrusted"):
        load(target)


def test_rejects_unready_executable_and_protected_runtime_location(tmp_path):
    executable = _executable(tmp_path)
    executable.chmod(0o720)
    path = _registry(
        tmp_path,
        f'[check.test_check]\nargv = ["{executable}"]\nprotocol = "nagios"\n',
    )
    with pytest.raises(RegistryError, match="executable_unready"):
        load(path)

    executable.chmod(0o700)
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
        f'[check.test_check]\nargv = ["{executable}"]\nprotocol = "nagios"\n',
    )
    previous = load(path)
    path.write_text("[check.test_check]\nprotocol = 'nagios'\n")
    os.chmod(path, 0o600)

    with pytest.raises(RegistryError):
        load(path)

    assert previous["test_check"].argv == (str(executable),)

"""[FS-02][PM-06][SE-04] Native Windows managed-path reparse defenses."""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")

if sys.platform == "win32":
    import win32security

    from ftmon.config import SecretRef
    from ftmon.paths import (
        _reload_event_name,
        atomic_write,
        get_paths,
        set_private_permissions,
    )


def test_reload_event_uses_cross_session_namespace_cl_07_pm_11():
    """[CL-07][PM-11] Task and interactive clients rendezvous across sessions."""
    assert _reload_event_name(1234) == "Global\\ftmon-reload-signal-1234"


def _make_junction(link: Path, target: Path) -> None:
    target.mkdir()
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"unable to create unelevated junction: {result.stderr or result.stdout}")


def _security_text(path: Path) -> str:
    information = (
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
    )
    descriptor = win32security.GetFileSecurity(str(path), information)
    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor, win32security.SDDL_REVISION_1, information
    )


def test_set_private_permissions_refuses_junction_without_mutating_target(tmp_path):
    target = tmp_path / "target"
    junction = tmp_path / "managed"
    _make_junction(junction, target)
    marker = target / "keep.txt"
    marker.write_text("unchanged")
    before = _security_text(target)

    with pytest.raises(OSError) as exc_info:
        set_private_permissions(junction, 0o700)

    assert exc_info.value.errno == errno.ELOOP
    assert marker.read_text() == "unchanged"
    assert _security_text(target) == before


def test_paths_ensure_refuses_managed_config_junction(tmp_path):
    target = tmp_path / "target"
    junction = tmp_path / "config"
    _make_junction(junction, target)
    marker = target / "keep.txt"
    marker.write_text("unchanged")
    before = _security_text(target)
    paths = get_paths(
        {
            "FTMON_CONFIG_DIR": str(junction),
            "FTMON_DATA_DIR": str(tmp_path / "data"),
            "FTMON_STATE_DIR": str(tmp_path / "state"),
            "FTMON_RUNTIME_DIR": str(tmp_path / "runtime"),
        }
    )

    with pytest.raises(OSError) as exc_info:
        paths.ensure()

    assert exc_info.value.errno == errno.ELOOP
    assert marker.read_text() == "unchanged"
    assert _security_text(target) == before


def test_atomic_write_refuses_junction_parent_before_creating_file(tmp_path):
    target = tmp_path / "target"
    junction = tmp_path / "config"
    _make_junction(junction, target)
    before = _security_text(target)

    with pytest.raises(OSError) as exc_info:
        atomic_write(junction / "config.toml", b"must not land")

    assert exc_info.value.errno == errno.ELOOP
    assert not (target / "config.toml").exists()
    assert _security_text(target) == before


def test_secret_ref_rejects_directory_junction_final_component(tmp_path):
    target = tmp_path / "secret-target"
    junction = tmp_path / "secret-link"
    _make_junction(junction, target)

    with pytest.raises(ValueError, match="opened safely"):
        SecretRef(file=junction).resolve()

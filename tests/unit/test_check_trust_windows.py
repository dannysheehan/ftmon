"""[EC-01][SE-04][SE-06][SE-07][PL-01] checks/trust.py's Windows ACL owner
and writability equivalents, against real files with real DACLs on this
machine -- not mocked win32security calls.

Fixture files get a fully explicit, non-inheriting DACL
(PROTECTED_DACL_SECURITY_INFORMATION) rather than relying on whatever ACL
the temp directory happens to inherit: confirmed empirically while writing
these tests that this sandboxed environment's own %TEMP% grants its sandbox
principal write access by default, which would silently make every
"clean" fixture report as broadly-writable regardless of the code under
test -- an ambient-environment trap a hand-rolled fixture must route around
deliberately, not something to leave implicit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="win32security is Windows-only")

if sys.platform == "win32":
    import ntsecuritycon
    import win32security

    from ftmon.checks.trust import (
        accessible_beyond_owner,
        owned_by_self,
        trust_failures,
        trusted_owner,
        writable_beyond_owner,
    )


def _protected_file(tmp_path: Path, name: str, extra_aces=()) -> Path:
    """A file whose DACL is exactly owner+SYSTEM+Administrators (full
    control) plus whatever extra (sid, mask) pairs the caller adds --
    protected so nothing is inherited back in from the parent directory."""
    path = tmp_path / name
    path.write_text("x")

    owner_sid = win32security.GetFileSecurity(
        str(path), win32security.OWNER_SECURITY_INFORMATION
    ).GetSecurityDescriptorOwner()
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    admins_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)

    dacl = win32security.ACL()
    for sid in (owner_sid, system_sid, admins_sid):
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid)
    for sid, mask in extra_aces:
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, mask, sid)

    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        sd,
    )
    return path


def _everyone_sid():
    return win32security.CreateWellKnownSid(win32security.WinWorldSid)


class TestOwnershipAndWritability:
    def test_clean_file_is_trusted_and_not_broadly_accessible(self, tmp_path):
        path = _protected_file(tmp_path, "clean.exe")
        info = os.stat(path)
        assert trusted_owner(path, info) is True
        assert owned_by_self(path, info) is True
        assert writable_beyond_owner(path, info) is False
        assert accessible_beyond_owner(path, info) is False

    def test_everyone_write_ace_fails_both_writable_and_accessible(self, tmp_path):
        path = _protected_file(
            tmp_path, "world_writable.exe",
            extra_aces=[(_everyone_sid(), ntsecuritycon.FILE_GENERIC_WRITE)],
        )
        info = os.stat(path)
        assert writable_beyond_owner(path, info) is True
        assert accessible_beyond_owner(path, info) is True

    def test_everyone_read_only_ace_fails_accessible_but_not_writable(self, tmp_path):
        """accessible_beyond_owner (SE-04 secrets) is strictly stricter than
        writable_beyond_owner (EC-01/SE-07 checks): read-only access to
        Everyone must trip the former but not the latter."""
        path = _protected_file(
            tmp_path, "world_readable.exe",
            extra_aces=[(_everyone_sid(), ntsecuritycon.FILE_GENERIC_READ)],
        )
        info = os.stat(path)
        assert writable_beyond_owner(path, info) is False
        assert accessible_beyond_owner(path, info) is True

    def test_trust_failures_reports_group_or_other_writable(self, tmp_path):
        path = _protected_file(
            tmp_path, "untrusted.exe",
            extra_aces=[(_everyone_sid(), ntsecuritycon.FILE_GENERIC_WRITE)],
        )
        failures = trust_failures(str(path))
        assert any(f.startswith("group_or_other_writable:") for f in failures)

    def test_real_system_file_is_not_a_trusted_owner(self):
        """cmd.exe is owned by TrustedInstaller on a stock install -- neither
        the current user nor SYSTEM/Administrators -- so it must not be
        silently treated as trusted just because it's a system binary (no
        Windows equivalent of the Linux masked_system_executable escape
        hatch exists, by design)."""
        path = Path(r"C:\Windows\System32\cmd.exe")
        info = os.stat(path)
        assert trusted_owner(path, info) is False
        assert owned_by_self(path, info) is False

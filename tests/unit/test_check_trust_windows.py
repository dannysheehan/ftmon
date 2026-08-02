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
        accessible_beyond_owner_open_file,
        owned_by_self,
        owned_by_self_open_file,
        trust_failures,
        trusted_owner,
        writable_beyond_owner,
    )
    from ftmon.paths import open_readonly_nofollow, set_private_permissions


def _protected_file(tmp_path: Path, name: str, extra_aces=()) -> Path:
    """A file whose DACL is exactly owner+SYSTEM+Administrators (full
    control) plus whatever extra (sid, mask) pairs the caller adds --
    protected so nothing is inherited back in from the parent directory."""
    path = tmp_path / name
    path.write_text("x")
    set_private_permissions(path, 0o600)

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


def _null_dacl_file(tmp_path: Path, name: str) -> Path:
    """A user-owned file whose NULL DACL makes it accessible to everyone."""
    path = tmp_path / name
    path.write_text("x")
    set_private_permissions(path, 0o600)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, None, 0)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        sd,
    )
    return path


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

    def test_null_dacl_fails_closed_for_checks_and_secrets(self, tmp_path):
        """[EC-01][SE-04][SE-07] NULL means full access, not no access."""
        path = _null_dacl_file(tmp_path, "null_dacl.exe")
        info = os.stat(path)
        assert owned_by_self(path, info) is True
        assert writable_beyond_owner(path, info) is True
        assert accessible_beyond_owner(path, info) is True
        fd = open_readonly_nofollow(path)
        try:
            open_info = os.fstat(fd)
            assert owned_by_self_open_file(fd, open_info) is True
            assert accessible_beyond_owner_open_file(fd, open_info) is True
        finally:
            os.close(fd)
        assert any(
            failure.startswith("group_or_other_writable:")
            for failure in trust_failures(str(path))
        )

    def test_open_handle_checks_do_not_requery_secret_by_path(self, tmp_path, monkeypatch):
        """[SE-04] Post-open secret trust stays anchored to the file handle."""
        from ftmon.config import SecretRef

        path = _protected_file(tmp_path, "secret.txt")
        path.write_text("secret")

        def path_requery_forbidden(*_args, **_kwargs):
            raise AssertionError("secret validation re-queried its path")

        monkeypatch.setattr(win32security, "GetFileSecurity", path_requery_forbidden)
        assert SecretRef(file=path).resolve()._reveal() == "secret"

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

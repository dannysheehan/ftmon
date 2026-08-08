"""Documentation contracts for external-check authority boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_windows_check_trust_contract_is_documented_ec_01_se_07_cl_08():
    """[EC-01][SE-07][CL-08][PL-01] Windows operators can discover the real
    SID/DACL policy, its deliberate System32 consequence, and the safe path."""
    guide = (ROOT / "docs/check-authoring.md").read_text(encoding="utf-8")
    normalized = " ".join(guide.split())

    for required in (
        "%LOCALAPPDATA%\\ftmon\\checks\\",
        "current-user SID",
        "LocalSystem (`SYSTEM`)",
        "Administrators SID",
        r"NT SERVICE\TrustedInstaller",
        "no equivalent path-based escape hatch",
        "ftmon check trust <absolute-path>",
        "never executes the candidate",
    ):
        assert required in normalized

    install = (ROOT / "docs/install.md").read_text(encoding="utf-8")
    assert "check-authoring.md#windows-why-system32-executables-are-rejected" in install
    assert "ftmon check trust <absolute-path>" in install

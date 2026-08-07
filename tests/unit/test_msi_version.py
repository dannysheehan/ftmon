"""[DO-02] PEP 440 → MSI version mapping for Windows packaging (issue #95)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.windows.msi_version import assert_versions_agree, map_pep440_to_msi


@pytest.mark.parametrize(
    ("pep440", "msi"),
    [
        ("2.0.0a15", "2.0.15"),
        ("2.0.0b1", "2.0.201"),
        ("2.0.0rc2", "2.0.402"),
        ("2.0.0", "2.0.800"),
        ("2.0.0.post3", "2.0.803"),
        ("2.1.2", "2.1.2800"),
    ],
)
def test_msi_version_mapping_examples(pep440: str, msi: str):
    mapped = map_pep440_to_msi(pep440)
    assert mapped.msi == msi
    assert mapped.pep440 == pep440
    assert mapped.file_version == tuple(int(x) for x in msi.split(".")) + (0,)


def test_msi_version_rejects_dev_and_overflow():
    with pytest.raises(ValueError, match="dev"):
        map_pep440_to_msi("2.0.0.dev1")
    with pytest.raises(ValueError, match="prerelease number"):
        map_pep440_to_msi("2.0.0a200")
    with pytest.raises(ValueError, match="16-bit|build"):
        map_pep440_to_msi("2.0.70")  # patch 70 → build 70800 > 65535
    with pytest.raises(ValueError, match="major/minor"):
        map_pep440_to_msi("256.0.0")
    with pytest.raises(ValueError, match="major/minor"):
        map_pep440_to_msi("1.256.0")


def test_release_gate_versions_agree():
    assert_versions_agree(tag="v2.0.0a15", package="2.0.0a15", frozen="2.0.0a15", msi="2.0.15")
    with pytest.raises(ValueError):
        assert_versions_agree(tag="v2.0.0", package="2.0.0a15", frozen="2.0.0a15", msi="2.0.15")


def test_parse_ftmon_version_from_cli_output():
    """[DO-02] Frozen ``ftmon --version`` text feeds the release version gate."""
    from tools.windows.check_version_agreement import parse_ftmon_version

    assert parse_ftmon_version("ftmon 2.0.0a15\n") == "2.0.0a15"
    assert parse_ftmon_version("FTMON 2.0.0\n") == "2.0.0"
    with pytest.raises(ValueError):
        parse_ftmon_version("not a version line\n")


def test_check_version_agreement_requires_msi_path():
    """[DO-02] Gate must inspect a built MSI, not compare the mapping to itself."""
    from tools.windows import check_version_agreement as mod

    with pytest.raises(SystemExit, match="--msi"):
        mod.main(["--exe", str(Path(__file__))])

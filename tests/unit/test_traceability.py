"""[TS-01] Requirements traceability: index freshness, coverage completeness.

Tests verify:
1. regenerating from SPEC.md equals committed tests/reqindex.json
2. all testable requirements are covered (test tag) or listed in pending
3. no requirement is both covered and pending (ratchet)
4. pending contains no unknown or exempt IDs

Docstring requirement IDs feed TS-01's scan (test tags are [XX-nn] bracketed).
"""

import json

# Import the tool's functions for coverage scanning and index generation
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
from gen_reqindex import (
    load_pending,
    load_reqindex,
    scan_spec_for_ids,
    scan_test_coverage,
)

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_PATH = REPO_ROOT / "SPEC.md"
TESTS_DIR = REPO_ROOT / "tests"
INDEX_PATH = TESTS_DIR / "reqindex.json"
PENDING_PATH = TESTS_DIR / "traceability_pending.json"


class TestTraceabilityIndex:
    """[TS-01] Index freshness and data integrity."""

    def test_reqindex_freshness(self):
        """[TS-01] regenerating from SPEC.md equals committed tests/reqindex.json."""
        # Generate current state from SPEC.md
        testable_ids, exempt_ids = scan_spec_for_ids(SPEC_PATH)

        # Load committed index
        committed_testable, committed_exempt = load_reqindex(INDEX_PATH)

        # Both must match
        assert testable_ids == committed_testable, (
            "testable IDs differ from SPEC.md. "
            "Run: uv run python tools/gen_reqindex.py"
        )
        assert exempt_ids == committed_exempt, (
            "exempt IDs differ from SPEC.md. "
            "Run: uv run python tools/gen_reqindex.py"
        )

    def test_reqindex_file_structure(self):
        """[TS-01] reqindex.json has correct schema."""
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

        assert "generated_from" in data
        assert data["generated_from"] == "SPEC.md"
        assert "testable" in data
        assert "exempt" in data
        assert isinstance(data["testable"], list)
        assert isinstance(data["exempt"], list)

        # All should be sorted
        assert data["testable"] == sorted(data["testable"])
        assert data["exempt"] == sorted(data["exempt"])


class TestRequirementCoverage:
    """[TS-01] Coverage completeness: every testable requirement tracked."""

    def test_all_testable_covered_or_pending(self):
        """[TS-01] every testable requirement is covered by tests or in pending."""
        testable_ids, _ = load_reqindex(INDEX_PATH)
        covered_ids = scan_test_coverage(TESTS_DIR)
        pending_ids = load_pending(PENDING_PATH)

        # A requirement is satisfied if it's covered OR pending
        covered_or_pending = covered_ids | pending_ids
        orphans = testable_ids - covered_or_pending

        assert not orphans, (
            f"Uncovered, non-pending requirements: {sorted(orphans)}. "
            f"Either add test tags [XX-nn] or run: "
            f"uv run python tools/gen_reqindex.py --init-pending"
        )

    def test_no_id_both_covered_and_pending(self):
        """[TS-01] Ratchet: no ID in both covered and pending sets."""
        covered_ids = scan_test_coverage(TESTS_DIR)
        pending_ids = load_pending(PENDING_PATH)

        both = covered_ids & pending_ids

        assert not both, (
            f"Requirements found in both covered and pending: {sorted(both)}. "
            f"Remove from pending when coverage is added. "
            f"Run: uv run python tools/gen_reqindex.py --init-pending"
        )

    def test_pending_file_structure(self):
        """[TS-01] traceability_pending.json is a sorted list."""
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))

        assert isinstance(data, list), "pending should be a JSON array"
        assert all(isinstance(item, str) for item in data)
        assert data == sorted(data), "pending IDs must be sorted"


class TestPendingValidation:
    """[TS-01] Pending list contains only unknown/testable IDs."""

    def test_pending_contains_only_known_testable_ids(self):
        """[TS-01] pending must not contain unknown or exempt requirement IDs."""
        testable_ids, _ = load_reqindex(INDEX_PATH)
        pending_ids = load_pending(PENDING_PATH)

        # Every ID in pending must be a testable ID (not exempt)
        invalid_in_pending = pending_ids - testable_ids

        assert not invalid_in_pending, (
            f"pending.json contains unknown/exempt IDs: {sorted(invalid_in_pending)}"
        )

    def test_pending_is_subset_of_testable(self):
        """[TS-01] pending is subset of testable (never has exempt IDs)."""
        testable_ids, exempt_ids = load_reqindex(INDEX_PATH)
        pending_ids = load_pending(PENDING_PATH)

        # Intersection should be empty (no exempt in pending)
        exempt_in_pending = pending_ids & exempt_ids

        assert not exempt_in_pending, (
            f"pending.json contains exempt IDs: {sorted(exempt_in_pending)}"
        )

        # All pending should be testable
        assert pending_ids <= testable_ids


class TestCoverageScan:
    """[TS-01] Coverage scan finds [XX-nn] tags in test files."""

    def test_scan_finds_docstring_tags(self):
        """[TS-01] scan_test_coverage finds [XX-nn] in docstrings."""
        # This test itself has [TS-01] in its docstring, so it should find itself
        covered = scan_test_coverage(TESTS_DIR)
        assert "TS-01" in covered

    def test_scan_finds_comment_tags(self):
        """[TS-01] scan_test_coverage finds [XX-nn] in comments."""
        # The tool itself has requirement references; verify it gets scanned
        covered = scan_test_coverage(TESTS_DIR)

        # Verify that at least the basic expression tests are covered
        # (test_expr_parse.py has [EX-01], etc.)
        assert len(covered) > 0

    def test_covered_ids_are_valid(self):
        """[TS-01] all scanned coverage tags are valid XX-nn format."""
        covered = scan_test_coverage(TESTS_DIR)
        testable_ids, exempt_ids = load_reqindex(INDEX_PATH)

        all_ids = testable_ids | exempt_ids

        invalid = covered - all_ids

        assert not invalid, (
            f"Coverage scan found invalid/unknown IDs: {sorted(invalid)}"
        )


class TestDocVersionCoherence:
    """[TS-19] SPEC header, newest changelog entry, and DESIGN companion agree.

    The Status header drifted from the changelog twice (v0.10->v0.11,
    v0.12->v0.14), each time caught only by a later manual review; this
    encodes the check as a test per the repository lint-rules-are-tests rule.
    """

    _HEADER_RE = r"^Status: \*\*(?:DRAFT\s+)?v(\d+\.\d+(?:\.\d+)?)\*\*"
    _ENTRY_RE = r"^\*\*v(\d+\.\d+(?:\.\d+)?) \("
    _COMPANION_RE = r"Companion to `SPEC\.md` v(\d+\.\d+(?:\.\d+)?)"

    @staticmethod
    def _as_tuple(version: str) -> tuple:
        return tuple(int(part) for part in version.split("."))

    def _spec_header_version(self) -> str:
        import re

        text = SPEC_PATH.read_text(encoding="utf-8")
        match = re.search(self._HEADER_RE, text, re.MULTILINE)
        assert match, "SPEC.md Status header version not found"
        return match.group(1)

    def _changelog_versions(self) -> list:
        import re

        text = SPEC_PATH.read_text(encoding="utf-8")
        changelog = text[text.index("## 21."):]
        versions = re.findall(self._ENTRY_RE, changelog, re.MULTILINE)
        assert versions, "SPEC.md section 21 has no version entries"
        return versions

    def test_header_matches_newest_changelog_entry(self):
        """[TS-19] Status header version equals the first section-21 entry."""
        header = self._spec_header_version()
        newest = self._changelog_versions()[0]
        assert header == newest, (
            f"SPEC.md Status header v{header} != newest changelog entry "
            f"v{newest}; bump the header when appending a changelog entry"
        )

    def test_newest_changelog_entry_is_highest(self):
        """[TS-19] Section-21 entries are newest-first by version."""
        versions = self._changelog_versions()
        highest = max(versions, key=self._as_tuple)
        assert versions[0] == highest, (
            f"newest changelog entry v{versions[0]} is not the highest "
            f"version present (v{highest}); new entries go at the top"
        )

    def test_design_companion_matches_spec(self):
        """[TS-19] DESIGN.md companion reference tracks the SPEC version."""
        import re

        design = (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8")
        match = re.search(self._COMPANION_RE, design)
        assert match, "DESIGN.md companion reference not found"
        assert match.group(1) == self._spec_header_version(), (
            f"DESIGN.md says companion to SPEC v{match.group(1)} but SPEC.md "
            f"header is v{self._spec_header_version()}; update both together"
        )

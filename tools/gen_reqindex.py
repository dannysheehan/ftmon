#!/usr/bin/env python3
"""
Generate requirements traceability index from SPEC.md.

Scans SPEC.md for requirement IDs (XX-nn) marked as **XX-nn**, classifies
them as testable or exempt, and writes tests/reqindex.json. Supports
--check (CI gate: regenerated index must match committed) and --init-pending
(list testable IDs not yet covered by tests) (TS-01).

This module defines functions importable by tests/unit/test_traceability.py
to verify completeness.
"""

import json
import re
import sys
from pathlib import Path

# Requirement ID prefix patterns (case-insensitive per SPEC)
EXEMPT_PREFIXES = {"NG", "DO"}


def scan_spec_for_ids(spec_path: Path) -> tuple[set[str], set[str]]:
    """
    Scan SPEC.md for bold requirement IDs (**XX-nn**).

    Returns (testable_ids, exempt_ids) sets, both in document order
    (sets preserve insertion order in Python 3.7+).
    """
    spec_text = spec_path.read_text(encoding="utf-8")

    # Regex matches **XX-nn** where XX is 2 uppercase letters, nn is 2+ digits
    pattern = r"\*\*([A-Z]{2}-\d{2})\*\*"

    testable = {}  # id -> position, to preserve document order
    exempt = {}

    for match in re.finditer(pattern, spec_text):
        req_id = match.group(1)
        pos = match.start()

        if req_id[:2] in EXEMPT_PREFIXES:
            exempt[req_id] = pos
        else:
            testable[req_id] = pos

    # Return sets, but order by position to preserve SPEC order
    testable_ids = set(testable.keys())
    exempt_ids = set(exempt.keys())

    return testable_ids, exempt_ids


def scan_test_coverage(tests_dir: Path) -> set[str]:
    """
    Scan test files for requirement coverage tags [XX-nn].

    Walks tests_dir recursively, collecting all bracketed tags [XX-nn] found
    in file contents (docstrings, comments). Returns set of covered IDs.

    This function is importable by tests/unit/test_traceability.py.
    """
    pattern = r"\[([A-Z]{2}-\d{2})\]"
    covered = set()

    for py_file in tests_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for match in re.finditer(pattern, content):
            covered.add(match.group(1))

    return covered


def load_reqindex(index_path: Path) -> tuple[set[str], set[str]]:
    """Load tests/reqindex.json and return (testable, exempt) sets."""
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        return set(data["testable"]), set(data["exempt"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return set(), set()


def load_pending(pending_path: Path) -> set[str]:
    """Load tests/traceability_pending.json and return set of pending IDs."""
    try:
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        # pending.json is a flat list of requirement IDs
        if isinstance(data, list):
            return set(data)
        return set()
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def write_reqindex(
    index_path: Path, testable_ids: set[str], exempt_ids: set[str]
) -> None:
    """Write tests/reqindex.json with sorted ID lists."""
    data = {
        "generated_from": "SPEC.md",
        "testable": sorted(testable_ids),
        "exempt": sorted(exempt_ids),
    }
    index_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_pending(pending_path: Path, pending_ids: set[str]) -> None:
    """Write tests/traceability_pending.json with sorted ID list."""
    data = sorted(pending_ids)
    pending_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    """CLI entry point: regenerate or check reqindex.json."""
    repo_root = Path(__file__).parent.parent
    spec_path = repo_root / "SPEC.md"
    tests_dir = repo_root / "tests"
    index_path = tests_dir / "reqindex.json"
    pending_path = tests_dir / "traceability_pending.json"

    if not spec_path.exists():
        print(f"Error: {spec_path} not found", file=sys.stderr)
        sys.exit(1)

    testable_ids, exempt_ids = scan_spec_for_ids(spec_path)

    # Default: regenerate
    if len(sys.argv) == 1:
        write_reqindex(index_path, testable_ids, exempt_ids)
        print(f"Generated {index_path}: {len(testable_ids)} testable, "
              f"{len(exempt_ids)} exempt")
        sys.exit(0)

    # --check: verify committed file matches current SPEC.md
    if sys.argv[1] == "--check":
        committed_testable, committed_exempt = load_reqindex(index_path)
        if (committed_testable == testable_ids and
            committed_exempt == exempt_ids):
            print("✓ reqindex.json is up to date")
            sys.exit(0)
        else:
            print("Error: reqindex.json is stale. Run: uv run python "
                  "tools/gen_reqindex.py", file=sys.stderr)
            sys.exit(1)

    # --init-pending: write uncovered testable IDs to traceability_pending.json
    if sys.argv[1] == "--init-pending":
        covered = scan_test_coverage(tests_dir)
        pending = testable_ids - covered
        write_pending(pending_path, pending)
        print(f"Generated {pending_path}: {len(pending)} pending requirement"
              f"{'s' if len(pending) != 1 else ''}")
        sys.exit(0)

    print(f"Usage: python {sys.argv[0]} [--check|--init-pending]",
          file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()

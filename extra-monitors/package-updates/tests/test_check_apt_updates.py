# SPDX-License-Identifier: MIT
"""Direct behavioral tests for the maintained check_apt_updates script (XR-05)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_apt_updates"


def _run(*argv: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.returncode, json.loads(proc.stdout)


def test_ok_when_no_upgrades(tmp_path):
    """[XR-05] Empty upgradable list with fresh cache is OK."""
    listing = tmp_path / "list.txt"
    listing.write_text("Listing...\n")
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    # mtime = now → age 0
    code, payload = _run(
        "-w", "20",
        "--apt-list-file", str(listing),
        "--cache-stamp", str(stamp),
        "--now", str(stamp.stat().st_mtime),
    )
    assert code == 0
    assert payload["schema"] == 1
    assert payload["state"] == 0
    assert payload["metrics"]["updates_total"]["value"] == 0
    assert payload["metrics"]["updates_security"]["value"] == 0
    assert payload["metrics"]["cache_age"]["value"] == 0


def test_warn_when_total_exceeds_threshold(tmp_path):
    """[XR-05] Non-security backlog above -w becomes warning."""
    listing = tmp_path / "list.txt"
    listing.write_text(
        "Listing...\n"
        "firefox/noble-updates 1.0 amd64 [upgradable from: 0.9]\n"
        "code/stable 1.2 amd64 [upgradable from: 1.1]\n"
        "packer/noble 1.16 amd64 [upgradable from: 1.15]\n"
    )
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    now = stamp.stat().st_mtime
    code, payload = _run(
        "-w", "2",
        "--apt-list-file", str(listing),
        "--cache-stamp", str(stamp),
        "--now", str(now),
    )
    assert code == 0
    assert payload["state"] == 1
    assert payload["metrics"]["updates_total"]["value"] == 3
    assert payload["metrics"]["updates_security"]["value"] == 0


def test_critical_when_security_pending(tmp_path):
    """[XR-05] Any security-pocket upgrade is critical."""
    listing = tmp_path / "list.txt"
    listing.write_text(
        "openssl/noble-security 3.0.2 amd64 [upgradable from: 3.0.1]\n"
    )
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    code, payload = _run(
        "-w", "50",
        "--apt-list-file", str(listing),
        "--cache-stamp", str(stamp),
        "--now", str(stamp.stat().st_mtime),
    )
    assert code == 0
    assert payload["state"] == 2
    assert payload["metrics"]["updates_security"]["value"] == 1


def test_warn_on_stale_cache(tmp_path):
    """[XR-05] Cache older than --cache-stale-s becomes warning."""
    listing = tmp_path / "list.txt"
    listing.write_text("Listing...\n")
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    now = stamp.stat().st_mtime + 8 * 86400
    code, payload = _run(
        "-w", "50",
        "--cache-stale-s", str(7 * 86400),
        "--apt-list-file", str(listing),
        "--cache-stamp", str(stamp),
        "--now", str(now),
    )
    assert code == 0
    assert payload["state"] == 1
    assert payload["metrics"]["cache_age"]["value"] >= 7 * 86400


def test_unknown_when_metadata_missing(tmp_path):
    """[XR-05] Missing list file and stamp fails closed as unknown."""
    code, payload = _run(
        "--apt-list-file", str(tmp_path / "missing.txt"),
        "--cache-stamp", str(tmp_path / "missing.stamp"),
    )
    assert code == 0
    assert payload["state"] == 3
    assert payload["metrics"] == {}


def test_four_field_apt_list_regex(tmp_path):
    """[XR-05] Real apt list shape (four fields before bracket) is counted."""
    listing = tmp_path / "list.txt"
    listing.write_text(
        "Listing...\n"
        "1password/stable 8.12.30 amd64 [upgradable from: 8.12.28]\n"
        "distro-info-data/noble-updates,noble-updates 0.72 all "
        "[upgradable from: 0.60]\n"
    )
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    code, payload = _run(
        "-w", "50",
        "--apt-list-file", str(listing),
        "--cache-stamp", str(stamp),
        "--now", str(stamp.stat().st_mtime),
    )
    assert code == 0
    assert payload["state"] == 0
    assert payload["metrics"]["updates_total"]["value"] == 2

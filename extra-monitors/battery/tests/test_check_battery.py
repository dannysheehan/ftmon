# SPDX-License-Identifier: MIT
"""Direct behavioral tests for the maintained check_battery script (XR-05)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_battery"


def _battery_tree(
    root: Path,
    *,
    capacity: int = 80,
    status: str = "Charging",
    charge_full: int = 4000,
    charge_full_design: int = 8000,
    voltage_now: int = 12_000_000,
    current_now: int = 1_000_000,
    ac_online: int = 1,
    bat_name: str = "BAT0",
    ac_name: str = "AC",
) -> Path:
    bat = root / "class" / "power_supply" / bat_name
    ac = root / "class" / "power_supply" / ac_name
    bat.mkdir(parents=True)
    ac.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "capacity").write_text(f"{capacity}\n")
    (bat / "status").write_text(f"{status}\n")
    (bat / "charge_full").write_text(f"{charge_full}\n")
    (bat / "charge_full_design").write_text(f"{charge_full_design}\n")
    (bat / "voltage_now").write_text(f"{voltage_now}\n")
    (bat / "current_now").write_text(f"{current_now}\n")
    (ac / "type").write_text("Mains\n")
    (ac / "online").write_text(f"{ac_online}\n")
    return root


def _run(sysfs: Path, *argv: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--sysfs-root", str(sysfs), *argv],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_ok_charging_above_thresholds(tmp_path):
    """[XR-05] Healthy charge on AC reports OK with mapped metrics."""
    sysfs = _battery_tree(
        tmp_path / "sys",
        capacity=80,
        charge_full=7200,
        charge_full_design=8000,
    )
    code, payload = _run(sysfs, "-w", "40,60", "-c", "15,40", "--require-ac")
    assert code == 0
    assert payload["schema"] == 1
    assert payload["state"] == 0
    assert payload["metrics"]["charge"]["value"] == 80.0
    assert payload["metrics"]["health"]["value"] == 90.0
    assert payload["metrics"]["ac_online"]["value"] == 1
    assert "Charging" in payload["message"]


def test_warn_on_low_charge(tmp_path):
    """[XR-05] Charge at/below warn threshold becomes warning."""
    sysfs = _battery_tree(
        tmp_path / "sys",
        capacity=35,
        charge_full=7000,
        charge_full_design=8000,
    )
    code, payload = _run(sysfs, "-w", "40,60", "-c", "15,40")
    assert code == 0
    assert payload["state"] == 1
    assert payload["metrics"]["charge"]["value"] == 35.0


def test_critical_on_very_low_charge(tmp_path):
    """[XR-05] Charge at/below critical threshold becomes critical."""
    sysfs = _battery_tree(
        tmp_path / "sys",
        capacity=8,
        status="Discharging",
        ac_online=0,
    )
    code, payload = _run(sysfs, "-w", "40,60", "-c", "15,40", "--require-ac")
    assert code == 0
    assert payload["state"] == 2
    assert "AC offline" in payload["message"]


def test_require_ac_warns_when_mains_offline(tmp_path):
    """[XR-05] --require-ac treats mains loss as at least warning."""
    sysfs = _battery_tree(
        tmp_path / "sys",
        capacity=85,
        status="Discharging",
        charge_full=7500,
        charge_full_design=8000,
        ac_online=0,
    )
    code, payload = _run(sysfs, "-w", "40,60", "-c", "15,40", "--require-ac")
    assert code == 0
    assert payload["state"] == 1
    assert payload["metrics"]["ac_online"]["value"] == 0


def test_unknown_when_battery_missing(tmp_path):
    """[XR-05] Missing sysfs battery fails closed as unknown."""
    root = tmp_path / "sys" / "class" / "power_supply"
    root.mkdir(parents=True)
    code, payload = _run(tmp_path / "sys", "--battery", "BAT0")
    assert code == 0
    assert payload["state"] == 3
    assert payload["metrics"] == {}


def test_invalid_threshold_is_unknown(tmp_path):
    """[XR-05] Malformed -w/-c pairs fail closed as unknown."""
    sysfs = _battery_tree(tmp_path / "sys")
    code, payload = _run(sysfs, "-w", "40")
    assert code == 0
    assert payload["state"] == 3

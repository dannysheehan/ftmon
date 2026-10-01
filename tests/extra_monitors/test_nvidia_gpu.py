"""Direct behavior tests for the maintained NVIDIA GPU recipe (XR-05, EC-10)."""

from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "extra-monitors/nvidia-gpu/scripts/check_gpu"
CSV = "0, NVIDIA Test GPU, {temp}, {util}, 25, 2048, 8192, 75, 200, 40, 1200, 7000\n"


def _run(monkeypatch, capsys, *, stdout: str = "", stderr: str = "", code: int = 0):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert runpy.run_path(str(SCRIPT))["main"]([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert len(calls) == 1
    assert calls[0][0][:2] == ["nvidia-smi", "--id=0"]
    assert calls[0][1]["timeout"] == 3.0
    return payload


def test_success_emits_typed_metrics_and_warning_then_critical(monkeypatch, capsys):
    """[XR-05][EC-10] A successful query uses severity in JSON and preserves units."""
    result = _run(monkeypatch, capsys, stdout=CSV.format(temp=55, util=42))
    assert result["state"] == 0
    assert result["metrics"]["mem_used"] == {"value": 2048.0, "uom": "MiB"}
    assert result["metrics"]["power"] == {"value": 75.0, "uom": "W"}
    assert result["metrics"]["sm_clock"] == {"value": 1200.0, "uom": "MHz"}

    assert _run(monkeypatch, capsys, stdout=CSV.format(temp=82, util=42))["state"] == 1
    assert _run(monkeypatch, capsys, stdout=CSV.format(temp=82, util=99))["state"] == 2
    assert _run(monkeypatch, capsys, stdout=CSV.format(temp=55, util=42))["state"] == 0


def test_stdout_only_driver_failure_is_unknown_without_stale_csv(monkeypatch, capsys):
    """[XR-05][EC-10] Nonzero nvidia-smi exit cannot become an OK CSV sample."""
    failure = "Failed to initialize NVML: Driver/library version mismatch\n"
    result = _run(monkeypatch, capsys, stdout=failure, code=1)
    assert result == {
        "schema": 1,
        "state": 3,
        "message": f"GPU check failed: {failure.strip()}",
        "metrics": {},
    }
    result = _run(monkeypatch, capsys, stdout=CSV.format(temp=55, util=42), code=1)
    assert result["state"] == 3
    assert result["metrics"] == {}

    recovered = _run(monkeypatch, capsys, stdout=CSV.format(temp=55, util=42))
    assert recovered["state"] == 0
    assert recovered["metrics"]["temp"]["value"] == 55


def test_stderr_takes_precedence_and_diagnostic_is_control_safe(monkeypatch, capsys):
    """[XR-05] A useful stderr diagnostic wins and cannot inject control text."""
    result = _run(
        monkeypatch,
        capsys,
        stdout="less useful output",
        stderr="\x1b[31mNVML failure\x1b[0m\nsecond line\x00" + "x" * 400,
        code=1,
    )
    assert result["state"] == 3
    assert result["message"].startswith("GPU check failed: NVML failure second line")
    assert "less useful" not in result["message"]
    assert "\x1b" not in result["message"]
    assert "\n" not in result["message"]
    assert len(result["message"]) <= 220
    stderr_only = _run(monkeypatch, capsys, stderr="NVML unavailable", code=1)
    assert stderr_only["message"] == "GPU check failed: NVML unavailable"
    assert stderr_only["state"] == 3 and stderr_only["metrics"] == {}


def test_empty_output_and_timeout_are_unknown(monkeypatch, capsys):
    """[XR-05][EC-10] A missing or timed-out query has no metrics and exits zero."""
    result = _run(monkeypatch, capsys)
    assert result["state"] == 3
    assert result["metrics"] == {}
    assert "no data" in result["message"]

    def timed_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert runpy.run_path(str(SCRIPT))["main"]([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == 3
    assert result["metrics"] == {}
    assert "timed out after 3s" in result["message"]


def test_partial_unsupported_values_do_not_become_zero(monkeypatch, capsys):
    """[XR-05][EC-04] Unsupported and nonfinite cells are absent metrics."""
    csv = CSV.format(temp="[N/A]", util=40).replace(", 75, 200,", ", nan, 200,")
    result = _run(monkeypatch, capsys, stdout=csv)
    assert result["state"] == 0
    assert "temp" not in result["metrics"]
    assert "power" not in result["metrics"]
    assert result["metrics"]["util"] == {"value": 40.0, "uom": "%"}

    result = _run(monkeypatch, capsys, stdout=CSV.format(temp="[N/A]", util="[N/A]"))
    assert result["state"] == 3
    assert result["metrics"] == {}


def test_fixtures_match_script_results_from_simulated_nvidia_smi(monkeypatch, capsys):
    """[XR-03][XR-05] Catalogue fixtures represent this script's JSON contract."""
    fixtures = SCRIPT.parents[1] / "fixtures"
    for name, temp in (("ok", 55), ("warning", 82), ("critical", 92)):
        result = _run(monkeypatch, capsys, stdout=CSV.format(temp=temp, util=42))
        assert result == json.loads((fixtures / f"{name}.txt").read_text())
    result = _run(
        monkeypatch,
        capsys,
        stdout="Failed to initialize NVML: Driver/library version mismatch\n",
        code=1,
    )
    assert result == json.loads((fixtures / "unknown.txt").read_text())


@pytest.mark.parametrize("output", ["bad,csv\n", "", "0, GPU, " + ", ".join(["N/A"] * 10)])
def test_unusable_csv_does_not_claim_health(monkeypatch, capsys, output):
    """[XR-05] Malformed or wholly unsupported output cannot claim OK."""
    result = _run(monkeypatch, capsys, stdout=output)
    assert result["state"] == 3
    assert result["metrics"] == {}

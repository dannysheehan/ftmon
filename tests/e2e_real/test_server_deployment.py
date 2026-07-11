"""Opt-in, offline smoke checks for the documented server deployment.

These checks use the host's systemd verifier but never start a system service,
require root, or contact an external notification endpoint. Keeping the smoke
offline makes it safe to run before production credentials exist (TS-13).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import as_file, files

import pytest

from ftmon.store.db import connect, migrate


@pytest.mark.realsystem
def test_server_unit_passes_host_systemd_verification_pm_09(tmp_path):
    """[PM-09][DO-06] The shipped hardened unit parses on the host systemd."""
    if sys.platform != "linux" or not shutil.which("systemd-analyze"):
        pytest.skip("Linux systemd-analyze required")

    resource = files("ftmon").joinpath("systemd/ftmon-server.service")
    if not resource.is_file():
        pytest.fail("packaged ftmon-server.service is missing")
    with as_file(resource) as unit:
        content = unit.read_text()
    # The production executable is intentionally root-owned and may not exist on
    # a developer host; substituting /bin/true tests every systemd directive
    # without weakening the shipped persistence boundary.
    verification_unit = tmp_path / "ftmon-server.service"
    verification_unit.write_text(
        content.replace("ExecStart=/usr/local/bin/ftmon daemon", "ExecStart=/bin/true")
    )
    result = subprocess.run(
        ["systemd-analyze", "verify", str(verification_unit)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.realsystem
def test_server_profile_doctor_resolves_local_secret_without_leaking_ts_13(tmp_path):
    """[NO-10][SE-05][TS-13] Doctor checks a local reference and sends nothing."""
    env = {
        **os.environ,
        "FTMON_CONFIG_DIR": str(tmp_path / "config"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
        "FTMON_NTFY_TOKEN": "offline-smoke-secret",
    }
    subprocess.run(
        [sys.executable, "-m", "ftmon", "init", "--profile", "server"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    config = tmp_path / "config/config.toml"
    content = config.read_text().replace(
        "# token_env = \"FTMON_NTFY_TOKEN\"",
        "token_env = \"FTMON_NTFY_TOKEN\"",
    ).replace("[notify.ntfy]\nenabled = false", "[notify.ntfy]\nenabled = true")
    config.write_text(content)
    conn = connect(tmp_path / "data/ftmon.db")
    migrate(conn)
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "ftmon", "doctor"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Notification desktop: disabled" in output
    assert "Notification ntfy: ready" in output
    assert "offline-smoke-secret" not in output
    assert not (tmp_path / "state/notifications.jsonl").exists()

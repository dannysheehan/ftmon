"""M9 real-runner integration from registered check to persisted incident."""

from __future__ import annotations

import sqlite3

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.paths import get_paths

EXTERNAL_DEF = """
schema = 1
[monitor]
name = "custom_growth"
description = "Synthetic external growth"
version = 1
enabled = true
platforms = ["linux"]
interval = "15s"
source = "external"

[source_options]
check = "growing_value"
entity = "synthetic"

[[source_options.perfdata]]
label = "size"
metric = "size_bytes"
plugin_uom = "B"
unit = "bytes"
kind = "gauge"

[[derived]]
name = "growth_bph"
expr = 'slope(size_bytes, "60s") * 3600'

[[rule]]
id = "growing"
when = "growth_bph > 1"
severity = "warning"
confirm_cycles = 2
message = "{plugin_message}"

[[trend]]
id = "growth"
kind = "growth"
title = "Synthetic growth"
value_metric = "size_bytes"
value_unit = "bytes"
rate_metric = "growth_bph"
rate_unit = "bytes/hour"
"""


def test_registered_json_metric_reaches_history_derived_rule_and_trend(tmp_path):
    """[EC-05][TS-15] Execute, map, derive, persist, alert, and expose Trend metadata."""
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    admin = tmp_path / "admin"
    admin.mkdir(mode=0o700)
    check = admin / "check_growth"
    check.write_text(
        "#!/bin/sh\n"
        "n=0; test ! -f external-count || n=$(cat external-count)\n"
        "n=$((n + 1)); printf '%s' \"$n\" > external-count\n"
        "printf '{\"schema\":1,\"state\":0,\"message\":\"growing\",'\n"
        "printf '\"metrics\":{\"size\":{\"value\":%s,\"uom\":\"B\"}}}' \"$n\"\n"
    )
    check.chmod(0o700)
    paths.check_registry_file.write_text(
        f'[check.growing_value]\nargv=["{check}"]\n'
        'protocol="ftmon-json"\ntimeout="2s"\n'
    )
    paths.check_registry_file.chmod(0o600)
    (paths.monitors_dir / "custom_growth.toml").write_text(EXTERNAL_DEF)

    clock = FakeClock(wall=1_700_000_000, mono=1000)
    core = DaemonCore(paths=paths, clock=clock)
    try:
        assert "custom_growth" in core.monitors
        for _ in range(8):
            core.on_tick(clock.now(), clock.monotonic(), 0)
            clock.advance(15)
        previous_registry = core.check_registry
        paths.check_registry_file.write_text("[check.growing_value]\nprotocol='nagios'\n")
        paths.check_registry_file.chmod(0o600)
        clock.advance(31)
        core.on_tick(clock.now(), clock.monotonic(), 0)
        # An invalid hand edit cannot partially revoke or replace the last
        # complete authority snapshot while the operator repairs the file.
        assert core.check_registry is previous_registry
        assert "custom_growth" in core.monitors
    finally:
        core.conn.close()

    conn = sqlite3.connect(paths.db_file)
    try:
        metrics = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT metric FROM series WHERE monitor='custom_growth'"
            )
        }
        incident = conn.execute(
            "SELECT state,owning_rule FROM incidents WHERE monitor='custom_growth'"
        ).fetchone()
        registry_event = conn.execute(
            "SELECT message FROM events WHERE provider='ftmon.config'"
        ).fetchone()
    finally:
        conn.close()

    assert {"plugin_state", "plugin_ok", "duration_s", "size_bytes", "growth_bph"} <= metrics
    assert incident == ("open", "growing")
    assert registry_event == ("external check registry rejected: invalid_argv",)
    assert core.monitors["custom_growth"].trends[0].value_metric == "size_bytes"

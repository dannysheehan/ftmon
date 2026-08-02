"""Focused external runner and protocol adapter tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, call, patch

from ftmon.checks import CheckRunner, CheckSpec
from ftmon.checks.jsoncheck import parse as parse_json
from ftmon.checks.nagios import parse as parse_nagios
from tests.platform_permissions import make_broadly_writable, make_private

_PYTHON = str(Path(sys.executable).resolve())


def test_windows_termination_falls_back_without_blocking_tick():
    """[EC-02] A failed taskkill and stubborn child use only bounded waits."""
    from ftmon.paths import _terminate_windows_process

    process = Mock(pid=1234)
    process.wait.side_effect = [
        subprocess.TimeoutExpired("child", 0.25),
        subprocess.TimeoutExpired("child", 0.25),
    ]
    with patch("ftmon.paths.subprocess.run", side_effect=OSError("taskkill failed")) as run:
        _terminate_windows_process(process)

    run.assert_called_once()
    assert run.call_args.kwargs["timeout"] == 1.0
    assert process.wait.call_args_list == [call(timeout=0.25), call(timeout=0.25)]
    process.kill.assert_called_once_with()


def test_nagios_state_message_perfdata_and_duplicate_labels():
    """[EC-03] Nagios output maps state and parses only unambiguous values."""
    output = b"WARNING\x07 disk | 'free space'=12.5GB;10;5;0;100 cpu=7%;80;90;0;100 cpu=8%\nignored"
    result = parse_nagios(output, 1, 0.2)

    assert (result.state, result.message, result.failure) == (1, "WARNING disk ", None)
    assert dict(result.values) == {"free space": (12.5, "GB")}


def test_nagios_invalid_exit_and_utf8_are_unknown():
    """[EC-03] Invalid process/output states fail closed with stable categories."""
    assert parse_nagios(b"oops", 9, 0).failure == "exit_status"
    assert parse_nagios(b"\xff", 0, 0).failure == "protocol"


def test_nagios_accepts_optional_threshold_fields_and_rejects_bad_ranges():
    """[EC-03] Common partial perfdata fields remain compatible but validated."""
    result = parse_nagios(
        b"OK | short=1s long=2s;@1:3;4:;0 bad=3s;not-a-range",
        0,
        0,
    )
    assert dict(result.values) == {"short": (1.0, "s"), "long": (2.0, "s")}


def test_ftmon_json_accepts_exact_schema_and_finite_numbers():
    """[EC-10] The native protocol accepts its one exact bounded object shape."""
    result = parse_json(
        b' \n{"schema":1,"state":0,"message":"fine","metrics":'
        b'{"latency":{"value":1.5,"uom":"s"}}}\t',
        0.1,
    )

    assert (result.state, result.message, result.failure) == (0, "fine", None)
    assert dict(result.values) == {"latency": (1.5, "s")}


def test_ftmon_json_rejects_unknown_keys_booleans_and_extra_json():
    """[EC-10] Native output cannot extend schema or exploit bool numeric coercion."""
    bad = (
        b'{"schema":1,"state":0,"message":"x","metrics":'
        b'{"x":{"value":true,"uom":""}}}'
    )
    extra = b'{"schema":1,"state":0,"message":"x","metrics":{}} {}'
    unknown_key = b'{"schema":1,"state":0,"message":"x","metrics":{},"extra":1}'

    assert parse_json(bad, 0).failure == "protocol"
    assert parse_json(extra, 0).failure == "protocol"
    assert parse_json(unknown_key, 0).failure == "protocol"


def test_ftmon_json_rejects_duplicate_keys_at_every_object_level():
    """[EC-10] JSON duplicate-key policy cannot silently change check evidence."""
    duplicate_metric = (
        b'{"schema":1,"state":0,"message":"x","metrics":'
        b'{"x":{"value":1,"value":2,"uom":"s"}}}'
    )
    duplicate_label = (
        b'{"schema":1,"state":0,"message":"x","metrics":'
        b'{"x":{"value":1,"uom":"s"},"x":{"value":2,"uom":"s"}}}'
    )
    assert parse_json(duplicate_metric, 0).failure == "protocol"
    assert parse_json(duplicate_label, 0).failure == "protocol"


def test_runner_uses_fixed_environment_cwd_and_no_shell(tmp_path):
    """[EC-02] Runner supplies only its fixed environment and invokes argv directly."""
    state = tmp_path / "state"
    state.mkdir()
    code = (
        "import json, os; "
        "print(json.dumps({'schema': 1, 'state': 0, "
        "'message': f\"{os.environ['FTMON_CHECK_ALIAS']}:\" "
        "+ os.environ.get('UNSAFE', 'unset') + ':' + os.getcwd(), 'metrics': {}}))"
    )
    os.environ["UNSAFE"] = "inherited"
    spec = CheckSpec("safe", (_PYTHON, "-c", code, "$(touch nope)"), "ftmon-json", 2)

    result = CheckRunner(state).run(spec, float("inf"))

    assert result.state == 0
    assert result.message == f"safe:unset:{state}"
    assert not (state / "nope").exists()


def test_runner_rejects_untrusted_executable_and_caps_output(tmp_path):
    """[EC-02] Last-moment trust checks and stdout bounds fail closed."""
    state = tmp_path / "state"
    state.mkdir()
    runner = CheckRunner(state)
    spec = CheckSpec(
        "large", (_PYTHON, "-c", "import sys; sys.stdout.write('x' * 70000)"),
        "nagios", 2,
    )

    assert runner.run(spec, float("inf")).failure == "output_limit"
    untrusted = tmp_path / ("untrusted.exe" if os.name == "nt" else "untrusted")
    untrusted.write_text("x")
    make_private(untrusted, 0o700)
    make_broadly_writable(untrusted, 0o777)
    assert runner.run(
        CheckSpec("untrusted", (str(untrusted),), "nagios", 2), float("inf")
    ).failure == "executable"


def test_runner_times_out_complete_check(tmp_path):
    """[EC-02] Deadline expiry returns unknown without leaving the leader alive."""
    state = tmp_path / "state"
    state.mkdir()
    started = time.monotonic()

    result = CheckRunner(state).run(
        CheckSpec("slow", (_PYTHON, "-c", "import time; time.sleep(10)"),
                  "nagios", 0.05),
        float("inf"),
    )

    assert result.failure == "timeout"
    assert time.monotonic() - started < 2

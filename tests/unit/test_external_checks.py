"""Focused external runner and protocol adapter tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from unittest.mock import Mock, call, patch

import pytest

from ftmon.checks import CheckRunner, CheckSpec
from ftmon.checks.jsoncheck import parse as parse_json
from ftmon.checks.nagios import parse as parse_nagios
from tests.platform_permissions import (
    make_broadly_writable,
    make_private,
    trusted_python_executable,
)

_PYTHON = trusted_python_executable()


def test_windows_check_environment_is_essential_but_still_scrubbed():
    """[EC-02] Windows runtime roots survive without leaking parent env."""
    from ftmon.paths import external_check_environment

    parent = {
        "SYSTEMROOT": r"D:\Windows",
        "SystemDrive": "D:",
        "windir": r"D:\Windows",
        "TEMP": r"D:\Temp",
        "tmp": r"D:\Tmp",
        "PATHEXT": ".COM;.EXE",
        "SECRET_SENTINEL": "must-not-pass",
        "PATH": "untrusted-parent-path",
    }
    env = external_check_environment("probe", 7, parent, platform_name="windows")
    assert env == {
        "PATH": os.defpath,
        "FTMON_CHECK_ALIAS": "probe",
        "FTMON_CHECK_TIMEOUT": "7",
        "SystemRoot": r"D:\Windows",
        "SystemDrive": "D:",
        "windir": r"D:\Windows",
        "TEMP": r"D:\Temp",
        "TMP": r"D:\Tmp",
        "PATHEXT": ".COM;.EXE",
    }


def test_windows_check_environment_has_documented_systemroot_fallback():
    """[EC-02] A stripped Windows service still exposes the OS root."""
    from ftmon.paths import external_check_environment

    env = external_check_environment("probe", 1, {}, platform_name="windows")
    assert env["SystemRoot"] == r"C:\Windows"


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows environment contract")
def test_runner_launches_check_that_requires_systemroot_on_windows(tmp_path, monkeypatch):
    """[EC-02] A real scrubbed child can locate System32 without env leakage."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FTMON_SECRET_SENTINEL", "must-not-pass")
    code = (
        "import json, os, pathlib; "
        "root=os.environ['SystemRoot']; "
        "ok=(pathlib.Path(root)/'System32'/'cmd.exe').is_file(); "
        "clean='FTMON_SECRET_SENTINEL' not in os.environ; "
        "print(json.dumps({'schema':1,'state':0 if ok and clean else 2,"
        "'message':'windows env ok' if ok and clean else 'windows env bad',"
        "'metrics':{}}))"
    )
    result = CheckRunner(state).run(
        CheckSpec("windows-env", (_PYTHON, "-c", code), "ftmon-json", 5),
        float("inf"),
    )
    assert result.state == 0
    assert result.message == "windows env ok"


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


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T is Windows-only")
def test_windows_timeout_reaps_child_and_grandchild_ec_02(tmp_path):
    """[EC-02] Native taskkill /T leaves no descendant process behind."""
    import psutil

    state = tmp_path / "state"
    state.mkdir()
    pid_file = tmp_path / "descendants.txt"
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import os,pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {p.pid}'); "
        "time.sleep(60)"
    )
    leader_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
        "time.sleep(60)"
    )
    result = CheckRunner(state).run(
        CheckSpec(
            "tree-timeout",
            (_PYTHON, "-c", leader_code, child_code, str(pid_file), grandchild_code),
            "nagios",
            1,
        ),
        float("inf"),
    )
    assert result.failure == "timeout"
    assert pid_file.exists(), "child did not publish descendant PIDs before timeout"
    pids = [int(value) for value in pid_file.read_text().split()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5
    while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all(not psutil.pid_exists(pid) for pid in pids)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group contract")
def test_posix_timeout_reaps_child_and_grandchild_ec_02(tmp_path):
    """[EC-02] Native killpg leaves no descendant process behind."""
    import psutil

    state = tmp_path / "state"
    state.mkdir()
    pid_file = tmp_path / "descendants.txt"
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import os,pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {p.pid}'); "
        "time.sleep(60)"
    )
    leader_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
        "time.sleep(60)"
    )
    result = CheckRunner(state).run(
        CheckSpec(
            "tree-timeout",
            (_PYTHON, "-c", leader_code, child_code, str(pid_file), grandchild_code),
            "nagios",
            1,
        ),
        float("inf"),
    )
    assert result.failure == "timeout"
    assert pid_file.exists(), "child did not publish descendant PIDs before timeout"
    pids = [int(value) for value in pid_file.read_text().split()]
    deadline = time.monotonic() + 5
    while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all(not psutil.pid_exists(pid) for pid in pids)

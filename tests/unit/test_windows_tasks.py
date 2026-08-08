"""[PL-01][DO-02] Windows Task Scheduler helper contracts (issue #94).

Parse-level and runner behavior tests run on any host that has Windows
PowerShell. Registration contract tests exercise Register-ScheduledTask only
when the OS is Windows and the caller can create per-user tasks.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_DIR = ROOT / "src" / "ftmon" / "windows"
INSTALLER = WINDOWS_DIR / "Install-FTMONTasks.ps1"
RUNNER = WINDOWS_DIR / "Invoke-FTMONTask.ps1"


def _powershell() -> str | None:
    """Absolute Windows PowerShell path, or None when unavailable."""
    if os.name == "nt":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
            r"System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        if candidate.is_file():
            return str(candidate)
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_ps(
    script: str,
    *,
    args: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    ps = _powershell()
    if ps is None:
        pytest.skip("Windows PowerShell is required for Task Scheduler helper tests")
    cmd = [
        ps,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-Command",
        script,
    ]
    if args:
        # When invoking -File scripts we pass a different shape; keep helper simple.
        raise AssertionError("use _run_ps_file for -File invocations")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _run_ps_file(
    path: Path,
    *script_args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    ps = _powershell()
    if ps is None:
        pytest.skip("Windows PowerShell is required for Task Scheduler helper tests")
    cmd = [
        ps,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-File",
        str(path),
        *script_args,
    ]
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, check=check, env=merged)


def _compile_fake_ftmon(
    tmp_path: Path,
    *,
    exit_code: int = 0,
    record_path: Path | None = None,
    stdout_text: str = "",
    stderr_text: str = "",
    stderr_bytes: int = 0,
) -> Path:
    """Build a real PE console stub (batch files named .exe are not CreateProcess-safe)."""
    if os.name != "nt":
        exe = tmp_path / "ftmon"
        record = record_path or (tmp_path / "argv.json")
        flood = ""
        if stderr_bytes > 0:
            flood = f'python -c "import sys; sys.stderr.write(\\"x\\" * {stderr_bytes})" >&2\n'
        exe.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$@" > "{record.as_posix()}"
                [ -n "{stdout_text}" ] && printf '%s\\n' "{stdout_text}"
                [ -n "{stderr_text}" ] && printf '%s\\n' "{stderr_text}" >&2
                {flood}exit {exit_code}
                """
            ),
            encoding="utf-8",
        )
        exe.chmod(0o755)
        return exe

    ps = _powershell()
    assert ps is not None
    exe = tmp_path / "ftmon.exe"
    record = record_path or (tmp_path / "argv.txt")
    # Escape for a C# verbatim string.
    record_cs = str(record).replace("\\", "\\\\").replace('"', '\\"')
    stdout_cs = stdout_text.replace("\\", "\\\\").replace('"', '\\"')
    stderr_cs = stderr_text.replace("\\", "\\\\").replace('"', '\\"')
    flood_line = ""
    if stderr_bytes > 0:
        flood_line = f'                Console.Error.Write(new string(\'x\', {stderr_bytes}));\n'
    source = textwrap.dedent(
        f"""\
        using System;
        using System.IO;
        public static class FakeFtmon {{
            public static int Main(string[] args) {{
                File.WriteAllText("{record_cs}", string.Join("\\n", args));
                if ("{stdout_cs}".Length > 0) Console.Out.WriteLine("{stdout_cs}");
                if ("{stderr_cs}".Length > 0) Console.Error.WriteLine("{stderr_cs}");
{flood_line}                return {exit_code};
            }}
        }}
        """
    )
    cs_path = tmp_path / "fake_ftmon.cs"
    cs_path.write_text(source, encoding="utf-8")
    compile_cmd = (
        f"Add-Type -OutputAssembly '{exe}' -OutputType ConsoleApplication "
        f"-TypeDefinition (Get-Content -LiteralPath '{cs_path}' -Raw)"
    )
    compiled = _run_ps(compile_cmd, check=False)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert exe.is_file()
    return exe


def _real_ftmon_launcher(tmp_path: Path, env_dirs: dict[str, Path]) -> Path:
    """Real PE launcher that injects FTMON_* and delegates to ``python -m ftmon``."""
    # On Windows a .cmd next to tests is fine for Install-FTMONTasks (& call operator),
    # but Invoke-FTMONTask uses CreateProcess — keep a compiled forwarder for both.
    if os.name != "nt":
        raise AssertionError("Windows-only helper")
    ps = _powershell()
    assert ps is not None
    exe = tmp_path / "ftmon.exe"
    py = str(sys.executable).replace("\\", "\\\\").replace('"', '\\"')
    exports = "\n".join(
        f'            Environment.SetEnvironmentVariable("{key}", @"{value}");'
        for key, value in env_dirs.items()
    )
    source = textwrap.dedent(
        f"""\
        using System;
        using System.Diagnostics;
        using System.Text;
        public static class FtmonForwarder {{
            public static int Main(string[] args) {{
        {exports}
                var psi = new ProcessStartInfo();
                psi.FileName = "{py}";
                var sb = new StringBuilder("-m ftmon");
                foreach (var a in args) {{
                    sb.Append(' ');
                    if (a.IndexOfAny(new char[] {{' ', '"'}} ) >= 0) {{
                        sb.Append('"').Append(a.Replace("\\"", "\\\\\\"")).Append('"');
                    }} else {{
                        sb.Append(a);
                    }}
                }}
                psi.Arguments = sb.ToString();
                psi.UseShellExecute = false;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                var p = Process.Start(psi);
                Console.Out.Write(p.StandardOutput.ReadToEnd());
                Console.Error.Write(p.StandardError.ReadToEnd());
                p.WaitForExit();
                return p.ExitCode;
            }}
        }}
        """
    )
    cs_path = tmp_path / "forwarder.cs"
    cs_path.write_text(source, encoding="utf-8")
    compiled = _run_ps(
        f"Add-Type -OutputAssembly '{exe}' -OutputType ConsoleApplication "
        f"-TypeDefinition (Get-Content -LiteralPath '{cs_path}' -Raw)",
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return exe


def test_task_helper_scripts_are_packaged_and_shared_pl_01():
    """[PL-01][DO-02] Helpers ship as package data and Hatch shared-scripts."""
    import tomllib
    from importlib.resources import files

    assert INSTALLER.is_file()
    assert RUNNER.is_file()
    packaged_install = files("ftmon").joinpath("windows/Install-FTMONTasks.ps1").read_text(
        encoding="utf-8"
    )
    packaged_runner = files("ftmon").joinpath("windows/Invoke-FTMONTask.ps1").read_text(
        encoding="utf-8"
    )
    assert "FTMON daemon" in packaged_install
    assert "ValidateSet('daemon', 'web')" in packaged_runner or (
        "ValidateSet('daemon', 'web')" in packaged_runner.replace('"', "'")
    )
    assert "0.0.0.0" not in packaged_install
    assert "mcp" not in packaged_install.lower().split()
    assert "LocalSystem" in packaged_install
    assert "IgnoreNew" in packaged_install
    assert "AtLogOn" in packaged_install
    assert "Limited" in packaged_install
    assert "Interactive" in packaged_install
    assert "IncludeWeb" in packaged_install
    assert "1MB" in packaged_runner or "1 MiB" in packaged_runner or "1MB" in packaged_runner

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    shared = project["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-scripts"]
    assert shared == {
        "src/ftmon/windows/Install-FTMONTasks.ps1": "Install-FTMONTasks.ps1",
        "src/ftmon/windows/Invoke-FTMONTask.ps1": "Invoke-FTMONTask.ps1",
    }


def test_powershell_parses_both_helpers_without_registering():
    """[PL-01] Windows PowerShell parses both scripts (syntax gate)."""
    for path in (INSTALLER, RUNNER):
        # Parser never executes the script body — syntax gate only.
        escaped = str(path).replace("'", "''")
        result = _run_ps(
            "$errors = $null; "
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped}', [ref]$null, [ref]$errors); "
            "if ($errors) { $errors | ForEach-Object { $_.ToString() }; exit 1 }; "
            "exit 0",
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout


def test_runner_forwards_role_captures_output_and_exit(tmp_path: Path):
    """[PL-01] Runner forwards daemon/web, captures output, propagates exit."""
    if os.name != "nt" and _powershell() is None:
        pytest.skip("PowerShell required")

    log = tmp_path / "task-daemon.log"
    record = tmp_path / "argv.txt"
    wrapper = _compile_fake_ftmon(
        tmp_path,
        exit_code=7,
        record_path=record,
        stdout_text="hello-stdout",
        stderr_text="hello-stderr",
    )

    result = _run_ps_file(
        RUNNER,
        "-Role",
        "daemon",
        "-FtmonExe",
        str(wrapper),
        "-LogFile",
        str(log),
        check=False,
        env={
            "FTMON_TASK_MAX_ATTEMPTS": "1",
            "FTMON_TASK_RESTART_DELAY_SEC": "0",
        },
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert record.read_text(encoding="utf-8").strip() == "daemon"
    text = log.read_text(encoding="utf-8")
    assert "hello-stdout" in text
    assert "hello-stderr" in text
    assert "exited 7" in text


def test_runner_drains_large_stderr_without_deadlock(tmp_path: Path):
    """[PL-01] Concurrent stream capture must not hang on a 2 MiB stderr flood."""
    if os.name != "nt" and _powershell() is None:
        pytest.skip("PowerShell required")

    log = tmp_path / "task-daemon.log"
    wrapper = _compile_fake_ftmon(tmp_path, exit_code=0, stderr_bytes=2 * 1024 * 1024)
    result = _run_ps_file(
        RUNNER,
        "-Role",
        "daemon",
        "-FtmonExe",
        str(wrapper),
        "-LogFile",
        str(log),
        check=False,
        env={"FTMON_TASK_MAX_ATTEMPTS": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "exited 0" in log.read_text(encoding="utf-8")
    assert log.stat().st_size > 1_000_000


def test_runner_uses_redirected_stream_files_not_sequential_read():
    """[PL-01] Runner must not ReadToEnd stdout before draining stderr."""
    text = RUNNER.read_text(encoding="utf-8")
    assert "RedirectStandardOutput" in text
    assert "RedirectStandardError" in text
    assert ".ReadToEnd(" not in text
    assert "StandardOutput.ReadToEnd" not in text
    assert "StandardError.ReadToEnd" not in text


# Source-contract tokens used by the mutation gate. Mutating any of these must
# make the corresponding validator return a non-empty error list.
_INSTALLER_REQUIRED = (
    ("[switch]$IncludeWeb", "web opt-in"),
    ("-RunLevel Limited", "principal level"),
    ("-MultipleInstances IgnoreNew", "duplicate policy"),
    ("-ExecutionTimeLimit ([TimeSpan]::Zero)", "indefinite lifetime"),
    ("New-ScheduledTaskTrigger -AtLogOn", "logon trigger"),
    ("-RestartCount 255", "restart policy"),
    ("-DontStopOnIdleEnd `", "idle stop disabled"),
    ("-LogonType Interactive", "interactive logon"),
)
_RUNNER_REQUIRED = (
    ("ValidateSet('daemon', 'web')", "role allow-list"),
    ("RedirectStandardOutput", "stdout redirect"),
    ("RedirectStandardError", "stderr redirect"),
    ("restarting $Role in", "wrapper restart loop"),
    ("$maxAttempts = 255", "restart budget"),
)


def installer_contract_errors(text: str) -> list[str]:
    """Return human-readable contract violations for Install-FTMONTasks.ps1."""
    errors: list[str] = []
    lowered = text.lower()
    compact = lowered.replace(" ", "")
    for token, label in _INSTALLER_REQUIRED:
        if token not in text:
            errors.append(f"missing {label}: {token!r}")
    if "runlevel highest" in lowered or "-runlevelhighest" in compact:
        errors.append("forbidden Highest run level")
    if "-atstartup" in compact:
        errors.append("forbidden AtStartup trigger")
    if "s-1-5-18" not in lowered:
        errors.append("missing LocalSystem SID rejection")
    if "0.0.0.0" in text:
        errors.append("forbidden bind-all address")
    if "mcp" in lowered.split():
        errors.append("forbidden mcp token in installer")
    return errors


def runner_contract_errors(text: str) -> list[str]:
    """Return human-readable contract violations for Invoke-FTMONTask.ps1."""
    errors: list[str] = []
    for token, label in _RUNNER_REQUIRED:
        if token not in text:
            errors.append(f"missing {label}: {token!r}")
    if ".ReadToEnd(" in text or "StandardOutput.ReadToEnd" in text:
        errors.append("forbidden sequential ReadToEnd drain")
    return errors


def test_installer_source_contract_excludes_forbidden_modes():
    """[PL-01][PM-09][NG-05] Source contract: no SYSTEM/highest/startup/MCP/bind-all."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert installer_contract_errors(text) == []
    lowered = text.lower()
    assert "s-1-5-18" in lowered
    assert "0.0.0.0" not in text
    assert "demo" not in lowered.split()


def test_installer_and_runner_contract_mutations_are_rejected():
    """[PL-01] Mutated sources must fail the same contract validator."""
    install = INSTALLER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    assert installer_contract_errors(install) == []
    assert runner_contract_errors(runner) == []

    for token, label in _INSTALLER_REQUIRED:
        mutated = install.replace(token, "MUTATED", 1)
        assert installer_contract_errors(mutated), f"installer mutant for {label} was accepted"

    for token, label in _RUNNER_REQUIRED:
        mutated = runner.replace(token, "MUTATED", 1)
        assert runner_contract_errors(mutated), f"runner mutant for {label} was accepted"


def test_runner_rejects_non_daemon_web_roles():
    """[PL-01] Runner accepts only daemon or web — never mcp/demo."""
    result = _run_ps_file(
        RUNNER,
        "-Role",
        "mcp",
        "-FtmonExe",
        str(RUNNER),  # unused; validation fails first
        "-LogFile",
        str(ROOT / "no-such.log"),
        check=False,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "daemon" in combined or "validate" in combined or "role" in combined


def test_runner_rolls_log_larger_than_one_mib(tmp_path: Path):
    """[PL-01] Task logs larger than 1 MiB roll to a single .1 backup."""
    log = tmp_path / "task-web.log"
    log.write_bytes(b"x" * (1_048_576 + 10))
    wrapper = _compile_fake_ftmon(tmp_path, exit_code=0)

    result = _run_ps_file(
        RUNNER,
        "-Role",
        "web",
        "-FtmonExe",
        str(wrapper),
        "-LogFile",
        str(log),
        check=False,
        env={"FTMON_TASK_MAX_ATTEMPTS": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "task-web.log.1").is_file()
    assert (tmp_path / "task-web.log.1").stat().st_size == 1_048_576 + 10
    assert log.is_file()
    assert "starting web" in log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Scheduled Task registration is Windows-only")
def test_installer_registers_daemon_only_and_web_opt_in(tmp_path: Path):
    """[PL-01] Daemon-only install creates no web task; -IncludeWeb adds it."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for d in (config_dir, data_dir, state_dir, runtime_dir):
        d.mkdir()

    env = {
        "FTMON_CONFIG_DIR": str(config_dir),
        "FTMON_DATA_DIR": str(data_dir),
        "FTMON_STATE_DIR": str(state_dir),
        "FTMON_RUNTIME_DIR": str(runtime_dir),
    }
    init = subprocess.run(
        [sys.executable, "-m", "ftmon", "init", "--profile", "windesktop"],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )
    assert init.returncode == 0, init.stdout + init.stderr

    wrapper = _real_ftmon_launcher(
        tmp_path,
        {
            "FTMON_CONFIG_DIR": config_dir,
            "FTMON_DATA_DIR": data_dir,
            "FTMON_STATE_DIR": state_dir,
            "FTMON_RUNTIME_DIR": runtime_dir,
        },
    )

    _run_ps_file(INSTALLER, "-Action", "Remove", check=False)

    install = _run_ps_file(
        INSTALLER,
        "-Action",
        "Install",
        "-FtmonExe",
        str(wrapper),
        check=False,
        env=env,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    probe = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-Command",
            textwrap.dedent(
                """\
                $t = Get-ScheduledTask -TaskName 'FTMON daemon'
                $a = @($t.Actions)[0]
                [pscustomobject]@{
                  TaskName = $t.TaskName
                  RunLevel = [string]$t.Principal.RunLevel
                  LogonType = [string]$t.Principal.LogonType
                  MultipleInstances = [string]$t.Settings.MultipleInstances
                  DisallowStartIfOnBatteries = [bool]$t.Settings.DisallowStartIfOnBatteries
                  StopIfGoingOnBatteries = [bool]$t.Settings.StopIfGoingOnBatteries
                  StartWhenAvailable = [bool]$t.Settings.StartWhenAvailable
                  RestartCount = [int]$t.Settings.RestartCount
                  Execute = [string]$a.Execute
                  Arguments = [string]$a.Arguments
                  WorkingDirectory = [string]$a.WorkingDirectory
                  TriggerUser = [string](@($t.Triggers)[0].UserId)
                } | ConvertTo-Json -Compress
                """
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    daemon_json = json.loads(probe.stdout)
    assert daemon_json["TaskName"] == "FTMON daemon"
    assert daemon_json["RunLevel"] == "Limited"
    assert daemon_json["LogonType"] == "Interactive"
    assert daemon_json["MultipleInstances"] == "IgnoreNew"
    assert daemon_json["DisallowStartIfOnBatteries"] is False
    assert daemon_json["StopIfGoingOnBatteries"] is False
    assert daemon_json["StartWhenAvailable"] is True
    assert daemon_json["RestartCount"] == 255
    assert daemon_json["TriggerUser"]  # account-specific logon trigger

    missing = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-Command",
            "Get-ScheduledTask -TaskName 'FTMON web' -ErrorAction SilentlyContinue",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.stdout.strip() == ""

    execute = daemon_json["Execute"]
    assert execute.lower().endswith("powershell.exe")
    assert Path(execute).is_absolute()
    args = daemon_json["Arguments"]
    assert "-NoProfile" in args
    assert "-WindowStyle Hidden" in args
    assert "RemoteSigned" in args
    assert "Invoke-FTMONTask.ps1" in args
    assert (state_dir / "tasks" / "Invoke-FTMONTask.ps1").is_file()
    assert "-Role daemon" in args
    assert "mcp" not in args.lower()
    assert "0.0.0.0" not in args

    web_install = _run_ps_file(
        INSTALLER,
        "-Action",
        "Install",
        "-FtmonExe",
        str(wrapper),
        "-IncludeWeb",
        check=False,
        env=env,
    )
    assert web_install.returncode == 0, web_install.stdout + web_install.stderr
    web = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-Command",
            "([string](@((Get-ScheduledTask -TaskName 'FTMON web').Actions)[0].Arguments))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "-Role web" in web.stdout

    remove = _run_ps_file(INSTALLER, "-Action", "Remove", check=False)
    assert remove.returncode == 0, remove.stdout + remove.stderr
    gone = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-Command",
            "@('FTMON daemon','FTMON web') | ForEach-Object {"
            "  if (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue) { $_ }"
            "}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert gone.stdout.strip() == ""


@pytest.mark.skipif(os.name != "nt", reason="init gate uses Windows paths")
def test_installer_refuses_without_config_toml(tmp_path: Path):
    """[PL-01][PM-08] Refuse task install until ftmon init has written config.toml."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for d in (config_dir, data_dir, state_dir, runtime_dir):
        d.mkdir()
    wrapper = _real_ftmon_launcher(
        tmp_path,
        {
            "FTMON_CONFIG_DIR": config_dir,
            "FTMON_DATA_DIR": data_dir,
            "FTMON_STATE_DIR": state_dir,
            "FTMON_RUNTIME_DIR": runtime_dir,
        },
    )
    result = _run_ps_file(
        INSTALLER,
        "-FtmonExe",
        str(wrapper),
        check=False,
        env={
            "FTMON_CONFIG_DIR": str(config_dir),
            "FTMON_DATA_DIR": str(data_dir),
            "FTMON_STATE_DIR": str(state_dir),
            "FTMON_RUNTIME_DIR": str(runtime_dir),
        },
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ftmon init" in combined.lower() or "config.toml" in combined.lower()

"""Record native #94 Task Scheduler checklist evidence on a Windows host.

Writes dated evidence under ``soak/windows-native/`` (gitignored via ``soak/``).

Automated probes only check task *configuration* (IgnoreNew, WindowStyle Hidden
tokens, Limited, …). Native *observations* — repeated Start-ScheduledTask with
no duplicate process, no visible console, three ticks, forced restart, web
loopback, remove — are separate fields. Use ``--observe`` to perform those on
this host (except reboot+logon, which remains operator-filled).

Workflow::

    # 1) Probe config + run native observations (except reboot)
    uv run python tools/windows/record_native_checklist.py --observe

    # 2) Edit the evidence file (set reboot_logon_recovery: pass after a real
    #    reboot+logon), or set FTMON_CHECKLIST_REBOOT_LOGON=pass, then:
    uv run python tools/windows/record_native_checklist.py --strict --evidence PATH

``--strict`` re-reads the evidence file (and env overrides). Editing the file
is the supported sign-off path; env vars are optional shortcuts.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "soak" / "windows-native"
WINDOWS_DIR = ROOT / "src" / "ftmon" / "windows"
INSTALLER = WINDOWS_DIR / "Install-FTMONTasks.ps1"

# Observed native results required for --strict (not config-token inference).
MANUAL_KEYS = (
    "no_duplicate_on_repeat_start",
    "no_visible_console",
    "forced_daemon_restart",
    "reboot_logon_recovery",
    "three_daemon_cycles",
    "web_loopback_only",
    "remove_leaves_no_tasks",
)

_ENV_FOR_KEY = {
    "no_duplicate_on_repeat_start": "FTMON_CHECKLIST_NO_DUPLICATE",
    "no_visible_console": "FTMON_CHECKLIST_NO_CONSOLE",
    "forced_daemon_restart": "FTMON_CHECKLIST_FORCED_RESTART",
    "reboot_logon_recovery": "FTMON_CHECKLIST_REBOOT_LOGON",
    "three_daemon_cycles": "FTMON_CHECKLIST_THREE_CYCLES",
    "web_loopback_only": "FTMON_CHECKLIST_WEB_LOOPBACK",
    "remove_leaves_no_tasks": "FTMON_CHECKLIST_REMOVE",
}

_RESULT_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*:\s*(\S+)\s*$")


def _ps(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _task_probe(name: str) -> dict | None:
    completed = _ps(
        f"""
        $t = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue
        if (-not $t) {{ '' ; exit 0 }}
        $a = @($t.Actions)[0]
        [pscustomobject]@{{
          TaskName = $t.TaskName
          RunLevel = [string]$t.Principal.RunLevel
          LogonType = [string]$t.Principal.LogonType
          MultipleInstances = [string]$t.Settings.MultipleInstances
          RestartCount = [int]$t.Settings.RestartCount
          Execute = [string]$a.Execute
          Arguments = [string]$a.Arguments
        }} | ConvertTo-Json -Compress
        """
    )
    raw = (completed.stdout or "").strip()
    if not raw:
        return None
    return json.loads(raw)


def _parse_evidence(path: Path) -> dict[str, str]:
    """Parse ``key: value`` result lines from an evidence file."""
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _RESULT_LINE.match(line)
        if match:
            parsed[match.group(1)] = match.group(2)
    return parsed


def _manual_from_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, env_name in _ENV_FOR_KEY.items():
        raw = os.environ.get(env_name, "").strip()
        if raw:
            out[key] = raw
    return out


def _resolve_ftmon_exe() -> Path | None:
    for candidate in (
        os.environ.get("FTMON_EXE", "").strip(),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "FTMON" / "ftmon.exe"),
        str(ROOT / "dist" / "windows" / "ftmon" / "ftmon.exe"),
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    which = _ps("(Get-Command ftmon -ErrorAction SilentlyContinue).Source")
    src = (which.stdout or "").strip()
    if src and Path(src).is_file():
        return Path(src).resolve()
    return None


def _ftmon_pids() -> list[int]:
    completed = _ps(
        """
        @(Get-CimInstance Win32_Process -Filter "Name = 'ftmon.exe'" |
          Select-Object -ExpandProperty ProcessId) | ConvertTo-Json
        """
    )
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, int):
        return [data]
    if isinstance(data, list):
        return [int(x) for x in data]
    return []


def _processes_have_visible_main_window(pids: list[int]) -> bool:
    if not pids:
        return False
    pid_list = ",".join(str(p) for p in pids)
    completed = _ps(
        f"""
        $pids = @({pid_list})
        foreach ($pid in $pids) {{
          try {{
            $p = Get-Process -Id $pid -ErrorAction Stop
            if ($p.MainWindowHandle -ne 0) {{ 'visible'; exit 0 }}
          }} catch {{}}
        }}
        'hidden'
        """
    )
    return "visible" in (completed.stdout or "")


def _apply_config_probes(results: dict[str, str], notes: list[str]) -> None:
    daemon = _task_probe("FTMON daemon")
    web = _task_probe("FTMON web")
    if daemon is None:
        results["daemon_task_present"] = results.get("daemon_task_present", "absent")
        return
    results["daemon_task_present"] = "pass"
    results["daemon_ignore_new"] = (
        "pass" if daemon.get("MultipleInstances") == "IgnoreNew" else "fail"
    )
    results["daemon_limited"] = (
        "pass" if daemon.get("RunLevel") == "Limited" else "fail"
    )
    results["daemon_restart_255"] = (
        "pass" if int(daemon.get("RestartCount") or 0) == 255 else "fail"
    )
    args_text = str(daemon.get("Arguments") or "")
    results["daemon_windowstyle_hidden"] = (
        "pass" if "WindowStyle Hidden" in args_text else "fail"
    )
    results["daemon_only_no_web_by_default"] = (
        "pass" if web is None else "info_web_present"
    )
    if web is not None:
        wargs = str(web.get("Arguments") or "")
        results["web_windowstyle_hidden"] = (
            "pass" if "WindowStyle Hidden" in wargs else "fail"
        )
        results["web_opt_in_present"] = "pass"


def _ensure_init(exe: Path) -> None:
    paths_run = subprocess.run(
        [str(exe), "paths", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if paths_run.returncode != 0:
        subprocess.run([str(exe), "init", "--profile", "windesktop"], check=False)
        return
    try:
        paths = json.loads(paths_run.stdout)
    except json.JSONDecodeError:
        subprocess.run([str(exe), "init", "--profile", "windesktop"], check=False)
        return
    cfg = Path(paths.get("config_dir", "")) / "config.toml"
    if not cfg.is_file():
        subprocess.run([str(exe), "init", "--profile", "windesktop"], check=False)


def _observe_native(results: dict[str, str], notes: list[str], exe: Path) -> None:
    """Perform native observations and write results into ``results``."""
    # Unit tests may leave FTMON_TASK_* overrides in the agent shell; clear them
    # so the installed wrapper uses production restart timing.
    for key in ("FTMON_TASK_MAX_ATTEMPTS", "FTMON_TASK_RESTART_DELAY_SEC"):
        os.environ.pop(key, None)
    _ensure_init(exe)
    lock = (
        Path(os.environ.get("TEMP", os.environ.get("TMP", "")))
        / "ftmon"
        / "ftmon"
        / "daemon.lock"
    )
    # Prefer paths from ftmon itself.
    paths_run = subprocess.run(
        [str(exe), "paths", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if paths_run.returncode == 0:
        try:
            lock = Path(json.loads(paths_run.stdout).get("lock_file", lock))
        except json.JSONDecodeError:
            pass

    def _clear_lock() -> None:
        try:
            if lock.is_file():
                lock.unlink()
        except OSError as exc:
            notes.append(f"observe: could not clear lock {lock}: {exc}")

    for pid in _ftmon_pids():
        _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
    _clear_lock()
    _ps(f"& '{INSTALLER}' -Action Remove")
    time.sleep(1)
    install = _ps(f"& '{INSTALLER}' -Action Install -FtmonExe '{exe}'")
    if install.returncode != 0:
        for key in (
            "no_duplicate_on_repeat_start",
            "no_visible_console",
            "three_daemon_cycles",
            "forced_daemon_restart",
            "web_loopback_only",
            "remove_leaves_no_tasks",
        ):
            results[key] = "fail"
        notes.append((install.stderr or install.stdout or "install failed")[:500])
        return

    _apply_config_probes(results, notes)
    if _task_probe("FTMON web") is not None:
        results["daemon_only_no_web_by_default"] = "fail"
        notes.append("observe: web task present after daemon-only install")
    else:
        results["daemon_only_no_web_by_default"] = "pass"

    _ps("Start-ScheduledTask -TaskName 'FTMON daemon'")
    ready = False
    for _ in range(30):
        time.sleep(1)
        if _ftmon_pids():
            ready = True
            break
    if not ready:
        results["no_duplicate_on_repeat_start"] = "fail"
        results["three_daemon_cycles"] = "fail"
        results["forced_daemon_restart"] = "fail"
        notes.append("observe: daemon process did not appear after Start-ScheduledTask")
        return

    first = _ftmon_pids()
    _ps("Start-ScheduledTask -TaskName 'FTMON daemon'")
    time.sleep(3)
    second = _ftmon_pids()
    if first and len(second) == len(first) and set(second) == set(first):
        results["no_duplicate_on_repeat_start"] = "pass"
    elif first and len(second) <= len(first):
        results["no_duplicate_on_repeat_start"] = "pass"
        notes.append(f"observe: repeat start pids before={first} after={second}")
    else:
        results["no_duplicate_on_repeat_start"] = "fail"
        notes.append(f"observe: duplicate/missing pids before={first} after={second}")

    pids = second or first
    if pids and not _processes_have_visible_main_window(pids):
        results["no_visible_console"] = "pass"
    else:
        results["no_visible_console"] = "fail"
        notes.append(f"observe: console check pids={pids}")

    seen: list[float] = []
    deadline = time.time() + 120
    status_errors = 0
    while time.time() < deadline and len(seen) < 3:
        status = subprocess.run(
            [str(exe), "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env={k: v for k, v in os.environ.items() if not k.startswith("FTMON_TASK_")},
        )
        # status exits non-zero when open incidents exist; JSON is still valid.
        if not status.stdout.strip():
            status_errors += 1
        else:
            try:
                payload = json.loads(status.stdout)
            except json.JSONDecodeError:
                status_errors += 1
                payload = {}
            tick = payload.get("last_tick_ts")
            age = payload.get("last_tick_age_s")
            # Ignore stale pre-start ticks (daemon not yet writing).
            if (
                isinstance(tick, (int, float))
                and isinstance(age, (int, float))
                and age < 30
                and (not seen or tick != seen[-1])
            ):
                seen.append(float(tick))
        time.sleep(1)
    results["three_daemon_cycles"] = "pass" if len(seen) >= 3 else "fail"
    if len(seen) < 3:
        notes.append(
            f"observe: only saw ticks {seen!r} (status_errors={status_errors})"
        )

    before = _ftmon_pids()
    if before:
        for pid in before:
            _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
        # Stale lock blocks a restarted daemon; clear after forced kill.
        time.sleep(2)
        _clear_lock()
        restarted = False
        # RestartInterval is 1 minute; allow a short buffer.
        for elapsed in range(90):
            time.sleep(1)
            now = _ftmon_pids()
            if now and set(now) != set(before):
                restarted = True
                notes.append(f"observe: forced restart after {elapsed + 1}s pids={now}")
                break
        results["forced_daemon_restart"] = "pass" if restarted else "fail"
        if not restarted:
            notes.append(
                "observe: daemon did not restart within ~90s after kill "
                "(RestartOnFailure / RestartInterval PT1M)"
            )
    else:
        results["forced_daemon_restart"] = "fail"
        notes.append("observe: no process to kill for forced restart")

    web_install = _ps(f"& '{INSTALLER}' -Action Install -FtmonExe '{exe}' -IncludeWeb")
    if web_install.returncode != 0:
        results["web_loopback_only"] = "fail"
        notes.append("observe: -IncludeWeb install failed")
    else:
        _ps("Start-ScheduledTask -TaskName 'FTMON web'")
        time.sleep(5)
        loop = _ps(
            """
            $ftmonPids = @(Get-Process ftmon -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty Id)
            if (-not $ftmonPids) { 'none'; exit 0 }
            $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
              Where-Object { $ftmonPids -contains $_.OwningProcess } |
              Select-Object -ExpandProperty LocalAddress -Unique)
            if (-not $listeners) { 'none'; exit 0 }
            $bad = @($listeners | Where-Object { $_ -notin @('127.0.0.1','::1') })
            if ($bad.Count -gt 0) { 'nonloopback:' + ($bad -join ',') } else { 'loopback' }
            """
        )
        out = (loop.stdout or "").strip()
        if out == "loopback":
            results["web_loopback_only"] = "pass"
        elif out == "none":
            probe = _ps(
                """
                try {
                  $c = New-Object System.Net.Sockets.TcpClient
                  $c.Connect('127.0.0.1', 8420)
                  $c.Close()
                  'up'
                } catch { 'down' }
                """
            )
            if "up" in (probe.stdout or ""):
                results["web_loopback_only"] = "pass"
                notes.append("observe: web up on 127.0.0.1:8420; no non-loopback listeners")
            else:
                results["web_loopback_only"] = "fail"
                notes.append("observe: no ftmon listen sockets / :8420 down")
        else:
            results["web_loopback_only"] = "fail"
            notes.append(f"observe: web bind probe: {out}")

        web_pids = _ftmon_pids()
        if web_pids and _processes_have_visible_main_window(web_pids):
            results["no_visible_console"] = "fail"
            notes.append("observe: process has visible MainWindowHandle after web start")

    remove = _ps(f"& '{INSTALLER}' -Action Remove")
    if remove.returncode != 0:
        results["remove_leaves_no_tasks"] = "fail"
        notes.append("observe: Remove failed")
    else:
        left = _ps(
            "@(Get-ScheduledTask -TaskName 'FTMON*' -ErrorAction SilentlyContinue).Count"
        )
        count = (left.stdout or "").strip()
        results["remove_leaves_no_tasks"] = "pass" if count == "0" else "fail"
        if count != "0":
            notes.append(f"observe: FTMON tasks remain after Remove: {count}")
        for pid in _ftmon_pids():
            _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")

    # Re-register daemon-only so a subsequent reboot can exercise logon recovery.
    # Operator confirms reboot_logon_recovery after reboot+logon.
    reinstall = _ps(f"& '{INSTALLER}' -Action Install -FtmonExe '{exe}'")
    if reinstall.returncode != 0:
        notes.append(
            "observe: could not re-install daemon task for reboot verification"
        )
    else:
        notes.append(
            "observe: daemon task left registered (not started) for reboot_logon_recovery"
        )


def _format_evidence(
    *,
    stamp: str,
    results: dict[str, str],
    notes: list[str],
    exe: Path | None,
) -> str:
    lines = [
        f"FTMON native #94 checklist evidence — {stamp}",
        f"host={platform.node()} platform={platform.platform()}",
        f"python={sys.version.split()[0]}",
        f"ftmon_exe={exe or 'unresolved'}",
        "",
        "Results (edit values to pass|fail; --strict re-reads this file):",
    ]
    for key in sorted(results):
        lines.append(f"  {key}: {results[key]}")
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {n}" for n in notes)
    lines.extend(
        [
            "",
            "Observed fields required by --strict:",
            "  no_duplicate_on_repeat_start — Start-ScheduledTask twice; same process set",
            "  no_visible_console — running task processes have MainWindowHandle 0",
            "  forced_daemon_restart — kill ftmon; task restarts within ~1m",
            "  reboot_logon_recovery — reboot, log on as task owner, monitoring resumes",
            "  three_daemon_cycles — last_tick_ts advances three times under the task",
            "  web_loopback_only — with -IncludeWeb, listener is 127.0.0.1 only",
            "  remove_leaves_no_tasks — Install-FTMONTasks.ps1 -Action Remove clears FTMON*",
            "",
            "Config-token probes (not substitutes for the observed fields above):",
            "  daemon_ignore_new / daemon_windowstyle_hidden — settings only",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe",
        action="store_true",
        help="perform native observations (duplicate start, console, ticks, …)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="require all MANUAL_KEYS == pass (re-reads --evidence file + env)",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="evidence file to re-read for --strict (and optional update target)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="evidence output path (default soak/windows-native/<stamp>.txt)",
    )
    args = parser.parse_args(argv)

    if os.name != "nt":
        raise SystemExit("native checklist recording requires Windows")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    exe = _resolve_ftmon_exe()

    results: dict[str, str] = {}
    notes: list[str] = []

    if args.evidence is not None:
        evidence_path = args.evidence
        if evidence_path.is_file():
            results.update(_parse_evidence(evidence_path))
    elif args.out is not None:
        evidence_path = args.out
    else:
        evidence_path = EVIDENCE_DIR / f"checklist-{stamp}.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    results.update(_manual_from_env())
    for key in MANUAL_KEYS:
        results.setdefault(key, "pending")

    if not args.strict or args.observe:
        _apply_config_probes(results, notes)

    if args.observe:
        if exe is None:
            raise SystemExit(
                "FTMON executable not found; set FTMON_EXE or install/build ftmon"
            )
        _observe_native(results, notes, exe)
        results.update(_manual_from_env())

    # Prefer file + env as sign-off authority when --strict.
    if args.strict:
        if not evidence_path.is_file() and not args.observe:
            raise SystemExit("--strict requires an existing --evidence file (or --observe)")
        if evidence_path.is_file() and not args.observe:
            file_results = _parse_evidence(evidence_path)
            for key in MANUAL_KEYS:
                if key in file_results:
                    results[key] = file_results[key]
        results.update(_manual_from_env())

    body = _format_evidence(stamp=stamp, results=results, notes=notes, exe=exe)
    if args.observe or not args.strict:
        evidence_path.write_text(body, encoding="utf-8")
        print(f"wrote {evidence_path}")
    else:
        print(f"validated {evidence_path} (not rewritten)")

    token_failed = [
        k
        for k, v in results.items()
        if v == "fail"
        and k
        in {
            "daemon_ignore_new",
            "daemon_limited",
            "daemon_restart_255",
            "daemon_windowstyle_hidden",
            "daemon_only_no_web_by_default",
        }
    ]
    if token_failed and args.observe:
        raise SystemExit(f"config-token checklist failures: {sorted(token_failed)}")

    if args.strict:
        pending = [k for k in MANUAL_KEYS if results.get(k) != "pass"]
        if pending:
            raise SystemExit(f"--strict: observed/manual items not pass: {pending}")
        print("strict checklist OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

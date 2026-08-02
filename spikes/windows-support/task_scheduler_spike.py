"""Spike: can we programmatically register a per-user logon-trigger Task
Scheduler task (the PL-01 service-wrapper seam for Windows) without admin
rights, via both `schtasks.exe` and the Schedule.Service COM API? And what,
if anything, does Task Scheduler offer as a PM-11 (SIGHUP) reload
equivalent?

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\task_scheduler_spike.py
"""

import subprocess
import sys

import pywintypes
import win32com.client

TASK_NAME = "FTMON-Spike-COM"
TASK_TRIGGER_LOGON = 9
TASK_ACTION_EXEC = 0
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3


def hresult_of(exc: pywintypes.com_error) -> str:
    try:
        code = exc.excepinfo[5]
    except (AttributeError, IndexError, TypeError):
        code = exc.args[0]
    return hex(code & 0xFFFFFFFF)


def try_com_api() -> None:
    print("=== Schedule.Service COM API ===")
    scheduler = win32com.client.Dispatch("Schedule.Service")
    scheduler.Connect()
    print("Connect(): OK")

    try:
        root = scheduler.GetFolder("\\")
        print(f"GetFolder('\\\\'): OK, {root.GetTasks(0).Count} existing task(s) visible")
    except pywintypes.com_error as exc:
        print(f"GetFolder('\\\\') FAILED: hresult={hresult_of(exc)} {exc}")
        print("(read access to the root folder itself is denied -- this blocks the")
        print(" COM path entirely, independent of whether registration would work)")
        return

    td = scheduler.NewTask(0)
    td.Triggers.Create(TASK_TRIGGER_LOGON)
    action = td.Actions.Create(TASK_ACTION_EXEC)
    action.Path = sys.executable
    action.Arguments = "-c \"print('ftmon spike task fired')\""
    td.RegistrationInfo.Description = "ftmon spike -- safe to delete"

    try:
        root.RegisterTaskDefinition(
            TASK_NAME, td, TASK_CREATE_OR_UPDATE, None, None, TASK_LOGON_INTERACTIVE_TOKEN
        )
        print(f"RegisterTaskDefinition('{TASK_NAME}'): OK")
        root.DeleteTask(TASK_NAME, 0)
        print("DeleteTask: OK (cleaned up)")
    except pywintypes.com_error as exc:
        print(f"RegisterTaskDefinition FAILED: hresult={hresult_of(exc)} {exc}")


def try_schtasks_cli() -> None:
    print("\n=== schtasks.exe CLI ===")
    name = "FTMON-Spike-CLI"
    create = subprocess.run(
        [
            "schtasks",
            "/create",
            "/tn",
            name,
            "/tr",
            f'"{sys.executable}" -c "print(1)"',
            "/sc",
            "onlogon",
            "/rl",
            "limited",
            "/f",
        ],
        capture_output=True,
        text=True,
    )
    print(f"schtasks /create exit={create.returncode}")
    print(f"  stdout: {create.stdout.strip()}")
    print(f"  stderr: {create.stderr.strip()}")
    if create.returncode == 0:
        subprocess.run(["schtasks", "/delete", "/tn", name, "/f"], capture_output=True, text=True)
        print("  cleaned up")


def main() -> None:
    try_com_api()
    try_schtasks_cli()

    print("\n=== PM-11 (SIGHUP) reload-equivalent via Task Scheduler ===")
    print("Task Scheduler has no concept of 'signal a running task instance to")
    print("reload its config'. Its only verbs on a task are: Run (start a new")
    print("instance -- irrelevant if PM-02's single-instance lock is already")
    print("held by the running daemon, since the new instance would just exit),")
    print("End, Disable/Enable, and trigger-based (re)start. None of these reach")
    print("into the running process the way SIGHUP does on Linux.")
    print("=> The reload path (CL-07 'ftmon monitor rescan') needs its own")
    print("   cross-process primitive independent of the service wrapper --")
    print("   see reload_signal_spike.py for a named-Event-object candidate.")


if __name__ == "__main__":
    main()

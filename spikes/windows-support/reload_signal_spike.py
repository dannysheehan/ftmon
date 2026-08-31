"""Spike: candidate PM-11 (SIGHUP) reload-equivalent for Windows -- a named
Win32 Event object. The daemon creates+owns a named event at startup and
polls it with a zero-timeout wait each tick (mirroring PM-11's requirement
that the signal handler only records a flag: no filesystem/DB access in the
"handler"). A second process (the CLI's `ftmon monitor rescan` equivalent)
opens the same name and sets it.

Run as the "daemon" (creates + waits on the event, prints when it fires):

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\reload_signal_spike.py --daemon-role

Then, from a second terminal (or let this script spawn both roles itself
with no args):

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\reload_signal_spike.py --signal-role
"""

import subprocess
import sys
import time

import pywintypes
import win32api
import win32event

EVENT_NAME = "Global\\ftmon-spike-reload-event"


def daemon_role(seconds: float = 10.0) -> None:
    handle = win32event.CreateEvent(None, False, False, EVENT_NAME)
    already_existed = win32api.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    print(f"[daemon] CreateEvent('{EVENT_NAME}') ok, already_existed={already_existed}")
    print("[daemon] simulating tick loop, polling with 0 timeout each 'tick' ...")
    deadline = time.monotonic() + seconds
    ticks = 0
    fired_at_tick = None
    while time.monotonic() < deadline:
        ticks += 1
        # This is the PM-11-equivalent check: cheap, no I/O, just a kernel wait
        # with zero timeout -- safe to call every tick.
        result = win32event.WaitForSingleObject(handle, 0)
        if result == win32event.WAIT_OBJECT_0:
            fired_at_tick = ticks
            print(f"[daemon] reload requested (observed at tick {ticks}) -- would now")
            print("[daemon] perform the PM-04 refresh: channels, checks, defs, acks")
            break
        time.sleep(0.2)
    if fired_at_tick is None:
        print(f"[daemon] no reload observed after {ticks} ticks / {seconds}s")
    win32api.CloseHandle(handle)


def signal_role() -> None:
    try:
        handle = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, EVENT_NAME)
    except pywintypes.error as exc:
        print(f"[signal] OpenEvent FAILED: {exc}")
        print("[signal] (expected if no daemon-role process is currently running)")
        return
    win32event.SetEvent(handle)
    print(f"[signal] SetEvent('{EVENT_NAME}') sent")
    win32api.CloseHandle(handle)


def main() -> None:
    if "--daemon-role" in sys.argv:
        daemon_role()
        return
    if "--signal-role" in sys.argv:
        signal_role()
        return

    # No args: orchestrate both roles as subprocesses of this same script,
    # proving the round trip across process boundaries end to end.
    print("orchestrating: spawn daemon-role, wait, spawn signal-role, observe")
    daemon_proc = subprocess.Popen(
        [sys.executable, __file__, "--daemon-role"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(1.0)  # let CreateEvent happen before we try to OpenEvent
    signal_proc = subprocess.run(
        [sys.executable, __file__, "--signal-role"],
        capture_output=True,
        text=True,
    )
    print("--- signal-role output ---")
    print(signal_proc.stdout.strip())
    print(signal_proc.stderr.strip())

    daemon_out, _ = daemon_proc.communicate(timeout=15)
    print("--- daemon-role output ---")
    print(daemon_out.strip())

    if "reload requested" in daemon_out:
        print("\nRESULT: PASS -- named Event object round-trips across processes")
        print("as a PM-11-shaped reload signal (handler-safe: no I/O to record it,")
        print("cheap enough to poll every tick).")
    else:
        print("\nRESULT: FAIL -- daemon-role never observed the signal")


if __name__ == "__main__":
    main()

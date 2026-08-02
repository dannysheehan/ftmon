"""Spike: does windows-toasts show a toast with zero setup -- no Start-menu
shortcut, no registered AUMID, just an arbitrary applicationText string, the
way a daemon-launched-from-a-console FTMON process would call it?

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\toast_spike.py

This *shows a real toast notification* -- watch the Action Center / top-right
corner when you run it.
"""

import sys
import time

import windows_toasts


def main() -> None:
    print(f"windows-toasts version: {windows_toasts.__version__}")
    print(f"module path: {windows_toasts.__file__}")

    print("\n--- Attempt 1: WindowsToaster with an arbitrary, unregistered name ---")
    try:
        toaster = windows_toasts.WindowsToaster("ftmon-spike (unregistered)")
        toast = windows_toasts.Toast()
        toast.text_fields = ["ftmon spike", "toast with no shortcut/AUMID registration"]
        result = toaster.show_toast(toast)
        print(f"show_toast() returned: {result!r}")
        print("If a toast appeared on screen, arbitrary applicationText works with no")
        print("shortcut/AUMID registration. If nothing appeared silently, that's the")
        print("finding: it fails open (no exception) rather than failing loud.")
    except Exception as exc:  # noqa: BLE001 -- spike, want to see the real type
        print(f"raised: {type(exc).__name__}: {exc}")

    time.sleep(2)

    print("\n--- Attempt 2: InteractableWindowsToaster (needed for actions/replies) ---")
    try:
        interactable = windows_toasts.InteractableWindowsToaster("ftmon-spike (interactable)")
        toast2 = windows_toasts.Toast()
        toast2.text_fields = ["ftmon spike", "interactable toaster, no shortcut"]
        result2 = interactable.show_toast(toast2)
        print(f"show_toast() returned: {result2!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"raised: {type(exc).__name__}: {exc}")

    print("\n--- Checking whether the package can detect Python has no AUMID ---")
    try:
        from windows_toasts.wrappers import AppNotificationState  # may not exist; probe

        print(f"AppNotificationState: {AppNotificationState}")
    except ImportError as exc:
        print(f"no such helper ({exc}); package does not expose an AUMID-registration probe")


if __name__ == "__main__":
    main()
    sys.exit(0)

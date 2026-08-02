#!/usr/bin/env python3
"""Submit an osascript notification and inspect its Notification Center identity."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path


def main() -> int:
    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            'display notification "FTMON spike body" '
            'with title "FTMON spike" subtitle "zero-setup osascript"',
        ],
        capture_output=True,
        text=True,
    )
    print(f"exit={result.returncode}")
    print(f"stdout={result.stdout!r}")
    print(f"stderr={result.stderr!r}")

    prefs = Path.home() / "Library/Preferences/com.apple.ncprefs.plist"
    with prefs.open("rb") as stream:
        apps = plistlib.load(stream).get("apps", [])
    script_editor = [app for app in apps if app.get("bundle-id") == "com.apple.ScriptEditor2"]
    print(f"script_editor_preferences={script_editor}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

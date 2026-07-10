"""Desktop notifier via notify-send (NO-01/NO-02, Linux seam of PL-01).

subprocess instead of D-Bus bindings: one fewer dependency, and notify-send
is present on every desktop this targets. A missing binary or a hung
notification daemon must degrade to NotifyError (outbox retries) — never
block the tick loop longer than the timeout.
"""

from __future__ import annotations

import shutil
import subprocess

from ftmon.model import Notification
from ftmon.notify.base import NotifyError

_URGENCY = {0: "low", 1: "low", 2: "normal", 3: "critical", 4: "critical"}


class DesktopNotifier:
    name = "desktop"

    def __init__(self, timeout_s: float = 5.0):
        self._timeout_s = timeout_s
        self._binary = shutil.which("notify-send")

    def deliver(self, n: Notification) -> None:
        if self._binary is None:
            raise NotifyError("notify-send not found")
        try:
            subprocess.run(
                [
                    self._binary,
                    "-a", "ftmon",
                    "-u", _URGENCY.get(n.severity, "normal"),
                    n.title,
                    n.body,
                ],
                check=True,
                capture_output=True,
                timeout=self._timeout_s,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            raise NotifyError(str(e)) from e

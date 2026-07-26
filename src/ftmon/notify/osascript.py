"""macOS desktop notifications through osascript (NO-01/02/10, PL-01).

The zero-bundle path is intentionally best effort: macOS attributes it to
Script Editor, and exit zero means accepted rather than visibly presented.
"""

from __future__ import annotations

import shutil
import subprocess

from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery


class OsaScriptNotifier:
    name = "desktop"
    identity = "Script Editor (com.apple.ScriptEditor2)"

    def __init__(self, timeout_s: float = 5.0, binary: str | None = None):
        self._timeout_s = timeout_s
        self._binary = binary if binary is not None else shutil.which("/usr/bin/osascript")

    @property
    def available(self) -> bool:
        """Readiness is executable presence; no supported authorization preflight exists."""
        return self._binary is not None

    def deliver(self, n: Notification) -> DeliveryResult:
        if self._binary is None:
            raise PermanentDelivery("desktop_unavailable")
        script = "display notification " + _apple_string(n.body) + " with title " + _apple_string(
            n.title
        )
        try:
            subprocess.run(
                [self._binary, "-e", script],
                check=True,
                capture_output=True,
                timeout=self._timeout_s,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise PermanentDelivery("desktop_exit", status_code=exc.returncode) from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RetryableDelivery("desktop_transport") from exc
        return DeliveryResult()


def _apple_string(value: str) -> str:
    """Quote untrusted notification text as one AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

"""Desktop notifier via notify-send (NO-01/NO-02, Linux seam of PL-01).

subprocess instead of D-Bus bindings: one fewer dependency, and notify-send
is present on every desktop this targets. A missing binary is permanent while
a hung notification daemon is retryable; neither may block beyond the timeout.
"""

from __future__ import annotations

import shutil
import subprocess

from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery

_URGENCY = {0: "low", 1: "low", 2: "normal", 3: "critical", 4: "critical"}


class DesktopNotifier:
    name = "desktop"

    def __init__(self, timeout_s: float = 5.0):
        self._timeout_s = timeout_s
        self._binary = shutil.which("notify-send")

    @property
    def available(self) -> bool:
        """Readiness check without sending a popup (NO-10)."""
        return self._binary is not None

    def deliver(self, n: Notification) -> DeliveryResult:
        if self._binary is None:
            raise PermanentDelivery("desktop_unavailable")
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
        except subprocess.CalledProcessError as e:
            raise PermanentDelivery("desktop_exit", status_code=e.returncode) from e
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RetryableDelivery("desktop_transport") from e
        return DeliveryResult()

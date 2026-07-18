"""Desktop notifier via notify-send (NO-01/NO-02, Linux seam of PL-01).

subprocess instead of D-Bus bindings: one fewer dependency, and notify-send
is present on every desktop this targets. A missing binary is permanent while
a hung notification daemon is retryable; neither may block beyond the timeout.

Tray hygiene (issue #40): a persistent GNOME tray entry per delivery let one
incident's lifecycle pile up four-plus slots, and a bad day left ~100 — enough
backlog to arm gnome-shell's notification/calendar SIGABRT (LP #2138529).
So recover/renotify are sent transient (banner only), each incident reuses one
tray slot via --print-id/--replace-id, and only severity 4 gets `critical`
urgency (GNOME never auto-expires critical). Flags are probed from --help
once so an older notify-send degrades to the previous behavior, not an error.
"""

from __future__ import annotations

import shutil
import subprocess

from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery

# Severity 3 stays `normal` so error popups can expire; see module docstring.
_URGENCY = {0: "low", 1: "low", 2: "normal", 3: "normal", 4: "critical"}

# Lifecycle endpoints stay in the tray (they may need action); the churn kinds
# only flash a banner. `digest` persists — it is the one summary worth keeping.
_TRANSIENT_KINDS = frozenset({"recover", "renotify"})

# In-memory only: notification-daemon IDs do not survive its restart, so
# persisting them would replace the wrong (or no) popup after a crash. Losing
# the map merely costs one extra tray slot per open incident (NO-04 at-least-
# once is unaffected). Bounded per RB ethos; eviction drops the oldest slot.
_ID_MAP_MAX = 256


class DesktopNotifier:
    name = "desktop"

    def __init__(self, timeout_s: float = 5.0):
        self._timeout_s = timeout_s
        self._binary = shutil.which("notify-send")
        self._flags: frozenset[str] | None = None  # probed lazily, once
        self._ids: dict[int, str] = {}  # incident_id -> notification id

    @property
    def available(self) -> bool:
        """Readiness check without sending a popup (NO-10)."""
        return self._binary is not None

    def _probe_flags(self) -> frozenset[str]:
        """One --help scan instead of version parsing: distros backport, so
        the flag list is the only honest capability signal."""
        if self._flags is None:
            try:
                out = subprocess.run(
                    [self._binary, "--help"],
                    capture_output=True, timeout=self._timeout_s, text=True,
                ).stdout
            except (subprocess.SubprocessError, OSError):
                out = ""
            self._flags = frozenset(
                flag for flag in ("--transient", "--print-id", "--replace-id")
                if flag in out
            )
        return self._flags

    def deliver(self, n: Notification) -> DeliveryResult:
        if self._binary is None:
            raise PermanentDelivery("desktop_unavailable")
        flags = self._probe_flags()
        argv = [
            self._binary,
            "-a", "ftmon",
            "-u", _URGENCY.get(n.severity, "normal"),
        ]
        if n.kind in _TRANSIENT_KINDS and "--transient" in flags:
            argv.append("--transient")
        replaceable = "--print-id" in flags and "--replace-id" in flags
        if replaceable:
            argv.append("--print-id")
            known = self._ids.get(n.incident_id)
            if known is not None:
                argv.extend(["--replace-id", known])
        argv.extend([n.title, n.body])
        try:
            proc = subprocess.run(
                argv,
                check=True,
                capture_output=True,
                timeout=self._timeout_s,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise PermanentDelivery("desktop_exit", status_code=e.returncode) from e
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RetryableDelivery("desktop_transport") from e
        if replaceable:
            self._remember(n, proc.stdout.strip())
        return DeliveryResult()

    def _remember(self, n: Notification, printed: str) -> None:
        if n.kind == "recover":  # lifecycle over; free the slot
            self._ids.pop(n.incident_id, None)
            return
        if not printed.isdigit():
            return
        if n.incident_id not in self._ids and len(self._ids) >= _ID_MAP_MAX:
            self._ids.pop(next(iter(self._ids)))
        self._ids[n.incident_id] = printed

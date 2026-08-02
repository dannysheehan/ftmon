"""Windows toast notifier via windows-toasts (NO-01/NO-02, Windows seam of
PL-01).

Confirmed on feature/windows-support's spike
(spikes/windows-support/toast_spike.py, NOTES.md SS2): show_toast() works
with an arbitrary applicationText and no Start-menu shortcut or registered
AUMID -- the toast renders with that exact string as its displayed identity,
visually confirmed. windows-toasts is actively maintained (checked
2026-07-25: last push 2025-11-24, not archived).

DesktopNotifier's tray-hygiene logic (issue #40 -- transient kinds,
--print-id/--replace-id tray-slot reuse per incident) is deliberately not
replicated here. windows-toasts has a group/tag concept that could map to
it, but that was never part of the spike -- v1 sends every notification as
an independent toast, no dedup/replace, rather than guessing at unvalidated
API surface.
"""

from __future__ import annotations

from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery


class ToastNotifier:
    name = "desktop"

    def __init__(self, app_name: str = "ftmon"):
        self._toaster = None
        try:
            import windows_toasts

            self._wt = windows_toasts
            self._toaster = windows_toasts.WindowsToaster(app_name)
        except Exception:
            self._wt = None

    @property
    def available(self) -> bool:
        """Readiness check without sending a toast (NO-10)."""
        return self._toaster is not None

    def deliver(self, n: Notification) -> DeliveryResult:
        if self._toaster is None:
            raise PermanentDelivery("desktop_unavailable")
        toast = self._wt.Toast()
        toast.text_fields = [n.title, n.body]
        try:
            self._toaster.show_toast(toast)
        except Exception as e:
            raise RetryableDelivery("desktop_transport") from e
        return DeliveryResult()

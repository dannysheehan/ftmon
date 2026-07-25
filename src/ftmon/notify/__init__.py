"""Bounded first-party notification adapters (NO-02, NO-05, PL-01)."""

from ftmon.notify.base import (
    DeliveryResult,
    Notifier,
    NotifyError,
    PermanentDelivery,
    RetryableDelivery,
)
from ftmon.notify.desktop import DesktopNotifier
from ftmon.notify.file import FileNotifier
from ftmon.notify.ntfy import NtfyNotifier
from ftmon.notify.smtp import SmtpNotifier
from ftmon.notify.webhook import WebhookNotifier

__all__ = [
    "DeliveryResult", "Notifier", "NotifyError", "PermanentDelivery", "RetryableDelivery",
    "DesktopNotifier", "FileNotifier", "NtfyNotifier", "SmtpNotifier", "WebhookNotifier",
    "desktop_notifier_for_platform",
]


def desktop_notifier_for_platform(platform: str | None = None) -> Notifier | None:
    """One desktop-notifier implementation per platform (PL-01). None where
    no adapter is registered yet — callers already handle an unavailable
    desktop channel the same way as a present-but-not-`.available` one."""
    from ftmon.paths import current_platform

    current = platform or current_platform()
    if current == "linux":
        return DesktopNotifier()
    if current == "windows":
        from ftmon.notify.toast import ToastNotifier

        return ToastNotifier()
    return None

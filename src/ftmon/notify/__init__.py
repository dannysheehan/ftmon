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
]

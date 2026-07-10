"""Notification adapters (NO-02, PL-01).

Two v1 implementations: `desktop` (notify-send) and `file` (JSONL audit
log, always on — it is also what tests assert against, which keeps the
delivery path identical in CI and production)."""

from ftmon.notify.base import Notifier, NotifyError
from ftmon.notify.desktop import DesktopNotifier
from ftmon.notify.file import FileNotifier

__all__ = ["Notifier", "NotifyError", "DesktopNotifier", "FileNotifier"]

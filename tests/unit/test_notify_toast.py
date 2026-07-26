"""[NO-01][NO-02][NO-10] Windows toast adapter: availability probing and
delivery, mirroring test_notify_desktop.py's fake-module style. windows-toasts
is imported lazily inside ToastNotifier.__init__, so tests substitute a fake
module in sys.modules rather than patching an attribute on the real one.
"""

from __future__ import annotations

import sys
import types

from ftmon.model import Notification
from ftmon.notify.base import PermanentDelivery, RetryableDelivery
from ftmon.notify.toast import ToastNotifier


class FakeToast:
    def __init__(self):
        self.text_fields = None


class FakeWindowsToaster:
    def __init__(self, app_name):
        self.app_name = app_name
        self.shown: list[FakeToast] = []
        self.raise_on_show: Exception | None = None

    def show_toast(self, toast):
        if self.raise_on_show is not None:
            raise self.raise_on_show
        self.shown.append(toast)


class ExplodingToaster:
    def __init__(self, app_name):
        raise RuntimeError("no notification server registered")


def _fake_module(toaster_cls=FakeWindowsToaster):
    mod = types.ModuleType("windows_toasts")
    mod.WindowsToaster = toaster_cls
    mod.Toast = FakeToast
    return mod


def _note():
    return Notification(
        incident_id=1, kind="open", severity=3, title="t", body="b", created_ts=0.0,
    )


def test_available_when_construction_succeeds_no_10(monkeypatch):
    """[NO-10] Readiness check without sending a toast."""
    monkeypatch.setitem(sys.modules, "windows_toasts", _fake_module())
    notifier = ToastNotifier()
    assert notifier.available is True


def test_unavailable_when_toaster_construction_fails_no_10(monkeypatch):
    """[NO-10] A failed WindowsToaster() (e.g. no notification server) must
    not raise out of __init__ -- available flips to False instead."""
    monkeypatch.setitem(sys.modules, "windows_toasts", _fake_module(ExplodingToaster))
    notifier = ToastNotifier()
    assert notifier.available is False


def test_unavailable_when_package_missing(monkeypatch):
    """No windows-toasts installed at all -> unavailable, not an import crash."""
    monkeypatch.setitem(sys.modules, "windows_toasts", None)  # import raises ImportError
    notifier = ToastNotifier()
    assert notifier.available is False


def test_deliver_sends_title_and_body_as_text_fields_no_01(monkeypatch):
    """[NO-01] Notification title/body reach the toast unmodified."""
    fake_mod = _fake_module()
    monkeypatch.setitem(sys.modules, "windows_toasts", fake_mod)
    notifier = ToastNotifier()
    notifier.deliver(_note())
    (toast,) = notifier._toaster.shown
    assert toast.text_fields == ["t", "b"]


def test_deliver_raises_permanent_when_unavailable_no_02(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", None)  # import raises ImportError
    notifier = ToastNotifier()
    try:
        notifier.deliver(_note())
        raise AssertionError("expected PermanentDelivery")
    except PermanentDelivery as exc:
        assert exc.category == "desktop_unavailable"


def test_deliver_raises_retryable_on_show_toast_failure_no_02(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", _fake_module())
    notifier = ToastNotifier()
    notifier._toaster.raise_on_show = RuntimeError("transient failure")
    try:
        notifier.deliver(_note())
        raise AssertionError("expected RetryableDelivery")
    except RetryableDelivery as exc:
        assert exc.category == "desktop_transport"

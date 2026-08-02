"""[NO-01][NO-02][NO-10] macOS best-effort osascript notifier."""

from __future__ import annotations

import subprocess

import pytest

from ftmon.model import Notification
from ftmon.notify.base import PermanentDelivery, RetryableDelivery
from ftmon.notify.osascript import OsaScriptNotifier


def _note() -> Notification:
    return Notification(1, "open", 3, 'Title "quoted"', "body\\path", 0.0)


def test_exit_zero_is_accepted_and_text_is_quoted(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    notifier = OsaScriptNotifier(binary="/usr/bin/osascript")
    notifier.deliver(_note())
    assert calls[0][:2] == ["/usr/bin/osascript", "-e"]
    assert '\\"quoted\\"' in calls[0][2]
    assert "body\\\\path" in calls[0][2]
    assert notifier.identity == "Script Editor (com.apple.ScriptEditor2)"


def test_missing_binary_is_permanent():
    notifier = OsaScriptNotifier(binary=None)
    notifier._binary = None
    with pytest.raises(PermanentDelivery, match="desktop_unavailable"):
        notifier.deliver(_note())


def test_timeout_is_retryable(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RetryableDelivery, match="desktop_transport"):
        OsaScriptNotifier(binary="/usr/bin/osascript").deliver(_note())


def test_nonzero_exit_is_permanent(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(PermanentDelivery, match="desktop_exit"):
        OsaScriptNotifier(binary="/usr/bin/osascript").deliver(_note())

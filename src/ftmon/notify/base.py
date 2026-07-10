"""Notifier protocol (NO-02). deliver() raising NotifyError leaves the
outbox row undelivered for retry — the adapter must not swallow failures."""

from __future__ import annotations

from typing import Protocol

from ftmon.model import Notification


class NotifyError(Exception):
    pass


class Notifier(Protocol):
    name: str

    def deliver(self, n: Notification) -> None: ...

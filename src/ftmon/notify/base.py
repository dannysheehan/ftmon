"""Shared notification adapter contract (NO-02, NO-07).

Errors intentionally contain only stable categories and optional protocol status
codes.  Keeping remote response text out of exceptions prevents a credential or
receiver-controlled body reaching SQLite when the dispatcher records failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ftmon.model import Notification


@dataclass(frozen=True)
class DeliveryResult:
    """A successful adapter attempt; protocol detail is diagnostic only."""

    status_code: int | None = None


class DeliveryError(Exception):
    """Safe, typed delivery failure consumed by the durable dispatcher."""

    def __init__(
        self,
        category: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.retry_after = retry_after
        suffix = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"{category}{suffix}")


class RetryableDelivery(DeliveryError):
    """The dispatcher may retry this attempt under NO-07."""


class PermanentDelivery(DeliveryError):
    """Retrying this configuration or request cannot normally succeed."""


# Compatibility name for callers that only need retryable delivery failure.
NotifyError = RetryableDelivery


class Notifier(Protocol):
    name: str

    def deliver(self, n: Notification) -> DeliveryResult: ...

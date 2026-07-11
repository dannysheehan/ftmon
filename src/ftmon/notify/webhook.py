"""Documented generic JSON webhook adapter (NO-05, NO-08)."""

from __future__ import annotations

import json
import urllib.request

from ftmon.config import ChannelConfig
from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery
from ftmon.notify.http import post


class WebhookNotifier:
    name = "webhook"

    def __init__(
        self,
        config: ChannelConfig,
        *,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        if config.secret is None:
            raise PermanentDelivery("webhook_missing_url")
        self._url = config.secret.resolve()._reveal()
        self._opener = opener

    def deliver(self, n: Notification) -> DeliveryResult:
        # monitor/entity are persisted alongside the immutable notification but
        # are optional on the engine's pre-persistence value. The dispatcher
        # supplies them from the joined database row.
        payload = {
            "schema": "ftmon.notify.v1",
            "incident_id": n.incident_id,
            "kind": n.kind,
            # Preserve the canonical numeric severity used by FTMON's storage
            # and API contracts; receivers can map 0..4 without parsing text.
            "severity": n.severity,
            "title": n.title,
            "body": n.body,
            "monitor": getattr(n, "monitor", ""),
            "entity": getattr(n, "entity", getattr(n, "entity_id", "")),
            "timestamp": n.created_ts,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return post(
            self._url,
            body,
            {"Content-Type": "application/json", "User-Agent": "ftmon/2"},
            opener=self._opener,
        )

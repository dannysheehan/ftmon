"""ntfy HTTP publish adapter (NO-05, NO-08, NO-09)."""

from __future__ import annotations

import urllib.request
from urllib.parse import quote

from ftmon.config import ChannelConfig
from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery
from ftmon.notify.http import post

_PRIORITY = {0: "2", 1: "3", 2: "4", 3: "5", 4: "5"}
_SEVERITY = {0: "info", 1: "notice", 2: "warning", 3: "error", 4: "critical"}


class NtfyNotifier:
    name = "ntfy"

    def __init__(
        self,
        config: ChannelConfig,
        *,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        settings = dict(config.settings)
        if config.secret is None:
            raise PermanentDelivery("ntfy_missing_token")
        self._token = config.secret.resolve()
        self._base_url = str(settings.get("base_url", "https://ntfy.sh")).rstrip("/")
        topic = settings.get("topic")
        if not isinstance(topic, str) or not topic:
            raise PermanentDelivery("ntfy_missing_topic")
        self._topic = topic
        self._opener = opener

    def deliver(self, n: Notification) -> DeliveryResult:
        severity = _SEVERITY.get(n.severity, "warning")
        # Only the configured topic enters the URL. Rendered monitor/entity data
        # remains in the bounded body/title fields (NO-08).
        url = f"{self._base_url}/{quote(self._topic, safe='')}"
        return post(
            url,
            _bounded_body(n.title, n.body),
            {
                "Authorization": f"Bearer {self._token._reveal()}",
                # A fixed header is intentional: title/entity text is rendered
                # incident content and NO-08 forbids proxying it into headers.
                "Title": f"FTMON {severity}",
                "Priority": _PRIORITY.get(n.severity, "4"),
                "Tags": f"ftmon,{n.kind},{severity}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            opener=self._opener,
        )


def _bounded_body(title: str, body: str) -> bytes:
    """Keep receiver-bound rendered text finite even if called out of pipeline."""

    prefix = f"{title}\n\n{body}".encode()[:512]
    # Cutting at a byte boundary may split a Unicode scalar. Dropping only that
    # incomplete tail preserves valid UTF-8 while retaining the strict cap.
    return prefix.decode(errors="ignore").encode()

"""File notifier: append-only JSONL audit trail (NO-02).

Always enabled by default: it is the user's notification history, the
substrate for TS-05 assertions, and the fallback proof of delivery when the
desktop channel misbehaves. Append + flush per line; no buffering, because
the reader may be a test polling the file mid-run.
"""

from __future__ import annotations

import json
from pathlib import Path

from ftmon.model import Notification
from ftmon.notify.base import NotifyError


class FileNotifier:
    name = "file"

    def __init__(self, path: Path):
        self._path = path

    def deliver(self, n: Notification) -> None:
        line = json.dumps(
            {
                "ts": n.created_ts,
                "incident_id": n.incident_id,
                "kind": n.kind,
                "severity": n.severity,
                "title": n.title,
                "body": n.body,
            },
            ensure_ascii=False,
        )
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            raise NotifyError(str(e)) from e

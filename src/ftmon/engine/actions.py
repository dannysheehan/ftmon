"""Post-commit execution of user-owned allowlisted actions (AC-01..03).

Actions are intentionally not a general command facility: the loader resolves
a bare filename inside ``actions/``, and this runner supplies no shell, args,
inherited environment, or writable configuration capability. Results enter
incident history in a short transaction after execution (PM-03).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from ftmon.engine.effects import PendingAction

_RATE_LIMIT_S = 600
_TIMEOUT_S = 30
_OUTPUT_CAP = 8192


@dataclass(frozen=True)
class ActionResult:
    """Recorded outcome; ``status`` is ran, timeout, error, or rate_limited."""

    status: str
    detail: dict[str, object]


def _cap(value: bytes | str | None) -> str:
    """Decode and cap independently so one stream cannot hide the other."""
    if value is None:
        return ""
    raw = value if isinstance(value, bytes) else value.encode()
    if len(raw) <= _OUTPUT_CAP:
        return raw.decode(errors="replace")
    return raw[:_OUTPUT_CAP].decode(errors="replace") + "\n[truncated]"


class ActionRunner:
    """Execute committed requests with a persistent per-action rate limit."""

    def __init__(self, conn, paths):  # sqlite/path facade untyped to preserve engine layering
        self._conn = conn
        self._paths = paths

    def run_pending(self, requests: tuple[PendingAction, ...], now: float) -> None:
        """Run requests sequentially and record every run or suppression."""
        for request in requests:
            result = self.run_one(request, now)
            self._record(request.incident_id, now, result)

    def run_one(self, request: PendingAction, now: float) -> ActionResult:
        """Reserve the rate slot, execute without a shell, and cap output."""
        row = self._conn.execute(
            "SELECT last_run_ts FROM action_runs WHERE action=?", (request.action,)
        ).fetchone()
        if row is not None and max(0, now - row["last_run_ts"]) < _RATE_LIMIT_S:
            return ActionResult("rate_limited", {
                "action": request.action,
                "retry_after_s": _RATE_LIMIT_S - max(0, now - row["last_run_ts"]),
            })

        # Commit the reservation before launch. If the daemon dies inside the
        # child, safety favors suppressing a repeat over running it twice.
        self._conn.execute(
            "INSERT INTO action_runs(action,last_run_ts) VALUES(?,?) "
            "ON CONFLICT(action) DO UPDATE SET last_run_ts=excluded.last_run_ts",
            (request.action, round(now)),
        )
        self._conn.commit()

        target = self._paths.actions_dir / request.action
        if (target.is_symlink() or not target.is_file()
                or not os.access(target, os.X_OK)):
            return ActionResult("error", {
                "action": request.action,
                "error": "action disappeared or is no longer executable",
            })
        env = {"PATH": os.defpath, **request.env}
        try:
            completed = subprocess.run(
                [str(target.resolve())],
                cwd=self._paths.state_dir,
                env=env,
                capture_output=True,
                timeout=_TIMEOUT_S,
                close_fds=True,
                check=False,
            )
            return ActionResult("ran", {
                "action": request.action,
                "exit_code": completed.returncode,
                "stdout": _cap(completed.stdout),
                "stderr": _cap(completed.stderr),
            })
        except subprocess.TimeoutExpired as exc:
            return ActionResult("timeout", {
                "action": request.action,
                "timeout_s": _TIMEOUT_S,
                "stdout": _cap(exc.stdout),
                "stderr": _cap(exc.stderr),
            })
        except OSError as exc:
            return ActionResult("error", {"action": request.action, "error": str(exc)})

    def _record(self, incident_id: int, now: float, result: ActionResult) -> None:
        """Append history without involving the next tick's bulk writer."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM incident_history WHERE incident_id=?",
                (incident_id,),
            ).fetchone()[0]
            kind = "action_run" if result.status == "ran" else "action_" + result.status
            self._conn.execute(
                "INSERT INTO incident_history(incident_id,seq,ts,kind,detail) "
                "VALUES(?,?,?,?,?)",
                (incident_id, seq, round(now), kind,
                 json.dumps(result.detail, ensure_ascii=False, sort_keys=True)),
            )
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

"""Action validation and post-commit execution (AC-01..03, PM-03)."""

from __future__ import annotations

import json
import os

import pytest

from ftmon.definitions.loader import ValidationError, load_text
from ftmon.definitions.manage import ManageError, approve_draft, write_draft
from ftmon.engine.actions import ActionRunner
from ftmon.engine.effects import PendingAction
from ftmon.paths import get_paths
from ftmon.store.db import connect, migrate

ACTION_DEF = """
schema = 1
[monitor]
name = "act"
description = "action test"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "system"
[[rule]]
id = "fire"
when = "load1 > 1"
severity = "warning"
message = "busy"
action = "capture"
"""


def _paths(tmp_path):
    paths = get_paths({
        "FTMON_CONFIG_DIR": str(tmp_path / "config"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    })
    paths.ensure()
    return paths


def test_active_action_requires_user_executable_but_draft_does_not_ac_01_ac_03(tmp_path):
    """[AC-01][AC-03] Drafts may name a future action; active validation may not."""
    paths = _paths(tmp_path)
    assert load_text(ACTION_DEF).rules[0].action == "capture"
    with pytest.raises(ValidationError, match="action_unavailable"):
        load_text(ACTION_DEF, actions_dir=paths.actions_dir, require_actions=True)

    script = paths.actions_dir / "capture"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o700)  # the test is the user; production code never chmods actions
    assert load_text(
        ACTION_DEF, actions_dir=paths.actions_dir, require_actions=True
    ).rules[0].action == "capture"


def test_action_runner_minimal_env_output_and_rate_limit_ac_02(tmp_path):
    """[AC-02] One post-commit run/10m records capped output and allowlisted env."""
    paths = _paths(tmp_path)
    script = paths.actions_dir / "capture"
    script.write_text(
        "#!/bin/sh\nprintf '%s|%s|%s' \"$FTMON_MONITOR\" \"$FTMON_ENTITY\" "
        "\"${HOME-unset}\"\nprintf err >&2\n"
    )
    script.chmod(0o700)
    conn = connect(paths.db_file)
    migrate(conn)
    conn.execute(
        "INSERT INTO incidents(id,monitor,grp,entity_id,state,severity,owning_rule,"
        "opened_ts,last_change_ts,notify_count,occurrences) "
        "VALUES(1,'disk','filling','/','open',2,'fill',1,1,1,1)"
    )
    conn.commit()
    request = PendingAction(1, "capture", {
        "FTMON_MONITOR": "disk", "FTMON_RULE": "fill", "FTMON_ENTITY": "/",
        "FTMON_SEVERITY": "warning", "FTMON_MESSAGE": "filling",
        "FTMON_INCIDENT_ID": "1", "FTMON_VALUE": "true",
    })
    runner = ActionRunner(conn, paths)
    runner.run_pending((request,), 1000)
    runner.run_pending((request,), 1001)
    rows = conn.execute(
        "SELECT kind,detail FROM incident_history WHERE incident_id=1 ORDER BY seq"
    ).fetchall()
    assert [row["kind"] for row in rows] == ["action_run", "action_rate_limited"]
    detail = json.loads(rows[0]["detail"])
    assert detail["exit_code"] == 0
    assert detail["stdout"] == "disk|/|unset"
    assert detail["stderr"] == "err"
    conn.close()


def test_action_validation_never_modifies_user_script_ac_03(tmp_path):
    """[AC-03] Loading observes but never edits or chmods the user-owned script."""
    paths = _paths(tmp_path)
    script = paths.actions_dir / "capture"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o711)
    before = (script.read_bytes(), os.stat(script).st_mode)
    load_text(ACTION_DEF, actions_dir=paths.actions_dir, require_actions=True)
    assert (script.read_bytes(), os.stat(script).st_mode) == before


def test_draft_approval_waits_for_user_created_action_ac_01_ac_03(tmp_path):
    """[AC-01][AC-03] Approval is the authority boundary; drafting remains harmless."""
    paths = _paths(tmp_path)
    write_draft(paths, ACTION_DEF)
    with pytest.raises(ManageError, match="validation_failed"):
        approve_draft(paths, "act")
    script = paths.actions_dir / "capture"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o700)
    assert approve_draft(paths, "act") == paths.monitors_dir / "act.toml"

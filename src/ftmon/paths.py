"""All filesystem paths (FS-01) and the atomic-write helper (PM-06).

Nothing else in ftmon may construct config/data/state paths. Tests override
via FTMON_* environment variables (set before first get_paths() call).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import platformdirs

_APP = "ftmon"


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    monitors_dir: Path
    drafts_dir: Path
    actions_dir: Path
    config_file: Path
    check_registry_file: Path
    data_dir: Path
    db_file: Path
    state_dir: Path
    log_file: Path
    notifications_file: Path
    runtime_dir: Path
    lock_file: Path

    def ensure(self) -> None:
        """Create all directories with 0700 (FS-02, SE-04)."""
        for d in (
            self.config_dir,
            self.monitors_dir,
            self.drafts_dir,
            self.actions_dir,
            self.data_dir,
            self.state_dir,
            self.runtime_dir,
        ):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)


def get_paths(env: dict[str, str] | None = None) -> Paths:
    e = os.environ if env is None else env
    config = Path(e.get("FTMON_CONFIG_DIR", platformdirs.user_config_dir(_APP)))
    data = Path(e.get("FTMON_DATA_DIR", platformdirs.user_data_dir(_APP)))
    state = Path(e.get("FTMON_STATE_DIR", platformdirs.user_state_dir(_APP)))
    runtime = Path(
        e.get("FTMON_RUNTIME_DIR", platformdirs.user_runtime_dir(_APP))
    )
    return Paths(
        config_dir=config,
        monitors_dir=config / "monitors",
        drafts_dir=config / "monitors" / "drafts",
        actions_dir=config / "actions",
        config_file=config / "config.toml",
        check_registry_file=Path(
            e.get("FTMON_CHECK_REGISTRY", str(config / "checks.toml"))
        ),
        data_dir=data,
        db_file=data / "ftmon.db",
        state_dir=state,
        log_file=state / "daemon.log",
        notifications_file=state / "notifications.jsonl",
        runtime_dir=runtime,
        lock_file=runtime / "daemon.lock",
    )


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """PM-06(a/b): tmp file in same dir + fsync + rename; 0600 by default."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def reject_symlink(path: Path) -> None:
    """PM-06(c): definition files must not be symlinks."""
    if path.is_symlink():
        raise OSError(f"symlinked definition file rejected: {path}")

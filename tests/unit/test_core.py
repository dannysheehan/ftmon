"""[FS-01][PM-06][TS-03] paths, atomic writes, clocks, and layering lint."""

import os
import stat
import subprocess
import sys
from pathlib import Path

from ftmon.clock import FakeClock
from ftmon.paths import atomic_write, get_paths

SRC = Path(__file__).resolve().parents[2] / "src" / "ftmon"


def test_paths_env_overrides(tmp_path):
    """[FS-01] FTMON_* env vars override every root."""
    env = {
        "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
        "FTMON_DATA_DIR": str(tmp_path / "data"),
        "FTMON_STATE_DIR": str(tmp_path / "state"),
        "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
    }
    p = get_paths(env)
    assert p.monitors_dir == tmp_path / "cfg" / "monitors"
    assert p.db_file == tmp_path / "data" / "ftmon.db"
    p.ensure()
    mode = stat.S_IMODE(os.stat(p.config_dir).st_mode)
    assert mode == 0o700  # [SE-04]


def test_atomic_write_modes_and_content(tmp_path):
    """[PM-06] tmp+rename, 0600, no partial files left behind."""
    target = tmp_path / "monitors" / "x.toml"
    atomic_write(target, b"hello")
    assert target.read_bytes() == b"hello"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    atomic_write(target, b"replaced")
    assert target.read_bytes() == b"replaced"
    leftovers = [f for f in target.parent.iterdir() if f.name.startswith(".x.toml.")]
    assert leftovers == []


def test_fake_clock_divergence():
    """[TS-03][SA-07] wall and mono can diverge (suspend simulation)."""
    c = FakeClock(wall=1000.0, mono=50.0)
    c.advance(5)
    assert (c.now(), c.monotonic()) == (1005.0, 55.0)
    c.advance(5, wall_seconds=600)  # suspend: wall jumps, mono ticks
    assert (c.now(), c.monotonic()) == (1605.0, 60.0)
    c.sleep_until(70.0)
    assert c.monotonic() == 70.0


def test_no_direct_time_outside_clock():
    """[TS-03] lint: time/datetime.now calls only in clock.py.

    expr/functions.py uses datetime.fromtimestamp (conversion, not clock
    access) which is allowed; datetime.now/time.time/monotonic are not.
    """
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "clock.py":
            continue
        text = py.read_text()
        for needle in ("time.time(", "time.monotonic(", "datetime.now(", "time.sleep("):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == []


def test_expr_package_is_stdlib_only():
    """[EX-04] the expression package imports nothing from the rest of ftmon."""
    code = (
        "import sys\n"
        "import ftmon.expr\n"
        "bad = [m for m in sys.modules if m.startswith('ftmon') "
        "and not m.startswith('ftmon.expr') and m != 'ftmon']\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(SRC.parents[1]),
        env={**os.environ, "PYTHONPATH": str(SRC.parent)},
        check=True,
    )
    assert out.stdout.strip() == ""

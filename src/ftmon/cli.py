"""Command-line interface (CL-01..05).

Entry point: main(argv). Subcommands: version, init, check, status, and
stubs for daemon/mcp/web/top/incidents/etc. All read paths work with daemon
down (PM-01). Every subcommand that produces lists supports --json (CL-03).
Status exit codes: 0 all-clear, 1 warnings, 2 errors+ (CL-04).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ftmon
from ftmon.paths import get_paths


def _default_config_toml() -> str:
    """Default config.toml content (FS-02): commented, tunable."""
    return """\
# FTMON v2 configuration
# See docs/definitions.md for monitor setup; this file covers daemon behavior.

[daemon]
# Tick interval in seconds (should be 5 for most deployments)
tick_seconds = 5

[privacy]
# Include full command line in process samples (truncated if false)
collect_cmdline = true

[quiet_hours]
# Suppress notifications outside working hours (optional trading calendar)
enabled = false

[web]
# Dashboard port (http only; use reverse proxy for TLS)
port = 8420
"""


def cmd_version(args: argparse.Namespace) -> int:
    """Print version and exit."""
    print(ftmon.__version__)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize FTMON paths, write default config, install builtins (FS-02).

    - Creates all dirs (0700)
    - Writes config.toml only if absent (unless --force)
    - Installs 8 builtin *.toml files from design/builtins if available
    - Prints summary of what was installed
    """
    from ftmon.paths import atomic_write

    paths = get_paths()
    paths.ensure()

    # Write config.toml only if absent (FS-02: never touch user config)
    if not paths.config_file.exists():
        atomic_write(paths.config_file, _default_config_toml().encode())
        print(f"wrote: {paths.config_file}")
    else:
        print(f"kept: {paths.config_file} (unchanged)")

    # Install builtin monitors
    installed = []
    skipped = []

    # Try package-data path first (if ftmon.definitions exists)
    builtins_dir = None
    try:
        import importlib.resources

        try:
            # Python 3.11+: use files() API
            resources = importlib.resources.files("ftmon.definitions")
            builtins_resources = resources / "builtins"
            # Check if it exists by trying to iterate
            try:
                for item in builtins_resources.iterdir():
                    if item.is_file() and item.name.endswith(".toml"):
                        builtins_dir = builtins_resources
                        break
            except (FileNotFoundError, AttributeError):
                pass
        except (ImportError, AttributeError):
            pass
    except ImportError:
        pass

    # Fallback to repo-relative design/builtins
    if builtins_dir is None:
        fallback = Path(__file__).resolve().parents[2] / "design" / "builtins"
        if fallback.is_dir():
            builtins_dir = fallback

    if builtins_dir:
        # Iterate builtins and copy
        if isinstance(builtins_dir, Path):
            # Path object: use iterdir
            builtin_files = sorted(builtins_dir.glob("*.toml"))
        else:
            # Traversable object: use iterdir then filter
            try:
                builtin_files = sorted(
                    [item for item in builtins_dir.iterdir()
                     if item.name.endswith(".toml")]
                )
            except (AttributeError, TypeError):
                builtin_files = []

        for src in builtin_files:
            dst = paths.monitors_dir / src.name
            if dst.exists() and not args.force:
                skipped.append(src.name)
            else:
                # Read source and write atomically
                try:
                    content = src.read_bytes() if isinstance(src, Path) else src.read_bytes()
                except (AttributeError, TypeError):
                    # If src is a Traversable without read_bytes, skip
                    skipped.append(src.name)
                    continue
                atomic_write(dst, content)
                installed.append(src.name)

    # Print summary
    if installed:
        print(f"installed {len(installed)} builtin monitor(s): "
              f"{', '.join(installed)}")
    if skipped:
        print(f"skipped {len(skipped)} existing file(s): {', '.join(skipped)}")
    if not installed and not skipped:
        print("no builtin definitions found")

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate monitor definitions (CL-02).

    Loads all monitors from monitors_dir, or one file if path given.
    Prints errors in format: file: path: code: message (hint)
    Returns 0 if clean, 1 if any errors.
    """
    try:
        from ftmon.definitions import loader
    except ImportError:
        print("definitions module not available", file=sys.stderr)
        return 2

    paths = get_paths()
    errors: list[str] = []

    def render(file: Path, ve: Exception) -> None:
        # ValidationError carries structured errors (MD-01: file, key path,
        # code, message, hint); anything else gets the generic rendering.
        structured = getattr(ve, "errors", None)
        if structured:
            for err in structured:
                hint = f" ({err['hint']})" if err.get("hint") else ""
                # Loader messages may already embed the filename; strip it so
                # the location prints once.
                msg = err["message"].removeprefix(f"{file}: ")
                errors.append(f"{file}: {err['path']}: {err['code']}: {msg}{hint}")
        else:
            errors.append(f"{file}: {type(ve).__name__}: {ve}")

    if args.path:
        try:
            loader.load_file(Path(args.path))
        except Exception as e:
            render(Path(args.path), e)
    else:
        # load_dir reports per-file failures as (path, ValidationError) pairs
        # rather than raising - a broken file must not hide the others (PM-04).
        for d in (paths.monitors_dir, paths.drafts_dir):
            if not d.exists():
                continue
            _defs, file_errors = loader.load_dir(d)
            for file, ve in file_errors:
                render(file, ve)

    for error in errors:
        print(error, file=sys.stderr)

    return 1 if errors else 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show daemon status (CL-04): last tick age, db size, incident count.

    Exit codes: 0 all-clear, 1 warnings, 2 errors+.
    Supports --json for scripting (CL-03).
    """
    from ftmon.paths import get_paths

    paths = get_paths()

    # Check if db file exists
    if not paths.db_file.exists():
        msg = "no data - is the daemon running? (ftmon daemon)"
        if args.json:
            print(json.dumps({"status": "no_data", "message": msg}))
        else:
            print(msg)
        return 1

    try:
        # Submodule imports, lazily: `ftmon.store` the package deliberately
        # re-exports nothing (DESIGN section 1 layering keeps import cost at
        # the composition points).
        from ftmon.clock import SystemClock
        from ftmon.store.db import connect
        from ftmon.store.query import Query

        now = SystemClock().now()
        conn = connect(paths.db_file, readonly=True)
        try:
            query = Query(conn)
            info = query.status(now=now)
            incidents = query.incidents(state="open")
            max_severity = max((row["severity"] for row in incidents), default=-1)
        finally:
            conn.close()

        # CL-04 exit codes: 0 all clear, 1 warnings-and-below, 2 errors+.
        if max_severity < 0:
            exit_code = 0
        elif max_severity <= 2:
            exit_code = 1
        else:
            exit_code = 2

        age = info.get("last_tick_age_s")
        if args.json:
            print(json.dumps({"status": "ok", "max_severity": max_severity, **info}))
        else:
            print(f"Last tick: {age:.0f}s ago" if age is not None else "Last tick: never")
            print(f"Database: {info['db_bytes'] / 2**20:.1f} MB")
            print(f"Open incidents: {info['open_incidents']}")
        return exit_code

    except Exception as e:
        msg = f"database error: {e}"
        if args.json:
            print(json.dumps({"status": "error", "message": msg}))
        else:
            print(msg, file=sys.stderr)
        return 1


def cmd_not_implemented(cmd_name: str) -> int:
    """Stub: print not-implemented message and return 2."""
    def handler(args: argparse.Namespace) -> int:
        print(f"{cmd_name}: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    return handler


def main(argv: list[str] | None = None) -> int:
    """Main entry point (CL-01). Returns exit code.

    Subcommands:
    - version: print version
    - init: setup paths and install defaults
    - check [file]: validate definitions
    - status: daemon status (exit code 0/1/2 per CL-04)
    - daemon, mcp, web, top, incidents, etc.: stubs (return 2)
    """
    parser = argparse.ArgumentParser(
        prog="ftmon",
        description="FTMON v2 - lightweight local systems monitor",
    )
    parser.add_argument(
        "--version", action="version", version=f"ftmon {ftmon.__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="subcommand")

    # version
    subparsers.add_parser("version", help="Print version and exit")

    # init
    init_parser = subparsers.add_parser(
        "init", help="Initialize FTMON (create dirs, install defaults)"
    )
    init_parser.add_argument(
        "--force", action="store_true",
        help="Re-install builtins (does not touch user config)"
    )

    # check
    check_parser = subparsers.add_parser(
        "check", help="Validate monitor definitions"
    )
    check_parser.add_argument(
        "path", nargs="?", help="Check one file (or all if omitted)"
    )

    # status
    status_parser = subparsers.add_parser(
        "status", help="Show daemon status and health"
    )
    status_parser.add_argument(
        "--json", action="store_true", help="Output JSON"
    )

    # daemon
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Run the daemon (main monitoring process)"
    )
    daemon_parser.add_argument(
        "--clock",
        choices=["system", "controlled"],
        default="system",
        help="controlled = test-harness clock via FTMON_CLOCK_SOCK (TS-05)",
    )

    # mcp
    subparsers.add_parser(
        "mcp",
        help="Run the MCP server (programmatic access)"
    )

    # web
    subparsers.add_parser(
        "web",
        help="Run the web dashboard (http://localhost:8420)"
    )

    # top
    subparsers.add_parser(
        "top",
        help="Show live metrics (like the old legacy top)"
    )

    # incidents
    subparsers.add_parser(
        "incidents",
        help="List all open incidents"
    )

    # incident
    incident_parser = subparsers.add_parser(
        "incident",
        help="Show details of one incident"
    )
    incident_parser.add_argument("id", help="Incident ID")

    # ack
    ack_parser = subparsers.add_parser(
        "ack",
        help="Acknowledge an incident"
    )
    ack_parser.add_argument("id", help="Incident ID")
    ack_parser.add_argument("--note", help="Acknowledgment note")

    # events
    subparsers.add_parser(
        "events",
        help="List raw events (log source samples)"
    )

    # query
    subparsers.add_parser(
        "query",
        help="Query metrics (time-series data)"
    )

    # monitors
    subparsers.add_parser(
        "monitors",
        help="List all monitors"
    )

    # monitor
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Monitor management (approve, enable, disable)"
    )
    monitor_parser.add_argument(
        "action", choices=["approve", "enable", "disable"],
        help="Action to take"
    )
    monitor_parser.add_argument("name", help="Monitor name")

    # baseline
    baseline_parser = subparsers.add_parser(
        "baseline",
        help="Baseline management"
    )
    baseline_parser.add_argument(
        "action", choices=["reset"],
        help="Action to take"
    )

    # doctor
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Database health check and repair"
    )
    doctor_parser.add_argument(
        "--deep", action="store_true",
        help="Run full integrity_check (slower)"
    )
    doctor_parser.add_argument(
        "--backup", metavar="PATH",
        help="Backup database to this path"
    )

    args = parser.parse_args(argv)

    # Dispatch to handler
    if args.command == "version":
        return cmd_version(args)
    elif args.command == "init":
        return cmd_init(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "daemon":
        # Imported lazily: the daemon pulls in psutil/sqlite machinery that
        # a `ftmon version` in a broken environment should not need.
        from ftmon.daemon import run as daemon_run

        return daemon_run(args)
    elif args.command == "mcp":
        print("mcp: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "web":
        print("web: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "top":
        print("top: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "incidents":
        print("incidents: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "incident":
        print("incident: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "ack":
        print("ack: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "events":
        print("events: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "query":
        print("query: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "monitors":
        print("monitors: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "monitor":
        print("monitor: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "baseline":
        print("baseline: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "doctor":
        print("doctor: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Command-line interface (CL-01..08).

Entry point: main(argv). Implements version/init/check/status plus list
commands (incidents/events/monitors), monitor management (monitor),
maintenance (doctor/baseline/demo), and authoring discoverability
(paths, monitor rescan, check trust — CL-06..08); top/query/incident remain
stubs. All read paths work with daemon down (PM-01). Every list subcommand
supports --json (CL-03). Status exit codes: 0 all-clear, 1 warnings,
2 errors+ (CL-04).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ftmon
from ftmon.paths import get_paths


def _builtin_monitors_source(profile: str):
    """Return a Path or Traversable directory of monitor TOML to install (FS-02).

    Desktop profile installs calibrated copies from profile/desktop; server
    profile keeps the normative design/builtins defaults.
    """
    if profile == "desktop":
        try:
            import importlib.resources

            resources = importlib.resources.files("ftmon.definitions") / "profile" / "desktop"
            try:
                for item in resources.iterdir():
                    if item.is_file() and item.name.endswith(".toml"):
                        return resources
            except (FileNotFoundError, AttributeError, TypeError):
                pass
        except ImportError:
            pass
        fallback = Path(__file__).resolve().parents[2] / "design" / "profile" / "desktop"
        if fallback.is_dir():
            return fallback

    try:
        import importlib.resources

        resources = importlib.resources.files("ftmon.definitions") / "builtins"
        try:
            for item in resources.iterdir():
                if item.is_file() and item.name.endswith(".toml"):
                    return resources
        except (FileNotFoundError, AttributeError, TypeError):
            pass
    except ImportError:
        pass
    fallback = Path(__file__).resolve().parents[2] / "design" / "builtins"
    return fallback if fallback.is_dir() else None


def _default_config_toml(profile: str = "desktop") -> str:
    """Explicit profile scaffold (PM-08); no runtime profile switch remains."""
    desktop_enabled = "true" if profile == "desktop" else "false"
    return f"""\
# FTMON v2 configuration
# See docs/definitions.md for monitor setup; this file covers daemon behavior.
# Generated for the {profile} profile. Every setting below is ordinary config;
# changing profile later means editing these values, not changing hidden behavior.

[daemon]
# Tick interval in seconds (should be 5 for most deployments)
tick_seconds = 5

[privacy]
# Include full command line in process samples (truncated if false)
collect_cmdline = true

[quiet_hours]
# NO-03: hold warning-and-below notifications overnight, delivered as one
# digest when quiet hours end. error and critical always come through.
# Incidents still open/clear during quiet hours - only delivery is held.
enabled = false
start = "22:00"   # local time, HH:MM
end = "08:00"     # may cross midnight (as here)

[web]
# Dashboard port (http only; use reverse proxy for TLS)
port = 8420

# The file audit channel is mandatory and has no enable switch.
[notify.desktop]
enabled = {desktop_enabled}
min_severity = "info"

# Remote channels start disabled. Credentials must stay outside this file;
# choose exactly one *_env or *_file reference before enabling a channel.
[notify.ntfy]
enabled = false
min_severity = "warning"
base_url = "https://ntfy.sh"
topic = "ftmon-hostname"
# token_env = "FTMON_NTFY_TOKEN"

[notify.webhook]
enabled = false
min_severity = "warning"
# url_env = "FTMON_WEBHOOK_URL"

[notify.smtp]
enabled = false
min_severity = "warning"
host = "smtp.example.net"
port = 587
tls = "starttls"
username = "ftmon@example.net"
from = "ftmon@example.net"
to = ["operator@example.net"]
# password_env = "FTMON_SMTP_PASSWORD"
"""


def cmd_version(args: argparse.Namespace) -> int:
    """Print version and exit."""
    print(ftmon.__version__)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize FTMON paths, write default config, install builtins (FS-02).

    - Creates all dirs (0700)
    - Writes config.toml only if absent (unless --force)
    - Installs 8 builtin *.toml files (desktop profile uses calibrated monitors)
    - Prints summary of what was installed
    """
    from ftmon.paths import atomic_write

    paths = get_paths()
    paths.ensure()

    # Write config.toml only if absent (FS-02: never touch user config)
    if not paths.config_file.exists():
        atomic_write(paths.config_file, _default_config_toml(args.profile).encode())
        print(f"wrote: {paths.config_file}")
    else:
        print(f"kept: {paths.config_file} (unchanged)")

    default_registry = paths.config_dir / "checks.toml"
    if paths.check_registry_file == default_registry and not paths.check_registry_file.exists():
        # A concrete empty table makes "no execution authority" explicit and
        # gives operators a discoverable starting point without granting any
        # command through monitor definitions themselves (FS-03/EC-01).
        atomic_write(
            paths.check_registry_file,
            b"# Administrator-owned external check registry.\n"
            b"# Monitor definitions may reference aliases declared here.\n"
            b"[check]\n",
        )
        print(f"wrote: {paths.check_registry_file}")

    # Install builtin monitors for the selected profile.
    installed = []
    skipped = []

    builtins_dir = _builtin_monitors_source(args.profile)

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


def cmd_recipe(args: argparse.Namespace) -> int:
    """List or install curated recipes; daemon hot-reload needs no restart (PM-04, XR-02)."""
    from ftmon.recipes import InstallError, install_recipe, list_recipe_ids

    paths = get_paths()
    paths.ensure()
    action = getattr(args, "action", None)
    if action == "list":
        for recipe_id in list_recipe_ids():
            print(recipe_id)
        return 0
    try:
        result = install_recipe(
            paths,
            args.recipe_ref,
            force=args.force,
            enable=not args.no_enable,
        )
    except InstallError as exc:
        print(f"{exc.category}: {exc.message}", file=sys.stderr)
        return 1
    state = "enabled" if result.enabled else "installed (disabled)"
    aliases = ", ".join(result.aliases) or "unchanged"
    print(f"installed recipe: {result.recipe_id}")
    print(f"monitor: {result.monitor} ({state})")
    print(f"checks: {aliases}")
    print(f"wrote: {result.monitor_path}")
    print("the daemon picks this up within 30s; no restart required")
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
    check_aliases: frozenset[str] = frozenset()
    if paths.check_registry_file.exists():
        try:
            from ftmon.checks.registry import load as load_check_registry

            check_aliases = frozenset(load_check_registry(
                paths.check_registry_file, paths=paths
            ))
        except ValueError as exc:
            # Registry errors expose only a stable category, never argv.
            errors.append(f"{paths.check_registry_file}: registry: {exc}")

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
            loader.load_file(Path(args.path), actions_dir=paths.actions_dir,
                             require_actions=True, check_aliases=check_aliases,
                             require_checks=True)
        except Exception as e:
            render(Path(args.path), e)
    else:
        # load_dir reports per-file failures as (path, ValidationError) pairs
        # rather than raising - a broken file must not hide the others (PM-04).
        for d in (paths.monitors_dir, paths.drafts_dir):
            if not d.exists():
                continue
            _defs, file_errors = loader.load_dir(
                d,
                actions_dir=paths.actions_dir,
                require_actions=d == paths.monitors_dir,
                check_aliases=check_aliases,
                require_checks=d == paths.monitors_dir,
            )
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


def cmd_incidents(args: argparse.Namespace) -> int:
    """List incidents (CL-01). Default: open + acked; --all includes cleared."""
    paths = get_paths()
    if not paths.db_file.exists():
        print("no data - is the daemon running? (ftmon daemon)", file=sys.stderr)
        return 1
    from ftmon.model import severity_name
    from ftmon.store.db import connect
    from ftmon.store.query import Query

    conn = connect(paths.db_file, readonly=True)
    try:
        rows = Query(conn).incidents(state=None if args.all else "open")
        if not args.all:
            rows = [r for r in rows if r["state"] in ("open", "acked")]
        if args.json:
            print(json.dumps([dict(r) for r in rows]))
            return 0
        if not rows:
            print("no incidents")
            return 0
        for r in rows:
            flap = " (flapping)" if r["flapping"] else ""
            print(
                f"#{r['id']:<4} {r['state']:<7} {severity_name(r['severity']):<8} "
                f"{r['monitor']}/{r['grp']} {r['entity_id']}{flap}"
            )
        return 0
    finally:
        conn.close()


def cmd_ack(args: argparse.Namespace) -> int:
    """Acknowledge an incident: stop renotifying, keep watching (IN-02)."""
    paths = get_paths()
    if not paths.db_file.exists():
        print("no data - is the daemon running? (ftmon daemon)", file=sys.stderr)
        return 1
    from ftmon.clock import SystemClock
    from ftmon.store.db import connect
    from ftmon.store.query import SmallWrites

    conn = connect(paths.db_file)
    try:
        ok = SmallWrites(conn).ack(args.id, by="cli", ts=SystemClock().now(), note=args.note)
    finally:
        conn.close()
    if ok:
        print(f"incident #{args.id} acknowledged (it will still clear on recovery)")
        return 0
    print(f"incident #{args.id} is not open (already acked, cleared, or unknown)",
          file=sys.stderr)
    return 1


def cmd_events(args: argparse.Namespace) -> int:
    """List stored events (CL-01, DM-09). What's here is what passed the
    store-filter: severity >= notice or rule-matched — not the full journal
    (that's what journalctl is for)."""
    paths = get_paths()
    if not paths.db_file.exists():
        print("no data - is the daemon running? (ftmon daemon)", file=sys.stderr)
        return 1
    from ftmon.clock import SystemClock
    from ftmon.model import SEVERITIES, severity_name
    from ftmon.store.db import connect
    from ftmon.store.query import Query

    min_sev = 0
    if args.min_severity:
        if args.min_severity not in SEVERITIES:
            print(f"unknown severity {args.min_severity!r}; use one of "
                  f"{', '.join(SEVERITIES)}", file=sys.stderr)
            return 1
        min_sev = SEVERITIES.index(args.min_severity)

    now = SystemClock().now()
    conn = connect(paths.db_file, readonly=True)
    try:
        rows = Query(conn).events(
            start=now - args.hours * 3600, end=now,
            min_severity=min_sev, provider=args.provider, limit=args.limit,
        )
    finally:
        conn.close()
    if args.json:
        print(json.dumps([dict(r) for r in rows]))
        return 0
    if not rows:
        print("no stored events in range")
        return 0
    for r in rows:
        from datetime import datetime

        when = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M:%S")
        print(f"{when} {severity_name(r['severity']):<8} "
              f"{r['provider']:<20} {r['message']}")
    return 0


def cmd_monitors(args: argparse.Namespace) -> int:
    """List installed monitors and pending drafts (CL-01, CL-03)."""
    from ftmon.clock import SystemClock
    from ftmon.definitions.manage import list_monitors

    payload = list_monitors(get_paths(), now=SystemClock().now())
    if args.json:
        print(json.dumps(payload))
        return 0
    monitors = sorted(payload["monitors"], key=lambda row: row["name"])
    if not monitors:
        print("no monitors")
        return 0
    for row in monitors:
        state = row["state"]
        if state in {"config_error", "draft_invalid"}:
            detail = row.get("error", "")[:60]
            print(f"{row['name']:<20} {state:<14} {detail}")
            continue
        source = row.get("source", "")
        description = row.get("description", "")[:50]
        print(f"{row['name']:<20} {state:<14} {source:<10} {description}")
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    """CL-06: print the resolved layout so authors stop guessing where files
    go. Paths only, never contents; works with the daemon down (PM-01)."""
    paths = get_paths()
    layout = {
        "config_dir": paths.config_dir,
        "config_file": paths.config_file,
        "monitors_dir": paths.monitors_dir,
        "drafts_dir": paths.drafts_dir,
        "actions_dir": paths.actions_dir,
        "check_registry": paths.check_registry_file,
        "data_dir": paths.data_dir,
        "db_file": paths.db_file,
        "state_dir": paths.state_dir,
        "log_file": paths.log_file,
        "notifications_file": paths.notifications_file,
        "runtime_dir": paths.runtime_dir,
        "lock_file": paths.lock_file,
    }
    if args.json:
        print(json.dumps({k: str(v) for k, v in layout.items()}, indent=2))
        return 0
    width = max(len(k) for k in layout)
    for key, value in layout.items():
        print(f"{key:<{width}}  {value}")
    return 0


def _monitor_rescan(paths) -> int:
    """CL-07: SIGHUP the daemon recorded in the PM-02 lock file. Acquiring
    the flock proves no daemon holds it — never signal a stale pid."""
    import fcntl
    import os
    import signal

    try:
        f = open(paths.lock_file)
    except OSError:
        print("daemon not running (no lock file); start it with `ftmon daemon`",
              file=sys.stderr)
        return 1
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("daemon not running (lock not held); start it with "
                  "`ftmon daemon`", file=sys.stderr)
            return 1
        except BlockingIOError:
            pass
        pid_text = f.read().strip()
    if not pid_text.isdigit():
        print("daemon lock held but no pid recorded — daemon predates "
              "CL-07; send SIGHUP manually or restart it", file=sys.stderr)
        return 1
    try:
        os.kill(int(pid_text), signal.SIGHUP)
    except (ProcessLookupError, PermissionError) as exc:
        print(f"cannot signal daemon pid {pid_text}: {exc}", file=sys.stderr)
        return 1
    print(f"reload requested (SIGHUP to pid {pid_text}); "
          "applied at the next tick")
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    """MD-05 lifecycle: approve a draft into monitors/ (PM-06d), flip a
    monitor's `enabled` line in place, or request an immediate reload
    (CL-07). The daemon notices edits within 30s (PM-04); no restart."""
    from ftmon.definitions import manage

    paths = get_paths()
    if args.action == "rescan":
        return _monitor_rescan(paths)
    if not args.name:
        print(f"monitor {args.action}: missing monitor name", file=sys.stderr)
        return 2
    check_aliases: frozenset[str] = frozenset()
    if paths.check_registry_file.exists():
        try:
            from ftmon.checks.registry import load as load_check_registry

            check_aliases = frozenset(load_check_registry(
                paths.check_registry_file, paths=paths
            ))
        except ValueError:
            # Approval must fail closed; doctor carries the redacted category.
            pass
    try:
        if args.action == "approve":
            target = manage.approve_draft(paths, args.name, check_aliases=check_aliases)
            print(f"approved: {target} (the daemon picks it up within 30s)")
        else:
            enabled = args.action == "enable"
            target = manage.set_enabled(
                paths, args.name, enabled, check_aliases=check_aliases
            )
            print(f"{'enabled' if enabled else 'disabled'}: {target}")
        return 0
    except manage.ManageError as e:
        print(f"{e.code}: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"  hint: {e.hint}", file=sys.stderr)
        for err in e.errors:
            print(f"  {err['path']}: {err['code']}: {err['message']}",
                  file=sys.stderr)
        return 1


def cmd_baseline(args: argparse.Namespace) -> int:
    """CA-06: `ftmon baseline reset <monitor> [entity]` clears learned
    baselines so they relearn from scratch (they return unknown while
    relearning — affected rules go quiet, they don't misfire)."""
    paths = get_paths()
    if not paths.db_file.exists():
        print("no data - is the daemon running? (ftmon daemon)", file=sys.stderr)
        return 1
    from ftmon.store.db import connect
    from ftmon.store.retention import reset_baselines

    conn = connect(paths.db_file)
    try:
        n = reset_baselines(conn, args.monitor, args.entity)
    finally:
        conn.close()
    scope = f"{args.monitor}/{args.entity}" if args.entity else args.monitor
    print(f"reset {n} baseline(s) for {scope} (relearning takes ~24h of data)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Inspect database/config health and optionally create a live backup (CL-05)."""
    from ftmon.clock import SystemClock
    from ftmon.config import load_config
    from ftmon.definitions import loader
    from ftmon.store.db import connect
    from ftmon.store.doctor import backup, inspect

    paths = get_paths()
    if not paths.db_file.exists():
        print("problem: database does not exist; start the daemon once", file=sys.stderr)
        return 1
    config, config_warnings = load_config(paths.config_file)
    config_errors = list(config_warnings)
    check_aliases: frozenset[str] = frozenset()
    registry_status = "disabled (registry missing)"
    if paths.check_registry_file.exists():
        try:
            from ftmon.checks.registry import load as load_check_registry

            registry = load_check_registry(paths.check_registry_file, paths=paths)
            check_aliases = frozenset(registry)
            registry_status = f"ready ({len(registry)} aliases)"
        except ValueError as exc:
            registry_status = f"error ({exc})"
            config_errors.append(f"external check registry: {exc}")
    channel_errors = {
        warning.split("]", 1)[0].removeprefix("[notify.")
        for warning in config_warnings if warning.startswith("[notify.")
    }
    if any(warning.startswith("config.toml unreadable") for warning in config_warnings):
        channel_errors.update(name for name, _channel in config.channels)
    _defs, definition_errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True,
        check_aliases=check_aliases, require_checks=True,
    )
    config_errors.extend(f"{path}: {error}" for path, error in definition_errors)
    conn = connect(paths.db_file)
    try:
        report = inspect(conn, now=SystemClock().now(), deep=args.deep)
        if args.backup:
            backup(conn, Path(args.backup))
    except Exception as exc:
        print(f"problem: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"{report['check']}: {', '.join(report['integrity'])}")
    print(f"WAL checkpoint: busy={report['checkpoint'][0]} "
          f"log={report['checkpoint'][1]} checkpointed={report['checkpoint'][2]}")
    print(f"Database: {report['db_bytes'] / 2**20:.1f} MB")
    print("Tables: " + ", ".join(f"{k}={v}" for k, v in report["tables"].items()))
    print("Orphans: " + ", ".join(f"{k}={v}" for k, v in report["orphans"].items()))
    for cursor in report["cursors"]:
        print(f"Cursor {cursor['source']}: {cursor['age_s']:.0f}s old")
    print("Notification file: ready")
    print(f"External checks: {registry_status}")
    for name, channel in config.channels:
        # A stable code is useful to automation; resolver prose remains only a
        # redacted diagnostic and doctor never sends a probe message (NO-10).
        status = (
            "error (invalid_config)" if name in channel_errors
            else "ready" if channel.enabled else "disabled"
        )
        if name == "desktop" and status == "ready":
            from ftmon.notify import DesktopNotifier

            if not DesktopNotifier().available:
                status = "error (desktop_unavailable)"
        print(f"Notification {name}: {status}")
    for error in config_errors:
        print(f"Config error: {error}", file=sys.stderr)
    if args.backup:
        print(f"Backup: {Path(args.backup).expanduser().resolve()}")
    return 0 if report["ok"] and not config_errors else 1


def cmd_not_implemented(cmd_name: str) -> int:
    """Stub: print not-implemented message and return 2."""
    def handler(args: argparse.Namespace) -> int:
        print(f"{cmd_name}: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    return handler


def _dispatch_check_install(argv: list[str]) -> int | None:
    """`ftmon check install` shares recipe install without breaking `check <path>`."""
    if len(argv) < 2 or argv[0] != "check" or argv[1] != "install":
        return None
    parser = argparse.ArgumentParser(prog="ftmon check install")
    parser.add_argument(
        "recipe_ref",
        help="Recipe id (e.g. http-tls) or path to a recipe directory",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Replace an existing monitor file or check alias",
    )
    parser.add_argument(
        "--no-enable", action="store_true",
        help="Install without flipping enabled = true",
    )
    args = parser.parse_args(argv[2:])
    args.action = "install"
    return cmd_recipe(args)


def _dispatch_check_trust(argv: list[str]) -> int | None:
    """CL-08: `ftmon check trust <path>` shares `check` without breaking
    `check <path>`, mirroring the `check install` shim. Reports every failed
    condition of the EC-01/SE-07 predicate; never executes the candidate."""
    if len(argv) < 2 or argv[0] != "check" or argv[1] != "trust":
        return None
    parser = argparse.ArgumentParser(prog="ftmon check trust")
    parser.add_argument(
        "executable", help="Absolute path to a candidate check executable"
    )
    args = parser.parse_args(argv[2:])
    from ftmon.checks.trust import trust_failures

    failures = trust_failures(args.executable)
    if not failures:
        print(f"trusted: {args.executable}")
        return 0
    print(f"not trusted: {args.executable}", file=sys.stderr)
    for reason in failures:
        print(f"  {reason}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point (CL-01). Returns exit code.

    Subcommands:
    - version: print version
    - init: setup paths and install defaults
    - check [file]: validate definitions
    - status: daemon status (exit code 0/1/2 per CL-04)
    - demo build: atomically create deterministic synthetic demonstration data
    - daemon, mcp, web: run services; top, query, incident: stubs (return 2)
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    install_rc = _dispatch_check_install(argv)
    if install_rc is not None:
        return install_rc
    trust_rc = _dispatch_check_trust(argv)
    if trust_rc is not None:
        return trust_rc

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
    init_parser.add_argument(
        "--profile", choices=("desktop", "server"), default="desktop",
        help="Write explicit desktop or server defaults (default: desktop)",
    )

    # check
    check_parser = subparsers.add_parser(
        "check", help="Validate monitor definitions (see: ftmon recipe install)"
    )
    check_parser.add_argument(
        "path", nargs="?", help="Check one file (or all if omitted)"
    )

    # recipe
    recipe_parser = subparsers.add_parser(
        "recipe", help="List or install curated extra-monitor recipes"
    )
    recipe_sub = recipe_parser.add_subparsers(dest="action", required=True)
    recipe_sub.add_parser("list", help="List installable recipe IDs")
    recipe_install = recipe_sub.add_parser(
        "install", help="Install a recipe into monitors/ and checks.toml"
    )
    recipe_install.add_argument(
        "recipe_ref",
        help="Recipe id (e.g. http-tls) or path to a recipe directory",
    )
    recipe_install.add_argument(
        "--force", action="store_true",
        help="Replace an existing monitor file or check alias",
    )
    recipe_install.add_argument(
        "--no-enable", action="store_true",
        help="Install without flipping enabled = true",
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
    daemon_parser.add_argument(
        "--fixtures",
        metavar="SCENARIO",
        help="replay a named scenario or JSONL file instead of live sampling (TS-04)",
    )

    # mcp
    subparsers.add_parser(
        "mcp",
        help="Run the MCP server (programmatic access)"
    )

    # web
    web_parser = subparsers.add_parser(
        "web",
        help="Run the web dashboard (http://localhost:8420)"
    )
    web_parser.add_argument(
        "--demo", action="store_true",
        help="serve only a validated synthetic database through the GET-only demo app",
    )
    web_parser.add_argument("--demo-db", type=Path, help="synthetic demo database")
    web_parser.add_argument("--demo-host", help="exact public demo hostname")
    web_parser.add_argument("--port", type=int, help="loopback listen port")

    # top
    subparsers.add_parser(
        "top",
        help="Show live metrics (like the old legacy top)"
    )

    # incidents
    incidents_parser = subparsers.add_parser(
        "incidents",
        help="List open/acked incidents (--all includes cleared)"
    )
    incidents_parser.add_argument("--all", action="store_true",
                                  help="Include cleared incidents")
    incidents_parser.add_argument("--json", action="store_true", help="Output JSON")

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
    ack_parser.add_argument("id", type=int, help="Incident ID")
    ack_parser.add_argument("--note", help="Acknowledgment note")

    # events
    events_parser = subparsers.add_parser(
        "events",
        help="List stored events (journal entries that passed the store-filter)"
    )
    events_parser.add_argument("--min-severity", metavar="LEVEL",
                               help="info|notice|warning|error|critical")
    events_parser.add_argument("--provider", help="Filter by producer")
    events_parser.add_argument("--hours", type=float, default=24.0,
                               help="Look back this many hours (default 24)")
    events_parser.add_argument("--limit", type=int, default=200)
    events_parser.add_argument("--json", action="store_true", help="Output JSON")

    # query
    subparsers.add_parser(
        "query",
        help="Query metrics (time-series data)"
    )

    # monitors
    monitors_parser = subparsers.add_parser(
        "monitors",
        help="List all monitors",
    )
    monitors_parser.add_argument(
        "--json", action="store_true", help="Output JSON",
    )

    # monitor
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Monitor management (approve, enable, disable, rescan)"
    )
    monitor_parser.add_argument(
        "action", choices=["approve", "enable", "disable", "rescan"],
        help="Action to take"
    )
    monitor_parser.add_argument(
        "name", nargs="?", default=None,
        help="Monitor name (not used by rescan)"
    )

    # paths
    paths_parser = subparsers.add_parser(
        "paths", help="Print the resolved filesystem layout (CL-06)"
    )
    paths_parser.add_argument(
        "--json", action="store_true", help="Machine-readable output (CL-03)"
    )

    # baseline
    baseline_parser = subparsers.add_parser(
        "baseline",
        help="Baseline management (reset learned baselines, CA-06)"
    )
    baseline_parser.add_argument(
        "action", choices=["reset"],
        help="Action to take"
    )
    baseline_parser.add_argument("monitor", help="Monitor name")
    baseline_parser.add_argument("entity", nargs="?", default=None,
                                 help="Entity id (all entities if omitted)")

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

    demo_parser = subparsers.add_parser(
        "demo", help="Build deterministic synthetic public-demo data"
    )
    demo_subparsers = demo_parser.add_subparsers(dest="demo_command", required=True)
    demo_build = demo_subparsers.add_parser(
        "build", help="Atomically build a marked synthetic SQLite database"
    )
    demo_build.add_argument("--output", required=True, type=Path, metavar="PATH")

    args = parser.parse_args(argv)

    if args.command == "web" and args.demo:
        if args.demo_db is None or args.demo_host is None:
            parser.error("web --demo requires --demo-db and --demo-host")
    elif args.command == "web" and (args.demo_db is not None or args.demo_host is not None):
        parser.error("--demo-db and --demo-host require web --demo")
    if args.command == "web" and args.port is not None and not 1 <= args.port <= 65535:
        parser.error("web --port must be between 1 and 65535")

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
        from ftmon.mcp_server import run as mcp_run

        return mcp_run(args)
    elif args.command == "web":
        from ftmon.web.app import run as web_run

        return web_run(args)
    elif args.command == "top":
        print("top: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "incidents":
        return cmd_incidents(args)
    elif args.command == "incident":
        print("incident: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "ack":
        return cmd_ack(args)
    elif args.command == "events":
        return cmd_events(args)
    elif args.command == "query":
        print("query: not implemented yet (arrives in a later milestone)",
              file=sys.stderr)
        return 2
    elif args.command == "monitors":
        return cmd_monitors(args)
    elif args.command == "monitor":
        return cmd_monitor(args)
    elif args.command == "paths":
        return cmd_paths(args)
    elif args.command == "recipe":
        return cmd_recipe(args)
    elif args.command == "baseline":
        return cmd_baseline(args)
    elif args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "demo":
        from ftmon.demo import build

        output = build(args.output)
        print(f"built synthetic demo database: {output}")
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())

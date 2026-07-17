# Writing an external check

This is the authoring guide: how to write the executable itself. For how an
administrator grants that executable authority to run, see
[External checks](external-checks.md). For how a monitor definition maps a
check's output onto stored metrics, see the "External checks" section of
[Definitions](definitions.md).

## What a check is, and the boundary around it

An external check is a small, separately maintained program — your own
script, or a Nagios/Monitoring Plugins-compatible binary — that answers one
question right now ("is the certificate about to expire?", "is the queue
backed up?"). FTMON runs it on a schedule and adds the parts a one-shot check
cannot provide on its own: bounded history, consecutive-cycle confirmation,
incidents, notifications, derived metrics and declared Trends.

The boundary is deliberate and enforced in code, not just convention: the
check executable lives entirely outside FTMON. A monitor definition, an
AI-authored draft, or the web UI can reference an administrator-approved
*alias* for a check, but none of them can specify or change what that alias
runs. Only a human editing `checks.toml` grants a command execution authority
(EC-01, SE-07). Keep that in mind while designing your check: it will never
receive definition-supplied arguments beyond what the administrator wrote
into `argv` once, by hand.

Concretely, writing a check means producing one executable file that does one
bounded piece of work and exits quickly, reports its result on stdout in one
of two protocols, and lives somewhere an administrator can trust without
inspecting your source every reload — in that order, below.

## Where the binary should live

FTMON does not ship a checks directory, because the executable is never
FTMON's to manage — but the trust rules below only pass for a stable,
non-writable, ownership-appropriate path, so a shared convention avoids
everyone rediscovering the same constraints by trial and error:

- **Desktop / per-user installs:** `~/.local/lib/ftmon/checks/`
- **Dedicated-server installs:** `/usr/local/lib/ftmon/checks/`, owned by
  `root`

Both satisfy the trust contract below for the same reasons: the path is
stable and absolute (not a build directory, not `/tmp`); the owner is either
the account the daemon runs as (a user systemd unit's effective uid) or
`root` — exactly the two identities the trust check accepts; neither
location is group- or other-writable by default, so no other local account
can rewrite what the administrator approved; and neither sits under FTMON's
own `data`, `state`, or `runtime` directories (`~/.local/share/ftmon`,
`~/.local/state/ftmon`, the XDG runtime dir) — those are writable *by the
daemon itself*, so trusting an executable found there would turn a
compromise of FTMON's own storage into arbitrary command execution, and the
registry loader rejects them outright.

For a server install, create the directory and ship your script with
ownership and modes that already satisfy the next section:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/lib/ftmon/checks
sudo install -o root -g root -m 0755 check-myservice.sh \
    /usr/local/lib/ftmon/checks/check-myservice.sh
```

Separately packaged plugins (Monitoring Plugins under `/usr/lib/nagios/`,
etc.) don't need to move — register their existing path directly, per
[External checks](external-checks.md).

## The trust contract

Every check FTMON is asked to run — whether at registry load time or at
execution time — is checked against one shared predicate
(`ftmon.checks.trust.trusted_executable_path`), so a path that is trusted at
registration cannot later be swapped for something untrusted at run time. All
of the following must hold, or the check fails closed with a stable reason
instead of running:

| Condition | Why it exists |
| --- | --- |
| Path is absolute | No shell means no `PATH` search to fall back on; a relative path would also resolve differently depending on the daemon's working directory. |
| Not a symlink, and the resolved path equals the given path | An admin approves one exact file. A symlink is an indirection that could be repointed later without touching `checks.toml`. |
| A regular file | Rules out device nodes, FIFOs, and directories — nothing FTMON should ever `exec`. |
| Owned by `root` or the daemon's effective uid | Trust follows the identity actually executing the check (SE-07), not just the file's nominal owner. Distro packages under `/bin`, `/lib`, `/sbin`, `/usr` sometimes report the overflow uid (`nobody`/`nfsnobody`, 65533/65534) once a systemd unit sets `NoNewPrivileges=yes`, since the kernel masks real ownership from that vantage point — those specific system paths are still trusted, because distro packaging already protects them independently. |
| Not group- or other-writable | Otherwise any other local account could rewrite what the administrator approved. |
| Executable | Caught at registration instead of surfacing later as a mysterious runtime failure. |

The registry applies one more rule that is specific to *where* a check may
live, not to the file itself: the resolved executable must not fall under
FTMON's own data, state, or runtime directories, for the reason given in
"Where the binary should live" above.

If you are not sure why a candidate path is being rejected, run:

```sh
ftmon check trust /usr/local/lib/ftmon/checks/check-myservice.sh
```

It evaluates the same predicate the registry and runner use and prints every
condition that fails by name — absolute path, symlink-free, regular file,
trusted owner, no group/other write, executable — without ever executing the
candidate.

## Choosing an output protocol

FTMON accepts two check output protocols. Pick based on what you're writing,
not personal preference — both are first-class and get the same history,
rules and Trends once mapped.

- **`nagios`** — exit code `0`/`1`/`2`/`3` (OK/warning/critical/unknown) plus
  an optional `|`-delimited performance-data tail on the first stdout line.
  Choose this when you are wrapping an existing Monitoring Plugins-compatible
  binary, or want your script to double as a plugin for something else that
  already speaks this convention.
- **`ftmon-json`** — one UTF-8 JSON object on stdout, always with exit code
  `0`. Choose this for a new check: there is no perfdata mini-language to get
  subtly wrong, types are explicit, and a script bug that corrupts the object
  fails closed instead of silently mis-parsing.

### A minimal `nagios` check (POSIX sh)

```sh
#!/bin/sh
# check-tmp-usage: warn/critical on /tmp filesystem usage.
set -eu

warn=80
crit=90
pct=$(df --output=pcent /tmp | tail -n 1 | tr -dc '0-9')

perfdata="usage=${pct}%;${warn};${crit};0;100"

if [ "$pct" -ge "$crit" ]; then
    echo "CRITICAL - /tmp is ${pct}% full | ${perfdata}"
    exit 2
elif [ "$pct" -ge "$warn" ]; then
    echo "WARNING - /tmp is ${pct}% full | ${perfdata}"
    exit 1
else
    echo "OK - /tmp is ${pct}% full | ${perfdata}"
    exit 0
fi
```

The adapter (`ftmon.checks.nagios`) reads only the first stdout line, splits
it on the first `|`, and keeps the summary text before it as
`plugin_message`. Each perfdata token is `label=value[uom][;warn;crit;min;max]`
(or `'quoted label'=...` when the label has a space); a label can appear only
once per line — a repeated label is dropped as ambiguous rather than guessed
at. `warn`/`crit` may be a bare number, a `start:end` range, or `~:end`/`@…`
forms; anything that doesn't parse cleanly just drops that one field, it does
not discard the OK/warning/critical state. Exit codes outside `0`–`3` make
the whole result unknown.

### A minimal `ftmon-json` check (Python)

```python
#!/usr/bin/env python3
"""check-queue-depth: report a queue backlog as a structured FTMON check."""
import json
import sys

depth = 12  # replace with a real measurement

if depth >= 100:
    state, message = 2, f"queue depth {depth} (critical)"
elif depth >= 50:
    state, message = 1, f"queue depth {depth} (warning)"
else:
    state, message = 0, f"queue depth {depth}"

print(json.dumps({
    "schema": 1,
    "state": state,
    "message": message,
    "metrics": {
        "queue_depth": {"value": depth, "uom": "items"},
    },
}))
sys.exit(0)
```

The adapter (`ftmon.checks.jsoncheck`) is strict on purpose — it fails closed
to "unknown" rather than guess. The top-level keys must be **exactly**
`schema`, `state`, `message`, `metrics`; `schema` must equal `1`; `state`
must be an `int` (not `bool`) in `0`–`3`; `message` a `str`; `metrics` a
`dict` of at most 64 entries, each key a non-empty label and each value an
object with **exactly** `value` (a finite `int`/`float` — `NaN`/`inf`
rejected) and `uom` (a `str`). Duplicate JSON keys, non-UTF-8 output,
trailing data after the object, and output over 64 KiB all fail closed the
same way.

Unlike `nagios` mode, the exit code carries no meaning here — the check must
always exit `0` and put its result *in* the JSON. A non-zero exit is treated
as `unknown` regardless of what the object said, so don't `sys.exit(state)`.

In both protocols, only metric labels the monitor definition explicitly maps
(see [Definitions](definitions.md#external-checks)) become stored FTMON
metrics — an unmapped label is silently ignored, not auto-discovered. This is
what lets a plugin gain new output over time without growing the database
schema underneath you.

## Runtime contract

The runner (`ftmon.checks.runner.CheckRunner`, EC-02) invokes your
executable directly — never through a shell — so write for that:

- **Argv only, no shell.** Nothing expands globs, quotes arguments, or
  chains commands; whatever the administrator wrote in `argv` is exactly
  what your program receives via `sys.argv`/`$1 $2 …`.
- **Scrubbed environment.** The process gets `PATH` set to the platform
  default, plus `FTMON_CHECK_ALIAS` and `FTMON_CHECK_TIMEOUT` — nothing
  else. No inherited environment, no stdin (connected to `/dev/null`), no
  ambient secrets. Configuration beyond `argv` should come from a file path
  you control, not an environment variable.
- **Bounded output.** Only the first 64 KiB of stdout and 8 KiB of stderr
  are read; exceeding the stdout bound makes the whole result unknown.
  FTMON does not persist stderr at all — use it for your own debugging when
  running the check by hand, not as a channel FTMON reads.
- **Timeout is a hard kill.** The check runs in its own process group; on
  timeout it gets `SIGTERM`, then `SIGKILL` a quarter-second later if it
  hasn't exited. A check that spawns children and ignores `SIGTERM` still
  gets reaped — don't rely on cleanup code after the signal.
- **No elevated privilege.** The daemon itself stays unprivileged, and the
  shipped units set `NoNewPrivileges=yes`, so calling `sudo` from a check can
  never work — it fails before any sudoers rule is consulted. If a check
  genuinely needs root (SMART, RAID controllers), see "Checks requiring
  privilege" in [External checks](external-checks.md) for the privileged
  exporter pattern: a root timer snapshots the data to a file and the check
  parses it unprivileged, treating a stale file as unknown.

Practically: keep the check doing one bounded thing, avoid background work,
and print exactly one line (`nagios`) or one JSON object (`ftmon-json`) and
exit. A check that hangs, forks and detaches, or produces multi-line/streamed
output is fighting the contract, not working within it.

## Handing off to registration and mapping

Once the executable is written, installed, and passes `ftmon check trust`:

1. **Register the alias.** An administrator adds an `argv`/`protocol`/
   `timeout` entry to `checks.toml` — see
   [External checks](external-checks.md#register-a-check). This is a
   separate, human-only step; nothing in this guide grants that authority.
2. **Map its output.** The monitor definition declares
   `source = "external"`, the `check` alias, and a `perfdata`/metrics
   mapping from your output labels to FTMON metric names, units and kind —
   see the ["External checks" section of Definitions](definitions.md#external-checks).
   Only labels you actually emit, matched exactly (`plugin_uom` for
   `nagios`, `uom` for `ftmon-json`), need mapping; everything else is
   ignored.
3. **Validate, then let it reload.** `ftmon check` validates the definition
   and registry without running your check destructively; `ftmon doctor`
   reports readiness categories (`check_unavailable`, `registry_untrusted`,
   `executable_unready`, …) without ever printing argv or plugin output. The
   running daemon re-scans monitor and check-registry changes on its own
   within about 30 seconds (PM-04); send `SIGHUP` or run
   `ftmon monitor rescan` if you don't want to wait out the window while
   iterating.

If a run comes back `unknown`, work outward from the failure category
in [External checks' troubleshooting table](external-checks.md#troubleshooting)
rather than guessing — the daemon deliberately doesn't retain enough of a
failed run (no stderr, no argv in errors) to diagnose it after the fact, so
reproduce the check by hand as the FTMON user first.

# External checks

External checks let FTMON reuse a local script or a separately installed
Nagios-compatible plugin. The check answers what is happening now; FTMON adds
bounded history, confirmation, incidents, notifications, derived metrics and
declared Trends.

The executable registry is deliberately separate from monitor definitions.
An administrator grants one exact command an alias in `checks.toml`; TOML
definitions, drafts, MCP and the web interface may only reference that alias.
This separation means an AI-authored or web-approved definition cannot grant
itself command-execution authority (EC-01, SE-07).

## Register a check

Desktop installations use `~/.config/ftmon/checks.toml`, created as a private
empty registry by `ftmon init`. The file must be mode `0600` (owner read/write
only); group-writable registries are rejected.

### Install a curated recipe

Reviewed integrations under `extra-monitors/` ship with a tested monitor
definition and `checks.toml.example`. Install one without restarting the
daemon:

```sh
sudo apt install monitoring-plugins   # when the recipe uses check_http
ftmon recipe install http-tls
ftmon check
```

`ftmon check install http-tls` is an equivalent alias. The command merges the
recipe's check alias into `checks.toml`, writes the monitor TOML into
`monitors/` with `enabled = true`, and the daemon rescans within ~30 seconds
(PM-04). Use `--no-enable` to register authority without turning the monitor
on, or `--force` to replace an existing alias or monitor file.

Recipes are **not** bundled into the FTMON wheel. From a git checkout,
`ftmon recipe install http-tls` discovers `extra-monitors/` automatically.
Otherwise set a catalogue root once:

```sh
export FTMON_EXTRA_MONITORS=/path/to/ftmon/extra-monitors
ftmon recipe install http-tls
```

You can also pass the recipe directory directly:

```sh
ftmon recipe install /path/to/extra-monitors/http-tls
```

Adding a new recipe is adding a directory under that catalogue — no changes to
`pyproject.toml` or the core package.

### Manual registration

You can also merge a recipe by hand:

```toml
[check.website_https]
argv = [
  "/usr/lib/nagios/plugins/check_http",
  "-H", "example.org",
  "-S", "--sni", "-E", "-w", "1", "-c", "3", "-t", "8",
]
protocol = "nagios"
timeout = "9s"
```

The hardened server service instead reads `/etc/ftmon/checks.toml`. Its parent
must be root-owned mode 0755; the file must be root-owned, group `ftmon`, mode
0640. The service can read this authority but cannot edit it.

Every alias contains an explicit argument vector, not a shell command. The
first argument must be an absolute, regular, executable path owned by root or
the FTMON service user and not writable by group or other users. Under a user
systemd unit with `NoNewPrivileges=yes`, distro plugins under `/usr/` may
report the overflow `nobody` uid instead of root; FTMON accepts those system
paths when they remain non-writable. Symlinks and executables under FTMON's
writable data, state or runtime directories are rejected. Timeouts range from
1 to 30 seconds and default to 10 seconds.

After editing either file, validate without executing a check:

```sh
ftmon check
ftmon doctor
```

`doctor` reports only readiness and stable error categories. It never prints
the registered command, arguments, plugin output or credentials.

## Map Nagios performance data

The Nagios protocol uses exit states 0 OK, 1 warning, 2 critical and 3 unknown.
FTMON stores those as `plugin_state`, plus `plugin_ok`, `duration_s` and the
sanitized first-line `plugin_message`. Text following `|` is parsed as Nagios
performance data, but only explicitly mapped labels become metrics:

```toml
[monitor]
name = "website"
description = "Public website availability and response time"
version = 1
enabled = true
platforms = ["linux"]
interval = "60s"
source = "external"

[source_options]
check = "website_https"
entity = "https://example.org"

[[source_options.perfdata]]
label = "time"
metric = "response_time_s"
plugin_uom = "s"
unit = "seconds"
kind = "gauge"
scale = 1.0

[[rule]]
id = "unavailable"
when = "plugin_state == 2"
severity = "critical"
confirm_cycles = 2
message = "{plugin_message}"
```

`label` and `plugin_uom` must match the plugin output exactly. `metric`, `unit`
and `kind` define FTMON's stored schema; `scale` performs a finite numeric unit
conversion. Missing, malformed, duplicate, non-finite or wrong-unit values are
omitted without discarding the valid plugin state. Undeclared output is ignored
so a plugin upgrade cannot silently grow the database schema.

Mapped metrics are ordinary FTMON metrics. They can be used in derived
expressions and explicit Trends—for example, response-time slope and monotonic
growth confidence—without FTMON inferring meaning from a plugin label.

The tested [`check_http` recipe](../extra-monitors/http-tls/) explains the SNI,
timeout and threshold choices and includes deterministic plugin-output
fixtures. Certificate expiry should use a separate invocation: Monitoring
Plugins 2.3.5 emits certificate status and HTTP performance data on separate
lines when `-C --continue-after-certificate` is used, while FTMON deliberately
accepts Nagios performance data only from the first line.

## Write an FTMON JSON check

Use `protocol = "ftmon-json"` when a new script can emit typed values directly.
It must write one UTF-8 JSON object and exit zero:

```json
{
  "schema": 1,
  "state": 0,
  "message": "certificate healthy",
  "metrics": {
    "days_remaining": {"value": 42, "uom": "d"}
  }
}
```

The exact top-level keys are required. State is an integer from 0 to 3; metrics
contains at most 64 unique labels, each with exactly a finite numeric `value`
and string `uom`. Unknown keys, duplicate keys, booleans used as numbers,
additional JSON, invalid UTF-8 or output above 64 KiB fail closed as unknown.
The monitor still declares every metric mapping as shown above.

Checks receive no stdin and only a minimal PATH plus `FTMON_CHECK_ALIAS` and
`FTMON_CHECK_TIMEOUT`. They run without a shell, inherited environment, open
file descriptors or root privileges. Their working directory is FTMON's state
directory. Do not depend on ambient environment variables.

## Checks requiring privilege

Keep the FTMON daemon unprivileged. If a read-only SMART, RAID or hardware check
genuinely needs elevation, register a root-owned wrapper and grant only that
exact wrapper through `sudoers`:

```sudoers
ftmon ALL=(root) NOPASSWD: /usr/local/libexec/ftmon/check-smart-health
```

```toml
[check.smart_health]
argv = [
  "/usr/bin/sudo", "-n",
  "/usr/local/libexec/ftmon/check-smart-health",
]
protocol = "nagios"
timeout = "15s"
```

`-n` makes missing authorization fail immediately rather than wait for a
password. The wrapper and every parent directory must be root-owned and not
writable by `ftmon`. Do not use wildcards, a shell, operator-controlled
arguments, broad `sudo` membership or an unrestricted plugin directory.
Validate the policy with `visudo -c`. Remediation actions are a separate trust
boundary and should not be hidden inside a read-only monitoring check.

## Credentials and third-party plugins

Arguments are configuration, not a secret transport. Never put passwords,
tokens, URL user-info or private keys in `argv`, definitions or plugin output.
If a plugin supports its own protected configuration file, an administrator
may register that non-secret path; the plugin remains responsible for its file
format and credential handling.

FTMON implements a bounded local compatibility convention, not NRPE or the
entire Nagios configuration model. Plugins are installed and licensed
separately and are never copied into the MIT-licensed FTMON package. Confirm an
upstream plugin's licence, maintenance status, required environment, privilege
needs, multiline-output behavior and secret-handling model before registering
it.

## Troubleshooting

| Result | Check |
| --- | --- |
| `check_unavailable` | Alias and registry status |
| `registry_untrusted` | Registry ownership and modes |
| `executable_unready` | Executable path, owner and mode |
| Plugin state unknown | Run its argv as the FTMON user |
| Mapped metric missing | Compare its label and UOM |
| Timeout | Check runtime and configured timeout |

FTMON intentionally does not persist plugin stderr. Diagnose a new integration
manually as the service user before enabling its monitor, then rely on
`plugin_state`, self metrics, incidents and `doctor` for ongoing operation.

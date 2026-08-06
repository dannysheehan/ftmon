# FTMON monitor definitions — the complete reference (DO-01)

This is the one document you need to write a monitor, whether you are a
person editing TOML or an AI calling `define_monitor`. It is exposed as the
MCP resource `ftmon://docs/definitions`.

A monitor is one TOML file in `~/.config/ftmon/monitors/`. The daemon
notices new/changed files within 30 seconds. `ftmon check` validates
everything and its errors say *what*, *where*, and *how to fix it* — trust
them.

## Authoring traps (read before `define_monitor`)

Validation catches schema errors; it cannot catch these behavioral contracts.
Read this list first when writing or reviewing a definition via MCP.

| Trap | What to do |
| --- | --- |
| **Trends are opt-in** | Slope/growth *rules* do not register Trends. If operators should open `/trends`, declare `[[trend]]` with value/rate metrics, units, and threshold params. |
| **Glance ≠ Trends ≠ incidents** | Glance = dashboard current value. Trends = investigation charts. Rules = incidents. None imply the others. |
| **`incident_group` needs `group`** | Overlay incidents on Trends only when the profile's `incident_group` matches a rule `group`. |
| **Signed rate units** | `slope()` is per-second; convert in the derived expr (e.g. `* 3600` for per-hour). Units are labels, not converters. |
| **`monot()` is rise-biased** | Fine for leak/temp-up. For bidirectional series, prefer `coverage()` over `monot()` as confidence. |
| **External `plugin_state` rules** | Always alert on `plugin_state == 3` (check health). Use state 1/2 **or** metric thresholds — do not double-threshold inconsistently. |
| **No argv in definitions** | Alias only; admin owns `checks.toml`. Writing a check executable? See `ftmon://docs/check-authoring` (always exit 0 for ftmon-json). |
| **Native units / `scale`** | Store what the plugin emits; `scale` is a rare real conversion, not a unit label. |
| **Unmapped labels vanish** | Only `[[source_options.perfdata]]` mappings persist (EC-04). |
| **`coverage()` with windows** | Pair `slope`/`avg`/`monot` with `coverage(...) >= …` when the window must be represented. |
| **Unknown ≠ false** | Unknown freezes confirm/clear; do not `coalesce` away gaps unless intentional (EX-06). There is no `is not None`. |
| **Drafts never run** | `define_monitor` writes drafts only; approve with CLI/web. Use `diagnose_monitor` / `monitor_paths`. |
| **Event vs sampler rule keys** | Events use cooldown/`clear_after`; no glance/trend on event monitors. |
| **TOML `exempt` placement** | Top-level arrays before the first `[table]`, or they attach silently. |
| **Thresholds as `[parameters]`** | Prefer parameters over literals in `when` so glance/Trends threshold lines stay honest. |

## 1. Shape of a definition

```toml
schema = 1

# TOML gotcha: top-level arrays like `exempt` MUST appear before the first
# [table] header, or TOML silently attaches them to that table.
exempt = [ 'matches(name, "^(gcc|clang|ffmpeg)$")' ]

[monitor]
name = "leak"                   # [a-z][a-z0-9_]{1,31}, unique
description = "Rising RSS without release"   # <= 200 chars
version = 1                     # integer, bump on meaningful change
enabled = true
platforms = ["linux"]
interval = "60s"                # how often to sample; minimum "15s"
source = "process"              # where entities and metrics come from

[parameters]                    # user-tunable knobs, referenced by name
warn_bph = { value = 10000000, doc = "warn at this many bytes/hour" }

[glance]                        # optional current-value tile readout
metric = "growth_bph"
unit = "bytes/hour"
aggregate = "max"
thresholds = [
  { label = "warn", parameter = "warn_bph" },
]

[[derived]]                     # computed metrics; can window over history
name = "growth_bph"
expr = 'slope(rss_bytes, "15m") * 3600'

[[rule]]
id = "grow"
when = 'growth_bph > warn_bph'
severity = "warning"            # info|notice|warning|error|critical
confirm_cycles = 3              # consecutive TRUE cycles before opening
clear_cycles = 3                # consecutive FALSE cycles before clearing
message = "{entity} sustained RSS growth: {growth_bph:.0f} B/h (warn at {warn_bph})"
```

Section reference:

| Key / table | Required | Notes |
| --- | --- | --- |
| `schema` | yes | always `1` |
| `[monitor]` | yes | `name, description, version, source, platforms` required; `interval` for sampler sources (min 15 s) |
| `[source_options]` | no | source-specific: `watchlist` (unit/net), `top_n` 5–50 (process), `channels`/`store_min_severity` (events) |
| `[parameters]` | no | each entry `{ value = <number>, doc = "..." }`; the doc is mandatory kindness |
| `[glance]` | no | sampler-only explicit primary metric, unit, entity aggregate and labelled threshold parameters; see below |
| `[[derived]]` | no | `name`, `expr`; may reference earlier deriveds (evaluation is dependency-ordered) |
| `exempt` | no | top-level array of boolean expressions; a TRUE prevents rules and all persistent metric/baseline history for that entity |
| `[promotion]` | no | process source only: `expr` marking entities worth persisting beyond the top-N |
| `[[rule]]` | yes (≥1) | see below |
| `[[trend]]` | no | validated presentation profile joining persisted value/rate metrics; see below |

### Dashboard glance readouts

`[glance]` is optional and sampler-only. It tells the dashboard which one
already-persisted value can honestly summarize a monitor; it never changes
sampling, rules, incidents, or health color. Nothing is guessed from rule or
metric names. The dashboard draws a linear threshold meter for percent glances
(0–100) and for other units that declare thresholds (axis 0…highest limit).

```toml
[glance]
metric = "used_pct"
unit = "percent"
aggregate = "max"
thresholds = [
  { label = "warn", parameter = "space_warn_pct" },
  { label = "error", parameter = "space_crit_pct" },
]
```

| Key | Required | Meaning |
| --- | --- | --- |
| `metric` | yes | existing persisted raw or derived metric |
| `unit` | yes | display unit, 1–32 characters; `percent` renders as `%` |
| `aggregate` | yes | `max` or `min` across each active entity's latest value |
| `thresholds` | no | up to four ordered `{label, parameter}` entries; labels and parameters must each be unique |

The dashboard omits the line when the daemon or sample is stale, the monitor is
disabled or invalid, or no active non-exempt entity has that metric. The same
`exempt` expressions that suppress rules also remove entities from glance
aggregation. It never substitutes a retained rollup or a value from an entity that has disappeared. Units are
display metadata only and do not convert stored values.

Rule keys — sampler sources (`process`, `disk`, `system`, `unit`, `net`,
`self`):

| Key | Default | Meaning |
| --- | --- | --- |
| `id` | required | `[a-z][a-z0-9_]*`, unique in the monitor |
| `when` | required | boolean expression (section 2) |
| `severity` | required | `notice`, `warning`, `error`, or `critical` |
| `confirm_cycles` | 3 | consecutive TRUE evaluations before the incident opens — the main noise defense |
| `clear_cycles` | 3 | consecutive FALSE evaluations before it clears |
| `group` | rule id | rules sharing a `group` form a severity *ladder*: one incident that escalates/downgrades instead of stacking |
| `message` | required | template, ≤200 chars; `{any_metric}`, `{any_param}`, `{entity}`, `{monitor}`, `{severity}`; `:.0f`-style format specs allowed; missing values render as `n/a` |
| `action` | none | executable bare filename under `~/.config/ftmon/actions/`; runs only when the incident opens, at most once/10m |
| `notify_recovery` | true | send the one recovery notification on clear |

Rule keys — the `events` source (episode rules, different lifecycle):

| Key | Default | Meaning |
| --- | --- | --- |
| `cooldown` | `"10m"` | minimum gap between re-notifications; repeats inside it just count up ("12x since open") |
| `clear_after` | `"30m"` | quiet period with no matching event that closes the episode (silently) |
| `confirm_count` | 1 | events needed within `confirm_window` before opening |
| `confirm_window` | none | window for `confirm_count` |
| `notify_recovery` | **false** | episodes close silently by default — "the log went quiet" is not news |

### Trend profiles

`[[trend]]` is optional and sampler-only. It declares how already-persisted
metrics belong together in the Trends UI; it does not evaluate expressions or
cause additional collection. Presentation is explicit because names alone
cannot establish units, thresholds, confidence, or whether projection is
meaningful.

| Key | Required | Meaning |
| --- | --- | --- |
| `id` | yes | unique profile id (`[a-z0-9-]{1,32}`) |
| `kind` | yes | `growth` or `capacity` |
| `title` | yes | human label, at most 80 characters |
| `value_metric`, `value_unit` | yes | primary persisted metric and display unit |
| `rate_metric`, `rate_unit` | yes | signed persisted rate and its unit |
| `confidence_metric` | no | persisted fraction from 0 to 1 |
| `confidence_threshold_param` | with confidence | parameter qualifying confidence |
| `remaining_metric` | capacity only | remaining quantity in the rate's base unit |
| `value_threshold_params` | no | parameters drawn on the value panel |
| `rate_threshold_params` | no | parameters drawn on the rate panel |
| `incident_group` | no | only overlay incidents from this rule group |

Growth profiles normally omit projection. Capacity profiles require a remaining
metric and may qualify projection through confidence. Every metric and parameter
reference is checked by `ftmon check`. Units are labels, not conversion rules,
so a derived metric must already use the declared unit.

## 2. The expression language

Expressions are a small, safe subset of Python syntax: comparisons,
`and/or/not`, arithmetic (`+ - * / %`), parentheses, function calls from
the table below. No attribute access, no subscripts, no lambdas, no
imports — if `ftmon check` rejects a construct, it is not in the language.

Names resolve to, in order: the source's **metrics** (e.g. `rss_bytes`),
your **derived** metrics, your **parameters**, the source's **attrs**
(strings, e.g. `name`, `cmdline`), and the built-in constants.

Constants: `KB MB GB TB` (powers of 1024) and the severity names
`info notice warning error critical` (0–4). Literals: numbers, strings,
`True/False/None`.

### Functions

| Function | Returns | Notes |
| --- | --- | --- |
| `last(m)` | latest sample of metric `m` | same as the bare name `m` |
| `avg(m, "5m")` `min(m, "5m")` `max(m, "5m")` | aggregate over the window | window is a duration string; max 6 h |
| `delta(m, "30m")` | last − first over window | for counters: raw increase |
| `rate(m, "5m")` | per-second rate | counter-aware: a counter reset yields unknown, not a negative spike |
| `slope(m, "15m")` | least-squares slope per second | needs ≥3 points; the leak detector |
| `monot(m, "15m")` | fraction of steps that increased, 0..1 | 1.0 = strictly rising; noise-tolerant leak signal |
| `coverage(m, "45m")` | fraction of the window actually observed, 0..1 | windows are maximums — a `"45m"` slope can rest on 3 samples; require coverage when the verdict needs the window represented |
| `age(m)` | seconds since `m` was last sampled | |
| `baseline(m)` | learned normal (EW mean, ~3-day half-life) | unknown for the first ~24 h of data |
| `pct(a, b)` | `100*a/b`, unknown if `b` is 0 | |
| `abs(x)` `roundv(x, n)` `clamp(x, lo, hi)` | arithmetic helpers | |
| `coalesce(x, default)` | `x` unless it is unknown | escape hatch when unknown-propagation is not what you want |
| `matches(s, "^regex$")` | regex search on a string | regex must be a literal, ≤512 chars |
| `contains(s, sub)` | substring test | |
| `during("09:00-18:00")` | true inside the local-time window | window may cross midnight |
| `dow()` | `"mon"`..`"sun"` | |

Durations: `"90s"`, `"10m"`, `"3h"`, `"2d"`.

### Unknown is not false (the EX-06 truth table)

Any value that cannot be computed — process too new for a window, PSI not
available, baseline still learning — is **unknown** (`None`). Unknown
propagates: `None > 5` is unknown, `unknown and True` is unknown,
`unknown or True` is True, `not unknown` is unknown. A rule **fires only
when it evaluates to exactly True**; unknown neither fires nor counts
toward clearing — it freezes the rule's counters. This is why a freshly
booted machine is silent instead of wrong. Use `coalesce()` when you
really want a default.

### Per-source names

Run `ftmon monitors` / read the built-ins for live examples. Summary:

| Source | Entities | Metrics | Attrs |
| --- | --- | --- | --- |
| `process` | every process (track-all + top-N/promoted persistence) | `cpu_pct rss_bytes num_fds fd_limit_soft num_threads io_read_bytes io_write_bytes` | `name cmdline username exe exe_base display cmd_hint` |
| `disk` | mounts | `total_bytes used_bytes free_bytes used_pct inode_used_pct` | `fstype device` |
| `system` | one (`system`) | `load1 load5 load15 cpu_pct mem_* swap_used_pct psi_some_*` | `hostname` |
| `unit` | watchlist targets | `present restarts` | `unit kind` |
| `net` | `totals` + watchlist listeners | `conn_total conn_established conn_time_wait conn_listen present` | `proto port` |
| `events` | episodes (see below) | `severity` | `provider event_id message source` |
| `self` | the daemon | `cpu_pct rss_bytes db_bytes db_file_bytes db_used_bytes db_freelist_bytes db_headroom_bytes cycle_s tick_overruns event_* ring_mem_bytes entities_persisted ...` | — |

`fd_limit_soft` is the process soft `RLIMIT_NOFILE`; it is omitted when denied,
unsupported, zero, or infinite so expressions like `pct(num_fds, fd_limit_soft)`
stay unknown rather than bogus. Prefer `pct(...)` (0–100) over a raw ratio.

## 3. Event rules and episodes

`source = "events"` rules run against the **live journal stream** (before
the store-filter, so they may match info-level entries). A match opens an
*episode* keyed by `(rule, provider, event_id or message-shape)` — similar
messages differing only in numbers ("Killed process **4001**") collapse
into one episode that counts occurrences. Canonical fields are the same on
every platform (`event_id` is a string; journald has none, Windows Event
Log will), so an event rule written today works unchanged when other
platforms land.

```toml
[[rule]]
id = "oom"
when = 'provider == "kernel" and contains(message, "Out of memory")'
severity = "critical"
cooldown = "5m"
clear_after = "30m"
message = "OOM killer fired: {message}"
```

### Windows Event Log channels and filtering

Windows only (journald has one fixed stream — no channel concept). By
default the event reader watches just `System` and `Application`. Add
`[source_options].channels` to subscribe to more — Security, `Microsoft-
Windows-PowerShell/Operational`, `Microsoft-Windows-Sysmon/Operational`, and
so on:

```toml
[[source_options.channels]]
path = "Security"
query = '*[System[(EventID=4688 or EventID=4689)]]'

[[source_options.channels]]
path = "System"
```

`query` is optional Windows Event Log XPath (the same query language
`EvtQuery`/`wevtutil`/`Get-WinEvent -FilterXPath` use — not a Windows Event
Forwarding/Collector feature, it works on a single local host). It filters
*at the subscription*, before anything reaches ftmon's own rules or the
store-filter — omit it to receive everything on that channel, which is
fine for low-volume channels but will flood a busy one like `Security`.
Supported operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `AND`, `OR`, plus
`Band()` for `Keywords` bitmasks and `timediff()` for relative time
windows. An invalid channel name or malformed query is isolated to that
one channel (the rest keep working) and reported once as a self-event —
check `ftmon events` if a channel you added isn't showing anything.

Channels are unioned across every loaded event monitor — there is one
shared subscription for the whole daemon, not one per monitor — and are
read once, when the event reader first starts. **Changing `channels` on an
already-running daemon needs a restart**, unlike ordinary rule edits: `ftmon
monitor rescan`/`SIGHUP` picks up new/changed rules within 30 s (PM-04), but
will not re-subscribe. Subscribing to a channel is also not enough to see
its events by itself: routine Security-audit-success entries are typically
low severity and get dropped by the store-filter unless a `[[rule]]`
explicitly matches them (or you lower `store_min_severity` below) — pair a
new channel with rules that care about it.

`store_min_severity` overrides the default store-filter threshold
(`notice`) for events that don't match any rule — a severity name or 0–4
int:

```toml
[source_options]
store_min_severity = "warning"
```

#### Curated starting point: Security-Auditing and PowerShell visibility

Hand-writing good `channels`/`query`/rule combinations is real work, so
`ftmon init --profile windesktop|winserver` ships one curated example —
`events_security.toml` — as a **draft** (`monitors/drafts/`, never loaded
until you run `ftmon monitor approve events_security` or approve it in the
web UI). It's a draft rather than an enabled monitor for two reasons:
subscribing to the Security log is sensitive (it can surface logon,
privilege, and account-management activity for every user of the machine),
and every event it watches for depends on Windows audit policy that is
**off by default** — approving the draft alone does nothing until you also
turn on the relevant audit subcategories (and, for PowerShell script-block
content, a separate Group Policy setting). The file's own header comment
has the exact `auditpol` commands and GPO path, plus the reasoning behind
which events it does and deliberately doesn't include (event 4688/process
creation is left out of the default set — it needs a second policy on top
of the base one to be useful, and is materially higher-volume than
everything else there). Read the file before approving it; that's the
point of it being a draft.

### External checks

`source = "external"` references an administrator-approved alias rather than
an executable path:

```toml
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
```

`check` and `entity` are required. Up to 32 mappings may declare a unique
plugin label and unique destination metric. `plugin_uom` must match exactly;
`unit` and `kind = "gauge"|"counter"` become FTMON's schema, and optional
finite `scale` defaults to 1. Fixed names are `plugin_state`, `plugin_ok`,
`duration_s`, and string attribute `plugin_message`. Mapped metric names enter
the expression environment before derived expressions, rules and Trends are
validated (MD-11).

**Values are stored in the unit the plugin emits.** `check_disk` reports
bytes, so the metric stores bytes; `check_iowait` reports percent, so it
stores percent. The metric name and `unit` describe what is actually stored —
they never trigger a conversion. `scale` exists only for the rare genuine
conversion (a plugin whose output unit you cannot use directly) and is
multiplied into the stored value; do not set `scale = 1048576` because
`unit = "bytes"` sits next to a plugin that already emits bytes — that stores
every sample a million-fold wrong and the bad rows have to be purged. When in
doubt, leave `scale` out and name the metric after the plugin's native unit
(`used_bytes`, `cpu_iowait_pct`), as every shipped recipe does.

The definition cannot contain argv or executable paths. Register the alias in
the separate `checks.toml` authority described by
[External checks](external-checks.md) (`ftmon://docs/external-checks`); to write
a new check executable, see [Writing an external check](check-authoring.md)
(`ftmon://docs/check-authoring`). Drafts may reference a future alias, but
approval and active validation fail until an administrator creates it.

## 4. Cookbook

Complete recipes below are marked `` ```toml recipe=<id> `` so CI can
`load_text` them. Unmarked fences are fragments or non-FTMON examples and are
not validated that way. Recipe bodies must not contain a line that is exactly
`` ``` `` (that always closes the outer fence).

### Per-process FD utilization (`fd-pct`)

**When to use:** alert when a process approaches its soft open-file limit.
`cpu_pct` is unrelated (and can exceed 100 as percent of one core).

```toml recipe=fd-pct
schema = 1

[monitor]
name = "fds"
description = "Per-process open-file utilization against the soft RLIMIT_NOFILE"
version = 1
platforms = ["linux"]
interval = "60s"
source = "process"

[parameters]
warn_pct = { value = 80, doc = "Warn when open FDs exceed this percent of the soft limit" }

[glance]
metric = "fd_pct"
unit = "percent"
aggregate = "max"
thresholds = [
  { label = "warn", parameter = "warn_pct" },
]

[[derived]]
name = "fd_pct"
expr = "pct(num_fds, fd_limit_soft)"

[[rule]]
id = "fd-high"
when = "fd_pct > warn_pct"
severity = "warning"
confirm_cycles = 3
message = "{entity} open files at {fd_pct:.0f}% of soft limit (warn {warn_pct})"
```

`fd_limit_soft` may be absent; then `fd_pct` is unknown and the rule does not
fire (EX-06). Thresholds are percent values (80), not fractions (0.8).

### Host pressure over time (`aggregate-pressure`)

**When to use:** detect whole-host CPU/swap pressure on the single `system`
entity. FTMON expressions do **not** aggregate across process entities; this
recipe is time-window pressure on host series only.

```toml recipe=aggregate-pressure
schema = 1

[monitor]
name = "host_pressure"
description = "Host CPU and swap pressure over short windows (single system entity)"
version = 1
platforms = ["linux"]
interval = "60s"
source = "system"

[parameters]
cpu_warn = { value = 85, doc = "Warn when 5m average host CPU percent exceeds this" }
swap_warn = { value = 25, doc = "Warn when 10m average swap used percent exceeds this" }

[glance]
metric = "cpu_5m"
unit = "percent"
aggregate = "max"
thresholds = [
  { label = "warn", parameter = "cpu_warn" },
]

[[derived]]
name = "cpu_5m"
expr = 'avg(cpu_pct, "5m")'

[[derived]]
name = "swap_10m"
expr = 'avg(swap_used_pct, "10m")'

[[rule]]
id = "cpu-pressure"
group = "pressure"
when = 'cpu_5m > cpu_warn and coverage(cpu_pct, "5m") >= 0.8'
severity = "warning"
confirm_cycles = 3
message = "Host CPU pressure {cpu_5m:.0f}% over 5m"

[[rule]]
id = "swap-pressure"
group = "pressure"
when = 'swap_10m > swap_warn and coverage(swap_used_pct, "10m") >= 0.8'
severity = "warning"
confirm_cycles = 3
message = "Host swap usage {swap_10m:.0f}% over 10m"
```

### Optional metrics stay unknown (`optional-metric`)

**When to use:** a metric may be missing (platform, privilege, or sampler
omission). There is no `is not None`; comparisons against unknown stay unknown
and freeze confirm/clear instead of firing.

```toml recipe=optional-metric
schema = 1

[monitor]
name = "optional_fds"
description = "FD utilization that stays quiet when fd_limit_soft is absent"
version = 1
platforms = ["linux"]
interval = "60s"
source = "process"

[parameters]
warn_pct = { value = 90, doc = "Warn percent of soft FD limit" }

[[derived]]
name = "fd_pct"
expr = "pct(num_fds, fd_limit_soft)"

[[rule]]
id = "fd-optional"
when = "fd_pct > warn_pct"
severity = "warning"
confirm_cycles = 2
message = "{entity} FD utilization {fd_pct:.0f}%"
```

Do not wrap optional metrics in inventing defaults unless you intentionally
want gaps to count as clear.

### Process matching across restarts (`process-match`)

**When to use:** target processes by stored attributes. Each `entity_id` is
still one process lifetime (DM-02); matching `name` / `exe_base` / `display`
does not merge rows across restarts.

```toml recipe=process-match
schema = 1

# Match workers by executable basename; identity remains name:pid:create_time.
exempt = [
  'matches(exe_base, "^(gcc|clang|cargo)$")',
]

[monitor]
name = "worker_rss"
description = "Rising RSS for named worker processes (per lifetime entity)"
version = 1
platforms = ["linux"]
interval = "60s"
source = "process"

[parameters]
warn_bph = { value = 10000000, doc = "Warn bytes/hour of RSS growth" }

[promotion]
expr = 'matches(name, "^worker") or matches(exe_base, "^worker")'

[[derived]]
name = "growth_bph"
expr = 'slope(rss_bytes, "15m") * 3600'

[[rule]]
id = "worker-grow"
when = 'matches(name, "^worker") and growth_bph > warn_bph and coverage(rss_bytes, "15m") >= 0.8'
severity = "warning"
confirm_cycles = 3
message = "{display} RSS rising {growth_bph:.0f} B/h"
```

`exe_base` and `cmd_hint` may be absent on some entities; then
`matches(exe_base, ...)` is unknown (EX-06), not a silent false. `display` is
always set (exe_base when distinct from name, otherwise name).

### Alert when a log pattern appears

Use the rule above; adjust `provider` and the `contains`/`matches` test. For a
specific platform event id: `when = 'event_id == "6008"'`.

### Monitor a non-standard log file with Fluent Bit

FTMON deliberately does not implement arbitrary file tailing. Rotation,
truncation, persistent offsets, multiline records, long lines, encodings, and
backpressure are a separate reliability problem already handled by Fluent
Bit's [`tail` input](https://docs.fluentbit.io/manual/data-pipeline/inputs/tail).
The recommended arrangement is:

```text
application log file -> Fluent Bit tail/parser -> service stdout -> journald
                                                              -> FTMON events
```

The following classic Fluent Bit configuration is a starting point. Replace
the path and tag, use a separate `DB` file for each tail input, and add a
documented multiline parser when the application emits stack traces:

```ini
[SERVICE]
    Flush              1
    Log_Level          info
    Parsers_File       parsers.conf

[INPUT]
    Name               tail
    Tag                ftmon.myapp
    Path               /var/log/myapp/*.log
    Exclude_Path       /var/log/myapp/*.gz
    DB                 /var/lib/fluent-bit/ftmon-myapp-tail.db
    Read_From_Head     Off
    Refresh_Interval   10
    Rotate_Wait        30
    Skip_Long_Lines    On
    Mem_Buf_Limit      5MB

[OUTPUT]
    Name               stdout
    Match              ftmon.myapp
    Format             json_lines
```

The stdout output is intentional: the packaged Fluent Bit systemd service has
its stdout captured by journald. It is not a claim that Fluent Bit has a native
journald output plugin. Confirm the resulting journal identity and message
before writing the FTMON rule:

```sh
sudo systemctl restart fluent-bit
journalctl -u fluent-bit.service -n 20 -o json-pretty
```

The provider is commonly `fluent-bit`, but use the actual
`SYSLOG_IDENTIFIER` or `_SYSTEMD_UNIT` shown by `journalctl`. The JSON record is
the event `message`, so an episode rule can select only the application pattern
that matters even though the journal priority of service stdout is normally
`info`:

```toml
[[rule]]
id = "myapp-database-errors"
when = 'provider == "fluent-bit" and contains(message, "database unavailable")'
severity = "error"
cooldown = "10m"
clear_after = "30m"
message = "My application reported a database failure: {message}"
```

Event rules are evaluated before FTMON's severity store-filter, so a matching
info-level forwarded record is retained automatically; do not lower
`store_min_severity` merely to make this recipe work. Grant Fluent Bit only the
group or ACL access needed for the selected files, exclude rotated archives to
avoid duplicate reads, and keep sensitive fields out of the emitted record.
FTMON does not configure, supervise, or test Fluent Bit—the integration is an
operator-recommended path for logs that are not already in journald.

### Alert when anything grows steadily (memory leak)

```toml
[[rule]]
id = "grow"
# Windows are maximums: a "15m" slope will happily fire on three samples.
# coverage() makes the rule wait until the window is actually represented.
when = 'slope(rss_bytes, "15m") * 3600 > warn_bph and coverage(rss_bytes, "15m") >= 0.8'
severity = "warning"
confirm_cycles = 5
message = "{entity} rss rising {growth_bph:.0f} B/h for 15m+"
```

### A severity ladder (one incident, not three)

```toml
[[rule]]
id = "warn"
group = "space"
when = 'used_pct > 85'
severity = "warning"
message = "{entity} at {used_pct:.0f}%"

[[rule]]
id = "crit"
group = "space"
when = 'used_pct > 97'
severity = "critical"
message = "{entity} nearly full: {used_pct:.0f}%"
```

### Compare against learned normal instead of a magic number

```toml
when = 'conn_total > baseline(conn_total) * 4'
```

Silent for the first day (baseline unknown), then tuned to *your* machine.

### Watch a service, but only during working hours

```toml
[source_options]
watchlist = [ { unit = "backup.service", during = "09:00-18:00" } ]
```

### Exempt the legitimate heavy hitters

```toml
exempt = [ 'matches(name, "^(gcc|clang|cargo|ffmpeg)$")',
           'username != "myuser"' ]
```

Exempt entities are sampled only in the daemon's bounded in-memory context so
the exemption can be evaluated. They do not alert and are not persisted, so
they do not appear in Metrics, Trends, Baselines, glance, or historical
`top_consumers` results. If an entity becomes exempt after accumulating data,
that monitor/entity's stored samples, rollups and baselines are removed on the
next successful tick.

## 5. Authoring via MCP (`define_monitor`)

`validate_monitor` checks a definition without writing anything.
`define_monitor` writes a **draft** to `monitors/drafts/` — drafts are
never loaded by the daemon. A human approves with
`ftmon monitor approve <name>` (or the web UI). Iterating on a draft
overwrites it; a name that already exists as a real monitor is refused.
Validation errors come back as `{path, code, message, hint}` — fix and
resubmit. Read the authoring traps above before the first draft.

### `query_metrics.filter_expr` (attribute-only)

MCP `query_metrics` accepts an optional `filter_expr` that selects entities by
**stored attributes** in `entities.attrs` — not by metric values. Expressions
such as `cpu_pct > 50` are invalid here. Available attribute names depend on
the monitor source and what has been sampled; a compile error lists attrs
observed for that monitor. For process name/PID discovery across history, use
`get_process_history(name_or_pid)` first.

Marked examples below are compiled by CI against a representative process
attribute environment (including optional `exe_base`):

```expr filter-example
matches(name, "^chrome$")
```

```expr filter-example
matches(exe_base, "^(python|node)$")
```

```expr filter-example
username == "alice" and matches(name, "worker")
```

Optional attrs such as `exe_base` may be missing on some entities; then
`matches(exe_base, ...)` is unknown (EX-06), not a silent false. Filtering
never merges process lifetimes: each matching `entity_id` remains one
`name:pid:create_time` row (DM-02).

# FTMON monitor definitions — the complete reference (DO-01)

This is the one document you need to write a monitor, whether you are a
person editing TOML or an AI calling `define_monitor`. It is exposed as the
MCP resource `ftmon://docs/definitions`.

A monitor is one TOML file in `~/.config/ftmon/monitors/`. The daemon
notices new/changed files within 30 seconds. `ftmon check` validates
everything and its errors say *what*, *where*, and *how to fix it* — trust
them.

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
| `[source_options]` | no | source-specific: `watchlist` (unit/net), `top_n` 5–50 (process), `store_min_severity` (events) |
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
metric names.

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
| `process` | every process (track-all + top-N/promoted persistence) | `cpu_pct rss_bytes num_fds num_threads io_read_bytes io_write_bytes` | `name cmdline username exe` |
| `disk` | mounts | `total_bytes used_bytes free_bytes used_pct inode_used_pct` | `fstype device` |
| `system` | one (`system`) | `load1 load5 load15 cpu_pct mem_* swap_used_pct psi_some_*` | `hostname` |
| `unit` | watchlist targets | `present restarts` | `unit kind` |
| `net` | `totals` + watchlist listeners | `conn_total conn_established conn_time_wait conn_listen present` | `proto port` |
| `events` | episodes (see below) | `severity` | `provider event_id message source` |
| `self` | the daemon | `cpu_pct rss_bytes db_bytes cycle_s tick_overruns event_* ring_mem_bytes ...` | — |

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
resubmit.

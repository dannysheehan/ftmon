# FTMON v2 — Specification

Status: **DRAFT v0.48** — v0.48 extends DM-04's durable/process retention
split to the 5-minute tier and makes DM-05 degradation an observable state
rather than a stream of per-pass events: a live desktop was degrading on a
quarter of all retention passes indefinitely, holding raw retention at ~24 h
against DM-04's stated 48 h, with no signal distinguishing that from a single
prune (DM-04, DM-05, RB-02, issue #102). v0.47 ships the Windows per-user MSI and Task
Scheduler service-wrapper helpers (issues #94/#95): DO-02 now requires the
self-contained x64 installer, silent install/upgrade/repair/uninstall
guidance, and the explicit Task Scheduler lifecycle; PyPI/`uv` remains
supported. v0.46 makes the self-monitor's budget signals mean
what the requirements they police actually say: DM-05 is measured on used
pages everywhere it is reported, its alarm sits above the enforcement target
rather than at it, DM-16 pressure counts entities that are persisted rather
than merely running, and unrelated budgets stop sharing one incident group
(DM-05, RB-02, DM-16, CL-05, issue #104). v0.45 extends PM-10's survive-the-lock discipline to
the background notification dispatcher (PM-12): a store fault inside the worker
thread must recover rather than silently kill the only delivery path, and a
dead worker or overdue claimable backlog must fail `ftmon doctor` instead of
reading as healthy from channel configuration alone (PM-12, NO-10, CL-05,
issue #98). v0.44 completes issue #74 with explicit database
pressure diagnostics and a bounded live-compaction policy: DM-05 measures used
pages, keeps incremental vacuum in the retention pass, and rejects automatic
full `VACUUM` while monitoring because its exclusive lock is disproportionate
for a 200 MB local store; CL-05 distinguishes file allocation, used bytes,
freelist headroom, and degradation recency (DM-05, CL-05, issue #74). v0.43
makes MD-09's catalog lifecycle concrete: a
`gone` entity (and its series/baselines) now reaps once its observations age
out and no open incident references it, closing the gap where dead process
identities counted against the DM-05 budget forever; CL-05 doctor gains
active-vs-total catalog visibility against the DM-16 worksheet (MD-09, CL-05,
DM-16, issue #74). v0.42 exposes the dashboard's bounded primary
readouts through MCP `get_status` (`glances`, ≤64, deterministic truncation
metadata), unifies the daemon-staleness boundary shared by the web UI and MCP,
and gives `get_status` the dashboard's external-check load authority so an
unavailable alias reads as a configuration error (MC-01, UI-14/17/18, MD-12,
CA-07, issue #64). v0.41 improves MCP authoring discoverability
(definition traps and CI-validated recipes, attribute-only `filter_expr`
guidance, ftmon-json exit-0 steering) and clarifies that Nagios exit evidence
(EC-06) must not be confused with the ftmon-json exit-0 contract (EC-10,
issue #62). v0.40 bounds MCP `query_metrics` total response size
and distinguishes empty-series reasons (DM-06/MC-01, issue #61). v0.39 extends
the process sampler with per-PID soft
`RLIMIT_NOFILE` (`fd_limit_soft`) so fd-utilization monitors can key off each
process's real limit rather than a single host-wide parameter (SA-04/PL-05,
issue #60). v0.38 hardens Windows managed paths, secret-file
handle validation, and the minimal external-check environment. v0.37 makes generic init profiles select the
current host's calibrated monitor tree and closes Windows multi-channel
checkpoint gaps at first subscription. v0.36 integrates Windows Event Log channel
selection and per-channel subscribe-time filtering configurability (MD-13,
DM-19, SA-10) from feature/windows-support into this lineage: a
`source = "events"` monitor's `[source_options]` can now declare `channels`
(path + optional XPath query, unioned across every loaded event monitor)
instead of the hardcoded System/Application default, and a bad channel name
or malformed query is isolated to that one channel instead of aborting the
whole subscription pass. Also closes a real doc/code gap: DM-09's
`store_min_severity` override was documented but had no schema branch to
actually accept it. v0.35 adds cursor-safe adjacent event coalescing
and raw event-rate telemetry/presentation across platform adapters. v0.34 replaces ambient macOS unified-log ingestion
with an enabled, source-filtered operational allowlist after a live event storm
proved that downstream rules and storage filtering cannot protect the reader queue.
v0.33 ships the validated macOS event, notification,
LaunchAgent, and builtin-profile seams. v0.32 replaces the `windowsdesktop` placeholder
profile with `windesktop`/`winserver` (PM-08), sharing one Windows-adapted
monitor tree that drops rules dead on Windows by construction (an
inode-based ladder — NTFS has no POSIX inode concept — and a
journald-provider-gated OOM rule) rather than leaving them silently
inert; `load`'s PSI-gated rules are deliberately left unchanged, since
§7.7.5 already specifies "absent, not replaced by an inferred metric" for
exactly this case. v0.31 adds the `windowsdesktop` init profile (PM-08)
and gives EC-01/SE-07's ownership/writability trust check a real Windows ACL
equivalent (owner SID + DACL walk in place of POSIX uid/mode bits) so the
check registry, external-check runner, secret-credential files (SE-04), and
the demo database (SE-06) all enforce their existing trust contract on
Windows instead of only on POSIX. v0.30 records real-hardware macOS
platform-spike contracts for unified-log replay, Script Editor
notifications, and LaunchAgent reload behavior. v0.29 keeps churny
historical process identities out
of the Trends entity selector while preserving direct incident and bookmark
access to their retained history. v0.28 hardens the web response boundary, refreshes
the remaining operational pages, and makes Metrics baselines unmistakably
visible (issues #21, #22, #48). v0.27 packages the three MCP authoring guides
so installed hosts can serve definitions and external-check guidance (issue
#37). v0.26 adds explicit current-value glance metadata and dashboard readouts
(issue #16), after v0.25 adds the read-only Baselines
index (issue #18), v0.24 adds the bounded `list_baselines` MCP tool (issue #46), and v0.23
makes CA-05 learning visible on Metrics at its honest retained five-minute
resolution (issue #17). Baseline rows persist the immutable effective half-life
so reverse reconstruction cannot mix EWMA coefficients; a coefficient change
reseeds that series. v0.22 extends MC-06 so `diagnose_monitor` surfaces
the last persisted external-check result (`plugin_state`, `plugin_ok`,
`duration_s`, `plugin_message`, `sample_age_s`) for the configured entity
(issue #36), closing the agent loop between "loaded" and "producing". v0.21
resolves OPEN-8: FTMON 2.0 container monitoring ships as an external-check
recipe, not a core source. Rootful container-socket or container-engine-group
authority is outside SE-01; the supported recipe precondition is a rootless
socket already owned by the same user that runs FTMON. v0.20 bounds the
desktop notifier's tray footprint (issue #40): NO-02 sends renotify/recover
transient, reuses one replaceable notification slot per incident, and reserves
`critical` urgency for severity 4, degrading per-flag on older `notify-send`.
v0.19 hardens leak-detection evidence (issue #20): `coverage()` joins the
CA-01 function table, §7.7.1 leak rules must require window coverage and
recent net growth alongside slope, SA-09 separates process display identity
from stable identity, and IN-09 makes entity-gone clearing survive daemon
restarts. v0.18 adds authoring discoverability: `ftmon paths`,
`ftmon monitor rescan`, `ftmon check trust` (CL-06..08) and the read-only MCP
tools `monitor_paths`/`diagnose_monitor` (MC-06). v0.17 makes SIGHUP a reload
request instead of the fatal default disposition (PM-11). v0.16 requires the
daemon to survive a tick write lock timeout instead of exiting (PM-10). v0.15
made document-version coherence a tested invariant (TS-19) and opened OPEN-8
(container monitoring: core source vs recipe), now resolved by v0.21. The
v0.12 release-readiness gates (TS-17 soak, TS-18 zero-pending traceability,
DO-09 drift audit; milestone M10) remain in force with the pending list burned
down to empty. §19 has no open items.
Audience: implementers (including LLM-based implementers) and the reviewer (project owner).
Every requirement has a stable ID (`XX-nn`). Tests MUST reference requirement IDs. Renumbering is not allowed after v1.0 of this document; retired requirements are marked `[RETIRED]`, new ones appended.

---

## 1. Purpose

FTMON v2 is a lightweight, local, single-host systems monitor for desktops,
workstations, and individually managed servers. It:

- detects memory leaks, CPU hogs, disks filling, service failures, and
  log/event-log entries of interest, then delivers notifications through
  locally selected channels;
- records metric history so questions about past behavior can be answered;
- lets users (and, with approval, AI) define new monitors declaratively, including formula-based derived metrics;
- lets administrators register local external checks so existing scripts and
  separately installed Nagios-compatible plugins can feed the same history,
  incident, notification, and Trend machinery;
- exposes everything to AI assistants through a local MCP server;
- is fully usable **without** AI through a CLI and a local web UI.

It is the successor to the legacy Perl FTMON (2001–2003), published separately at [SourceForge](https://sourceforge.net/projects/ftmon/). It ports that system's design ideas — delta/monotonic calculations, consecutive-cycle confirmation, baselining, threshold tables, escalation — not its code. The original source is deliberately not vendored so the v2 repository has an unambiguous MIT-licensed boundary.

### 1.1 Non-goals (v1)

- **NG-01** Multi-host / fleet monitoring, remote agents, or any network listener other than the localhost web UI.
- **NG-02** **[RETIRED v0.9]** Desktop-only notifications. Replaced by the
  bounded channel set in NO-05; direct SMS and a general notification-platform
  plugin ecosystem remain out of scope.
- **NG-03** Being a Nagios/Zabbix/Prometheus replacement. FTMON monitors one
  host per installation; fleet inventory, cross-host queries, service
  discovery, and a central collector are out of scope.
- **NG-04** Windows and macOS *implementations* (interfaces and schema must support them; see §4). Linux ships first.
- **NG-05** Authentication/multi-user support in the operational web UI. It
  remains loopback-only and single-user; the public demo exception is
  synthetic and read-only (UI-15), not a remotely manageable FTMON instance.
- **NG-06** Per-process network connection attribution (deferred; needs elevated rights on some systems).
- **NG-07** Baseline seasonality (day-of-week / time-of-day patterns) — deliberately absent in v1; the baseline is a single smoothed level (CA-05).
- **NG-08** Secret-pattern redaction of command lines / log messages (privacy posture is SE-04: local single-user data, restrictive file modes, truncation, collection toggle).
- **NG-09** Loading third-party Python modules into the daemon, discovering
  plugins automatically, vendoring Nagios plugins, NRPE, or becoming a general
  remote/fleet check orchestrator. M9 executes only explicit local commands
  registered by the administrator (EC-01).

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **tick** | One scheduler wake-up. Base tick 5 s (monotonic clock). |
| **cycle** | One run of a given monitor (its `interval` has elapsed and it executes its pipeline). |
| **source** | A data producer: a `Sampler` (metrics) or an `EventSource` (events). Sources run at most once per tick and their snapshot is shared by all consuming monitors (SA-06). |
| **snapshot** | The immutable output of one source run: a set of entities with metric values, all carrying the same timestamp. |
| **monitor** | A named unit of observation defined in a TOML file: a source, parameters, derived-value formulas, and rules. |
| **entity** | One row a monitor observes: a process, a mount point, a socket, a unit. Identified by a stable `entity_id` string. Watchlist entities (service/net targets) are *synthetic*: they always exist with a `present` metric. |
| **metric** | A named numeric time series per entity (e.g. `rss_bytes` for entity `firefox`). |
| **event** | A discrete occurrence from an `EventSource` (journald / Windows Event Log / macOS unified log), normalized to the canonical record (§5.3). |
| **rule** | A condition (expression) attached to a monitor; when confirmed, it opens or escalates an incident. |
| **group** | A set of rules in one monitor sharing incident identity (a severity ladder). Default group of a rule = its own id. |
| **incident** | The stateful lifecycle of a rule group firing for an entity: open → (escalate/downgrade/renotify) → cleared/acked. |
| **episode** | The event-rule flavor of an incident: keyed by matching events, cleared by a quiet period rather than false evaluations (IN-08). |
| **baseline** | A learned "normal" value for a metric/entity (§7.4) usable in rule expressions. |
| **draft** | A monitor definition written by MCP `define_monitor`, stored inactive until approved. |
| **external check** | An administrator-registered local executable invocation sampled by FTMON under a hard deadline. It may implement FTMON's JSON protocol or the Nagios plugin convention. |
| **performance data** | Numeric label/value output from an external check. Only definition-declared mappings become FTMON metrics; undeclared labels are ignored. |

---

## 3. Product decisions already made (context for implementers)

These were decided during specification and are not open for re-litigation by implementers:

- Language: **Python ≥ 3.11**, managed with **uv** (`pyproject.toml`, lockfile). Lint/format: **ruff**. Tests: **pytest**.
- Repo: monorepo at `PROJECTS/ftmon`; new code in the `ftmon` package. The original Perl source remains in its separate SourceForge project and is not vendored here.
- License: this repository is **MIT**. The separate original SourceForge project is GPLv2.
- Storage: **SQLite** (WAL mode, `auto_vacuum=INCREMENTAL`). No external database, no RRDtool.
- Process model: daemon + CLI + MCP server + web UI are **separate processes** sharing the SQLite database and (for definitions) the config directory under the coordination rules of PM-06/PM-07. Web UI is a fully separate service from the daemon.
- Monitor definitions: **TOML** with expression strings in a **restricted
  Python-AST subset** (§8). Definitions are data, never executable code; M9
  definitions may reference but never create an administrator check alias.
- AI authority: **draft + approval** (§8.3, §11). Actions are **pre-existing allowlisted scripts only** (§10).
- Incident model: **ladder groups** (IN-03): one incident per (monitor, entity, group); severity ladders share a group.
- Incident behavior: consecutive-cycle confirmation, **escalate + backoff** renotification, recovery notification (§9).
- Notification delivery: **at-least-once via durable outbox** (NO-04); exactly-once is explicitly not promised.
- Privacy: command lines collected, truncated to 256 chars, `collect_cmdline` toggle, 0600 data files (SE-04).
- Resource budget: "standard" (§13), with a capacity worksheet required in the design doc (DM-16).
- Quiet hours: global only in v1 (NO-03). Web UI freshness: 5 s polling, no SSE in v1 (UI-04).
- Testing: **two tiers** — deterministic fixture-driven e2e in CI plus opt-in real-system smoke tests (§16).

---

## 4. Platforms and process model

### 4.1 Platform matrix

| Capability | Linux (v1) | Windows (v1.x) | macOS (v1.x) |
|---|---|---|---|
| Process/CPU/mem/disk sampling | psutil | psutil | psutil |
| Event source | journald (`journalctl -o json` subprocess) | `win32evtlog.EvtSubscribe` (pywin32) | `log stream --style ndjson` subprocess |
| Event cursor (DM-15) | journald cursor string | `EvtBookmark` XML | wall-time high-water mark + bounded recent-event identities |
| Notification | `notify-send` (fallback: D-Bus) | toast (`windows-toasts`) | `osascript display notification` (Script Editor identity) |
| External checks | local executable; Nagios + FTMON JSON | FTMON JSON planned | FTMON JSON planned |
| Service wrapper | systemd user unit | Task Scheduler (logon) | launchd LaunchAgent |
| Config/data paths | XDG dirs | `%APPDATA%` / `%LOCALAPPDATA%` | `~/Library/Application Support` |

- **PL-01** All platform-specific behavior MUST live behind exactly four seams: `Sampler` implementations, `EventSource` implementations, the notification adapter, and the service wrapper/paths module (use `platformdirs`). No platform conditionals anywhere else.
- **PL-02** The canonical schemas (§5) MUST NOT assume any platform's shape. In particular `event_id` is an **optional string** (Windows has numeric IDs, journald has identifiers, and macOS has no native ID but MAY assign a stable FTMON event class during normalization).
- **PL-03** Permission failures during sampling (e.g. psutil `AccessDenied`) MUST degrade gracefully: skip the entity, count it in self-metrics (§13), never crash or spam the log (log once per entity per daemon lifetime at DEBUG).
- **PL-04** v1 ships and is tested on Linux only, but the fake/fixture implementations of `Sampler` and `EventSource` (§16) count as second implementations, keeping the seams honest.
- **PL-05** Every `Sampler` and `EventSource` declares its schema: entity kind,
  metric names/units, attr names/types. Built-in declarations are static. The
  `external` source composes its fixed fields with the definition's validated
  EC-04 performance-data mappings; no runtime output can add a name to the
  expression namespace. Validation (MD-01) resolves expressions against the
  resulting declaration, which is also the documentation source for DO-01.

On Intel macOS 12, the locked dependency set lacks a
`cryptography==49.0.0` x86_64 wheel and requires a native OpenSSL/Rust build;
support policy and packaging must be resolved before macOS is advertised.

### 4.2 Processes

| Process | Started by | Role | Shared-state access |
|---|---|---|---|
| `ftmon daemon` | service wrapper | sampling, calculations, rules, incidents, notifications, retention | DB read-write (sole bulk writer); reads definitions |
| `ftmon <cmd>` (CLI) | user | status, queries, config check, approve/enable, ack, doctor | DB read + small writes; definition file writes (PM-06) |
| `ftmon mcp` | AI client (stdio) | MCP tools (§11) | DB read + small writes; draft file writes (PM-06) |
| `ftmon web` | user or service wrapper | local web UI (§12) | DB read + small writes; definition file writes (PM-06) |

- **PM-01** Each process MUST function (with stale data where applicable) when the others are down. The web UI and MCP server MUST clearly surface daemon liveness (last-cycle age).
- **PM-02** The daemon MUST enforce single-instance per user via a lock (advisory file lock in the runtime dir). Second start exits non-zero with a clear message.
- **PM-03** All small writes from non-daemon processes MUST be short transactions with `busy_timeout ≥ 5000 ms`. The daemon MUST never hold a write transaction across a sampling call.
- **PM-04** The daemon MUST re-scan monitor definition files for changes every 30 s (mtime + content-hash) and apply add/change/remove without restart. An invalid changed file MUST NOT take down the daemon: keep the currently loaded version, record a `config_error` self-event, surface it in CLI/web/MCP status. **After a daemon restart**, an invalid file on disk means that monitor is simply not loaded (config_error) — the persisted copy (PM-07) is for diagnostics and history, never silent resurrection.
- **PM-05** MCP transport is **stdio only** in v1. The web UI binds **127.0.0.1** only, default port 8420, configurable. No other sockets are opened.
- **PM-06** Definition-file coordination rules, binding on every process that writes to the config tree: (a) all writes are atomic — write to a temp file in the same directory, fsync, `rename()`; (b) directories 0700, files 0600 at creation; (c) symlinked definition files are rejected at load with a config_error; (d) approval (`drafts/x.toml` → `monitors/x.toml`) re-validates then renames atomically, and fails if the target exists; (e) concurrent writers are resolved last-write-wins — acceptable for a single-user tool — but every load path re-validates, so a torn outcome is at worst a config_error, never a partial load.
  On Windows, every managed-directory permission mutation MUST open the final
  component with reparse traversal disabled, reject any reparse point, and
  apply its protected DACL through that verified handle; initialization and
  `atomic_write` MUST fail before mutating a junction target.
- **PM-07** On each successful load, the daemon persists the monitor's normalized definition, content hash, and load timestamp in the DB. This is the substrate for change detection (PM-04), `get_monitor` history, and MD-06 — not a fallback config store (see PM-04).
- **PM-08** `ftmon init --profile desktop|server|windesktop|winserver|macdesktop|macserver`
  writes
  explicit initial settings; the profile is scaffolding, not a permanent
  hidden behavior switch. The generic `desktop` and `server` names MUST
  resolve to the current host's calibrated platform variant on Windows and
  macOS; omitting `--profile` selects the host's desktop variant. On Linux,
  `desktop` enables the file and desktop channels,
  installing GNOME-calibrated monitor definitions (real host-tuning data,
  `docs/tuning-desktop-xps15.md`). `server` enables the file channel only,
  disables desktop delivery, and documents remote-channel setup, installing
  the normative uncalibrated definitions. `windesktop` and `winserver` share
  one Windows monitor tree — not host-tuning data, but OS-semantic fixes: an
  inode-based rule ladder and a journald-provider-gated event rule are
  dropped because they are dead on Windows by construction (NTFS has no
  POSIX inodes; no Windows Event Log provider is ever named `"kernel"`),
  not because of any threshold tuning. `windesktop` enables the file and
  desktop (toast) channels like `desktop`; `winserver` enables the file
  channel only, like `server`. Existing configuration is never rewritten;
  `--force` continues to reinstall built-in monitor definitions only
  (FS-02), not user settings. `macdesktop` and `macserver` share a Darwin
  tree: PSI and foreign event rules are removed; unified-log ingestion is
  enabled only behind a source-side allowlist for third-party faults and
  explicit kernel storage-integrity messages; read-only/nobrowse mounts
  and inode rules are excluded; connection
  alerts require an explicit listener watchlist; service examples are
  process-based; and only the desktop variant enables best-effort Script
  Editor notifications.
- **PM-09** The supported server deployment runs the daemon as a dedicated
  unprivileged account or the administrator's ordinary account. It MUST NOT run
  as root. The normal web process remains on loopback; remote operational access
  is through an SSH tunnel unless a future authenticated mode is specified.
- **PM-10** When a tick's write transaction fails because SQLite reports the
  database is locked after `busy_timeout` is exceeded, the daemon MUST NOT exit.
  The failed tick's buffered writes MUST be dropped, a `sqlite_lock_errors`
  self-metric MUST increment, a `self` event MUST record the failure, and the
  next tick MUST continue. Short contention within `busy_timeout` remains
  tolerated by the connection pragma (PM-03); this requirement covers only the
  timeout-exceeded crash path. Operators MUST NOT open a writable SQL client
  against the live database while the daemon runs — use read-only mode or stop
  the daemon first.
- **PM-11** The daemon MUST treat `SIGHUP` as a reload request rather than the
  fatal default disposition. The signal handler MUST only record the request —
  no filesystem or database access — and the next tick MUST perform the same
  refresh as the periodic rescan (PM-04): notification channels, the
  external-check registry, monitor definitions, and acknowledgements. A reload
  request MUST NOT interrupt an in-progress tick. The packaged daemon systemd
  units MUST expose this via `ExecReload=` so `systemctl reload` works. A
  macOS LaunchAgent MUST preserve the same signal contract: sending SIGHUP to
  the launchd-managed PID reloads in place. `launchctl kickstart -k` is a
  restart (new PID), not a reload substitute.
- **PM-12** When notification dispatch runs on a background worker, that worker
  owns its own database connection, the startup `sending` → `pending` reset, and
  the drain loop; the daemon's main connection MUST NOT perform that reset,
  because a lock there aborts startup before any recovery path exists. The
  worker MUST survive a transient store fault — the database reported locked or
  busy after `busy_timeout`, or a connection invalidated underneath it — by
  closing and recreating its connection, repeating the `sending` → `pending`
  reset (the NO-04 duplicate window, bounded at one redelivery), and continuing
  to drain. Recovery MUST count a `notify_store_errors` self-metric, record a
  `self` event carrying a fixed redacted category rather than exception text,
  and MUST NOT itself raise a notification. A fault that is not transient —
  corruption, a failed migration, an unwritable or failing device, or an
  unexpected exception — MUST end the thread deliberately after publishing a
  durable dead state readable by `ftmon doctor` and reporting the same fixed
  category to the daemon's log. The daemon MUST record which dispatch mode it
  is running so a diagnostic reading only the database can distinguish "no
  worker expected" from "worker died". Sampling MUST continue in every case.

### 4.3 Filesystem layout (Linux)

```
~/.config/ftmon/config.toml            # global settings
~/.config/ftmon/checks.toml            # desktop/user external-check authority
~/.config/ftmon/monitors/*.toml        # enabled monitor definitions
~/.config/ftmon/monitors/drafts/*.toml # AI-authored, awaiting approval
~/.config/ftmon/actions/*              # allowlisted action scripts (user-created)
~/.local/share/ftmon/ftmon.db          # SQLite database (0600)
~/.local/state/ftmon/daemon.log        # daemon's own log (rotating, cap 10 MB × 3, 0600)
~/.local/state/ftmon/notifications.jsonl # notification audit log (0600)
$XDG_RUNTIME_DIR/ftmon/daemon.lock     # instance lock
```

- **FS-01** Paths MUST be resolved through one module using `platformdirs`; nothing else constructs these paths.
- **FS-02** First run MUST create all directories (0700), write a commented default `config.toml`, and install the built-in monitor definitions (§7.6) as real, user-editable TOML files (not hidden defaults). `ftmon init --force` re-installs built-ins without touching user files.
- **FS-03** `Paths.check_registry_file` resolves from
  `FTMON_CHECK_REGISTRY` when set, otherwise
  `config_dir/checks.toml`. `init` may create a commented empty user registry
  but never creates `/etc/ftmon/checks.toml`. MCP, web, monitor approval and
  draft tooling MUST treat the registry as read-only. Hardened server
  documentation creates `/etc/ftmon` and `checks.toml` root-owned, mode
  0755/0640 respectively, readable by group `ftmon`, and the service unit
  exposes the path without granting it write access.

---

## 5. Data model

The SQLite schema itself is a design-document concern; this section fixes the *logical* records that schema must represent, and their semantics.

### 5.1 Metric sample

`(ts, monitor, entity_id, metric, value: float)`

- **DM-01** `ts` is Unix epoch seconds (UTC) as recorded by the daemon clock (§16.2). All timestamps in the system are UTC; presentation layers localize. Samples whose value is NaN or ±inf are rejected at ingest and counted in a self-metric.
- **DM-02** `entity_id` MUST be stable across the entity's lifetime and unambiguous within a monitor:
  - process: `"{name}:{pid}:{create_time}"` (create_time defeats PID reuse); a separate entity attribute carries the display name so queries like "firefox history" work across restarts.
  - mount: the mount point path; unit/service: unit name; socket: `"{proto}:{laddr}:{lport}"`.
- **DM-03** Entities have an attributes record `(monitor, entity_id, first_seen, last_seen, gone_ts|null, attrs: JSON)` — e.g. process cmdline, username, exe path. Attrs are for display/filtering, never for arithmetic. The attrs JSON is capped at 4 KB (truncated with a `truncated: true` marker).
- **DM-13** Incident `history` (DM-12) is capped at 500 entries; on overflow the oldest 100 are replaced by one summary entry (count, time range, severity range). Event messages are truncated to 2 KB at ingest.

### 5.2 Retention and rollups

- **DM-04** Raw samples are kept **48 h**. 5-minute rollups `(avg, min, max, last, count)` are kept **30 d** for *durable* series and **7 d** for process-sourced series (v0.47 amendment, issue #102: the same churn argument the hourly tier settled in v0.3, applied to a tier twelve times denser and, in practice, the largest table in the database; no rule reads that far back either way, since CA-04 caps expression windows at 6 h and CA-05 baselines step incrementally as rollups are produced rather than re-reading history). 1-hour rollups are kept **400 d** for *durable* series (system, disk, self, and watchlist-synthetic entities) and **90 d** for process-sourced series (v0.3 amendment: the capacity worksheet shows process-entity churn makes 400 d hourly retention for all series infeasible within DM-05). Rollup jobs run in the daemon, incrementally, never more than 1 s of work per cycle.
- **DM-05** The database's **used-page footprint** MUST stay under **200 MB**, measured as `(page_count − freelist_count) × page_size`. On breach the daemon degrades in this fixed order until under budget: (1) oldest raw samples beyond 24 h, (2) oldest events beyond 7 d, (3) oldest 5-min rollups, (4) oldest 1-h rollups. Incidents are never pruned. Each degradation step increments a `db_degradations` counter, and whether the most recent pass degraded MUST be published as a `db_degrading` gauge so a definition can window it (v0.47 amendment, issue #102): *durably* degrading — retention prunes on most passes indefinitely, silently shortening the DM-04 windows — is a different condition from one lossy prune, and only the first requires an operator. Degradation self-events MUST be rate-limited and MUST state how many passes each report covers; emitting one per step per pass buries the transition in a stream nobody reads. The DB is created with `auto_vacuum=INCREMENTAL`; bounded `PRAGMA incremental_vacuum(200)` runs after each retention transaction so reusable freelist pages progressively return to the filesystem. The main database file MAY temporarily exceed the used-page footprint while that bounded reclaim catches up; free pages remain immediately reusable and do not trigger further lossy degradation. FTMON MUST NOT run a full `VACUUM` automatically while the daemon is live: rebuilding this bounded local database requires an exclusive SQLite write lock whose sampling, retention, and notification availability cost is disproportionate to tighter physical packing. Offline full compaction is explicit operator maintenance, not part of the live retention path. (v0.44 amendment, issue #74.) (v0.46 amendment, issue #104.) Every surface that reports database capacity — the `self` source, `ftmon doctor`, `ftmon status`, MCP, and the dashboard — MUST distinguish used pages from file allocation, and MUST present used pages as the budget figure; file allocation is fragmentation context and MUST NOT on its own indicate a breach. A definition alarming on this budget MUST compare used pages, and its alarm threshold MUST sit **above** the 200 MB target rather than at it: retention holds the footprint just under the target by design, so a rule tripping at the target fires precisely when retention is working, and an alarm must instead mean that retention is failing.
- **DM-06** Queries spanning tiers (raw → 5 m → 1 h) MUST be answered transparently by the query layer choosing resolution by range; callers never pick tables. (v0.47 amendment, issue #102.) The choice MUST be made on the **age of the oldest requested point** rather than the span of the range — a narrow window far in the past has a short span but data only the coarser tier still holds — and MUST respect DM-04's durable/process split: a tier is eligible only if the requested range lies inside the retention that tier keeps *for the series being asked about*. Where a request covers series of mixed durability, or matches none, the shorter window applies: one resolution serves the whole answer, so choosing the longer would truncate part of it silently. The resolution reported to a caller MUST be the one used for discovery, preflight and retrieval alike, including for an empty result. MCP `query_metrics` MAY apply a documented post-tier entity/point truncation with explicit metadata; it MUST NOT select a coarser retention table merely because many entities matched. The selected resolution MUST be reported even when no observations exist in the range. MCP MUST omit entities with no in-range observations (quiet windows are empty `series` with `empty_reason`, not empty-point shells).
- **DM-16** The design document MUST include a capacity worksheet deriving RB-01/DM-05 feasibility from stated assumptions — max tracked entities (budget: 400 persisted), metrics per entity (≤ 10), sample width in bytes, rows/day at 60 s intervals, event rates, ring-buffer memory (CA-04) — and the worksheet's assumptions become validation limits (a definition exceeding them is rejected). (v0.46 amendment, issue #104.) Because the budget counts **persisted** entities, pressure against it MUST be measured as those for which durable history is currently being written — not as entities merely present. A process the pipeline samples but does not select is running, not persisted: under SA-05 track-all every sampled entity is marked seen, so counting presence overstates storage pressure by the ratio of sampled to selected entities, which on a desktop is roughly an order of magnitude. A count derived from presence MAY still be reported, under a name that says so.
- **DM-17** Historical chart queries MUST expose the selected rollup statistic (`avg|min|max|last`) and, when requested, the stored minimum/maximum envelope. Rates and projections MUST be computed from observations before display downsampling; presentation code MUST NOT derive them from the ≤2 000 rendered points. Missing intervals remain gaps rather than being interpolated.

### 5.3 Canonical event record

`(ts, source, provider, event_id: str|null, severity, message, attrs: JSON)`

- **DM-07** `source` ∈ {`journald`, `eventlog`, `oslog`, `file`, `self`}. `provider` is the platform's producer field (journald `SYSLOG_IDENTIFIER`/`_SYSTEMD_UNIT`, Event Log Provider, os_log subsystem). `self` is FTMON's own operational events (config errors, budget breaches, prune runs, clock gaps, event overflows).
- **DM-08** `severity` is normalized to the 5-level scale: `info(0) notice(1) warning(2) error(3) critical(4)`. Each `EventSource` documents and tests its mapping (journald PRIORITY 0–7 → this scale; Event Log Level; os_log messageType).
- **DM-09** Stored events are kept 30 d (subject to DM-05 degradation). A **store-filter** (v0.3 amendment, capacity-driven) decides what is stored: events with severity ≥ `notice` (configurable `store_min_severity`) plus any event matching a loaded event rule; info-level non-matching events are counted in a self-metric but not stored — a desktop journal's full volume (50–200 k lines/day) cannot fit the DM-05 budget. Event *rules* (§7.7.3) evaluate against the live stream before the store-filter (a rule can match info-level events; matching forces storage) and match on canonical fields only — a rule written against journald fields MUST be expressible identically against Event Log fields. This storage policy is not source admission control: platform adapters MAY apply a stricter upstream predicate to protect SA-08's queue and process budget.
- **DM-10** Event ingestion MUST be rate-defended: per (source, provider), more than 100 stored events/min collapses into a single `event_storm` self-event with a count, until the rate drops. (A log-spamming app must not fill the DB.)
- **DM-15** Each `EventSource` persists a source-specific **checkpoint** in the
  DB after every drained batch. Journald stores its cursor string and Windows
  stores a composite of per-channel `EvtBookmark` XML. Before subscribing to
  any Windows channel absent from that composite, the source MUST persist a
  restart-safe initial boundary at the filtered channel tail; an empty channel
  MUST retain an explicit oldest-record boundary until its first event drains.
  macOS unified log has no persistent bookmark: its
  checkpoint is a wall-time high-water mark plus a bounded set of recent event
  identities. First run ever starts at "now" (no historical backfill). On
  daemon restart the reader resumes from the checkpoint and replays events
  that occurred while the daemon was down. Bookmark sources resume after the
  exact checkpoint; macOS MUST replay from before its watermark and
  deduplicate the overlap, including the `log show` → `log stream` handoff.
  The checkpoint advances only after the corresponding events are durably
  accepted. An expired macOS replay boundary MUST record an observable
  retention-gap self-event rather than silently claiming exact resume. Events
  carry both source timestamp (stored as `ts`) and ingest timestamp; ordering
  for rules is ingest order, so late-arriving source timestamps cannot
  re-trigger past windows.
- **DM-19** `channels` (MD-13) selects which platform-specific event channels
  an `EventSource` subscribes to and, where the platform's query language
  supports filtering at the subscription itself (e.g. Windows Event Log's
  XPath-subset query engine — shared by `EvtQuery`/`EvtSubscribe`/`wevtutil`/
  `Get-WinEvent`, not WEC/WEF-specific), narrows which events on that channel
  are delivered at all, before DM-09's store-filter ever runs. There is one
  shared subscription for the whole daemon, not one per monitor: channels are
  unioned across every loaded event monitor. The same channel path requested
  with conflicting non-empty queries keeps the first-seen query and reports
  the conflict as a self-event (SA-10) rather than silently choosing one.
  Channel/query configuration is read once, at the event reader's first
  start; changing an already-running reader's channels requires a daemon
  restart to take effect — PM-04's hot-reload guarantee covers rule changes,
  not this.
- **DM-20** Before bounded-queue admission, every platform event adapter MUST
  coalesce a contiguous run of canonically identical events. Identity is the
  exact `(source, provider, event_id, severity, message)` tuple; origin is
  mandatory so one producer cannot conceal another. The aggregate retains the
  first event record, advances its source checkpoint to the last represented
  event, and records string attrs `repeat_count`, `repeat_first_ts`, and
  `repeat_last_ts`. Coalescing MUST NOT cross an intervening event because an
  opaque journal cursor or bookmark could then advance past undrained evidence.
  Event-rule confirmation and episode occurrence totals count represented raw
  occurrences, not aggregate rows. The `self` source exposes cumulative raw
  `events_received`, cumulative `events_repeated`, and a rolling
  `event_rate_per_min` gauge that includes coalesced repeats.

### 5.4 Incident

`(id, monitor, group, entity_id, state, severity, owning_rule_id, opened_ts, last_change_ts, cleared_ts|null, clear_reason|null, ack: {by, ts}|null, notify_count, occurrences, history: [...])`

- **DM-11** `state` ∈ {`open`, `acked`, `cleared`}. Identity is **(monitor, entity, group)** per IN-03. `clear_reason` ∈ {`recovered`, `entity_gone`, `superseded`, `quiet_period`}. Incidents are never deleted by retention; they are the system's long-term memory.
- **DM-12** `history` records every state/severity transition, every notification sent, and every action run, with timestamps — sufficient for `explain_incident` (§11) to reconstruct the full story (subject to the DM-13 cap).
- **DM-14** Notifications flow through a durable **outbox**: the immutable
  rendered notification is written in the same transaction as the incident
  transition that caused it (see NO-04).
- **DM-18** Fan-out is represented by one durable delivery row per
  `(notification, configured_channel)`, carrying `state`, `attempt_count`,
  `next_attempt_ts`, `delivered_ts`, and a bounded redacted `last_error`.
  Notification creation and the complete initial delivery set are atomic.
  Success or permanent failure in one channel cannot mark another channel
  delivered, and configuration changes do not retroactively add channels to an
  already-created notification.

---

## 6. Sampling and scheduling

### 6.1 Pipeline

- **SA-06** The data flow per tick is fixed:

```
sources due? → each needed source runs ONCE → immutable snapshot (single ts)
            → each due monitor projects its entities/metrics from the snapshot
            → derived metrics (topological order, MD-08)
            → exemptions (CA-07) → rules → incident engine (§9)
```

  A source shared by several monitors (e.g. the process source feeding `leak`, `hog`, `service`) is enumerated once per tick; all consumers see identical values and timestamps.
  For `external`, the sharing key is the registered check alias rather than the
  broad source name, so different commands never collapse into one snapshot
  while several definitions can reuse one execution (EC-08).

### 6.2 Scheduling

- **SA-01** The scheduler ticks every 5 s on the **monotonic** clock. Each monitor declares `interval` (default `"60s"`, min `"15s"`); it runs on the first tick at/after its due time. A monitor whose cycle overruns its interval is skipped (not queued) for the missed slot, with a self-metric counting overruns.
- **SA-02** Samplers run sequentially in the daemon (no thread pool in v1).
  Timeout semantics are honest about Python's limits: **subprocess-backed**
  sources (`journalctl`, `systemctl`, and M9 external checks) get hard deadlines
  enforced by process-group kill; **in-process** samplers (psutil loops) check a
  cooperative deadline between entities (default budget 10 s); a truly stuck
  native call cannot be killed and is instead *detected* — the cycle-overrun
  self-metric and the `self` monitor's watchdog rule (RB-02) surface it.
- **SA-07** Clock discipline: scheduling and elapsed-time math use the monotonic clock; sample/event timestamps use wall UTC. After suspend/resume or any monotonic gap > 2× base tick, missed cycles are **skipped without catch-up** and a `clock_gap` self-event records the gap. Backoff arithmetic (IN-02) uses wall timestamps but treats negative elapsed time (wall clock stepped back) as zero and recomputes from now. Window functions simply see a gap in samples; CA-02's `None` semantics make rules silent rather than wrong across gaps.

### 6.3 Sources

- **SA-03** `EventSource`s run as supervised subprocess readers (e.g. `journalctl -f -o json --after-cursor=…`) feeding an in-daemon queue, drained each tick. A dead reader is restarted with exponential backoff (1 s → 60 s cap) and a self-event on first death.
- **SA-08** The event queue is bounded at 10 000 entries; on overflow the oldest are dropped and an `event_overflow` self-event records the count. Malformed lines are skipped and counted (self-metric), never fatal. Reader stall detection: `event_source_last_activity_age` is a self-metric; the `self` monitor warns when it exceeds 10 m while the reader process is alive. The macOS adapter MUST apply the same fixed operational predicate to replay and streaming before records enter this queue; ambient debug-level unified-log ingestion is forbidden.
- **SA-09** Process display identity (v0.19, issue #20). Interpreter-hosted processes often expose a generic runtime thread name (`MainThread`, `node`, `python3`) as the kernel process name, defeating both operator recognition and name-based exemptions. The process sampler MUST additionally collect, where readable: `exe` (executable path, already collected), `exe_base` (its basename), and `cmd_hint` (executable basename plus the basename of the first path-like argument, ≤ 64 chars total — derived basenames only, never raw arguments; SE-04's posture is unchanged). It MUST publish a `display` attr: `"{exe_base} ({name})"` when `exe_base` is present and differs from `name`, else `name`. `{entity}` in rule/notification templates resolves to `display` when present (falling back to `name`, then `entity_id`). All of these are declared attrs (PL-05) so exemptions (CA-07) and rules can target executable identity. Raw `cmdline` remains governed by SE-04 and MUST NOT appear in notifications; loopback surfaces (web incident detail, MCP) SHOULD expose the sampled attrs. Stable identity (DM-02) is unchanged.
- **SA-10** A per-channel subscribe failure in a multi-channel `EventSource`
  (an unknown channel name, or a malformed filter query, DM-19) MUST be
  isolated to that channel: the reader keeps every other channel alive
  rather than aborting the whole subscription pass. Each distinct failing
  channel is reported once per daemon lifetime as a self-event — not a
  spam-guarded renotify, since nothing about a permanently invalid
  channel/query self-heals the way SA-03's death/restart does.
- **SA-04** Built-in samplers v1: `process` (per-process cpu%, rss, and — where available without elevated rights — open fds, soft `RLIMIT_NOFILE` (`fd_limit_soft`; omitted when denied, unsupported, zero, or infinite), threads, io counters), `disk` (per-mount total/used/free bytes, inodes where supported), `system` (load1/5/15, cpu% total, mem available/used, swap, PSI where present), `net` (per-listen-socket presence, per-proto/state connection counts; **no per-process attribution in v1**, NG-06), `unit` (systemd unit active-state + NRestarts via `systemctl show`).
- **SA-05** The `process` source implements **track-all + promote**: every process is sampled into a bounded in-memory window (last 15 of its samples) each tick it's due; long-term persistence happens only for entities that are (a) on a monitor's watchlist, (b) in the top-N (default 15) by cpu or rss that cycle, or (c) **promoted** by a trend heuristic (§7.6.1). Promotion/demotion transitions are recorded as self-events. This keeps DM-05/DM-16 achievable with hundreds of processes.

### 6.4 Administrator-registered external checks

External checks extend what one FTMON installation can observe without loading
third-party code into the daemon or requiring FTMON to reproduce a large probe
catalog. They are a local execution seam, not a fleet agent protocol. A check
may inspect the host or an explicitly named service/endpoint, but FTMON still
has no discovery, remote agent, central collector, or cross-host view (NG-03/09).

The administrator registers execution authority in a separate `checks.toml`;
monitor definitions can only refer to an existing alias. Desktop/user installs
default to the private FTMON config directory. The hardened server unit fixes
`FTMON_CHECK_REGISTRY=/etc/ftmon/checks.toml`, outside every service-writable
path, where the file and parent directory are root-owned:

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

The monitor definition declares identity and every performance value it is
prepared to accept:

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

[parameters]
latency_growth_sph = { value = 0.2, doc = "Response-time growth per hour" }
growth_confidence_min = { value = 0.8, doc = "Required rising fraction" }

[[derived]]
name = "response_time_rate_sph"
expr = 'slope(response_time_s, "2h") * 3600'

[[derived]]
name = "response_time_growth_confidence"
expr = 'monot(response_time_s, "2h")'

[[rule]]
id = "latency-degrading"
group = "latency-growth"
when = "response_time_rate_sph > latency_growth_sph and response_time_growth_confidence >= growth_confidence_min"
severity = "warning"
confirm_cycles = 3
message = "HTTPS response time is steadily increasing"

[[rule]]
id = "plugin-critical"
group = "availability"
when = "plugin_state == 2"
severity = "critical"
confirm_cycles = 2
message = "{plugin_message}"

[[trend]]
id = "response-time"
kind = "growth"
title = "HTTPS response-time trend"
value_metric = "response_time_s"
value_unit = "seconds"
rate_metric = "response_time_rate_sph"
rate_unit = "seconds/hour"
confidence_metric = "response_time_growth_confidence"
confidence_threshold_param = "growth_confidence_min"
rate_threshold_params = ["latency_growth_sph"]
```

- **EC-01** Check execution authority exists only in the administrator-edited
  `[check.<alias>]` table of the separate registry selected by `Paths`. The
  desktop/user default is `config_dir/checks.toml`; hardened server packaging
  MUST set an absolute `/etc/ftmon/checks.toml` through a root-owned service
  environment and MUST NOT make that path writable by the service. Each entry
  has a unique definition-name alias, an explicit non-empty `argv` array whose
  first element is an absolute executable path, `protocol =
  "ftmon-json"|"nagios"`, and a timeout from 1–30 s (default 10 s). Monitor
  TOML, drafts, MCP, and the web UI can reference an alias but MUST NOT create,
  edit, or supply executable paths or arguments. Missing/invalid aliases are
  configuration errors and never execute. The registry itself must be a
  regular non-symlink file owned by the current user or root, not writable by
  group/other; every parent from the selected trust root must reject
  group/other writes.
- **EC-02** The runner invokes `argv` directly—never through a shell—with no
  stdin, a minimal fixed `PATH`, no inherited environment except the explicit
  Windows runtime allowlist `SystemRoot`, `SystemDrive`, `windir`, `TEMP`,
  `TMP`, and `PATHEXT`, closed file
  descriptors, a private process group, a state-directory working directory,
  and capped stdout/stderr. The daemon MUST run unprivileged. Timeout kills the
  complete process group and returns an unknown check result; subprocess work
  occurs outside every SQLite transaction.
- **EC-03** Nagios mode maps exit codes `0/1/2/3` to OK/warning/critical/unknown.
  Signals, timeout, launch failure, and all other exit codes map to unknown and
  increment a categorized self-metric. The first stdout line, stripped of
  controls and capped at 2 KiB, becomes `plugin_message`; stderr is never a
  metric, notification body, or persisted incident attribute. The first
  line's text after `|` is parsed using the Nagios performance-data shape
  `'label'=value[UOM];warn;crit;min;max`; plugin thresholds remain the plugin's
  concern and do not silently create FTMON rules.
- **EC-04** Each `[[source_options.perfdata]]` mapping declares the source
  `label`, destination `metric`, expected `plugin_uom`, display/storage `unit`,
  `kind = "gauge"|"counter"`, and an optional finite numeric `scale` (default
  1). Metric names are unique and enter the validator's NameEnv before derived
  expressions, rules, and Trends compile. Only mapped finite values with the
  expected UOM are persisted. Missing labels produce an absent metric;
  undeclared labels are ignored; duplicate labels, malformed numbers, UOM
  mismatch, NaN, or infinity reject that mapped value and increment a
  self-metric without discarding the valid check state.
- **EC-05** Every run produces one synthetic entity with stable `entity_id =
  source_options.entity`, fixed metrics `plugin_state` (0–3), `plugin_ok`
  (0/1), and `duration_s`, fixed attr `plugin_message`, plus valid mapped
  metrics. These are ordinary persisted series: they are queryable through
  CLI/MCP/Metrics, usable by parameters, derived expressions, baselines and
  confirmation rules, and eligible for explicit `[[trend]]` profiles. FTMON
  MUST NOT infer a Trend, unit, threshold, or semantic meaning from a Nagios
  label.
- **EC-06** For `protocol = "nagios"`, exit codes 1/2/3 are valid monitoring
  evidence under EC-03, not a daemon fault. FTMON rules decide how plugin state
  confirms, escalates, clears, and notifies. This exit-as-severity contract
  does not apply to `protocol = "ftmon-json"` (EC-10). Execution failure also
  yields state 3 so a definition may distinguish “unknown” from OK; a missing
  sample caused by the global source budget stays `None` and cannot falsely
  clear an incident (CA-02). Reloading a changed registry is atomic; invalid
  new registry content leaves the last valid registry active and emits one
  redacted configuration self-event.
- **EC-07** Registry arguments are configuration, not a secret transport.
  Tokens, passwords, URL user-info, and private keys MUST NOT appear in argv,
  monitor definitions, output, database rows, diagnostics, or MCP/web views.
  Plugins needing credentials use an administrator-created, service-readable
  configuration/credential file supported by that plugin; FTMON passes no
  secret environment values in M9. `doctor` reports only alias readiness and
  stable error categories, never argv or plugin output.
- **EC-08** Due aliases execute sequentially under the existing per-tick source
  deadline with round-robin fairness. One alias referenced by multiple due
  monitors runs once and its immutable raw result is projected through each
  definition's mappings. Work left when the source budget expires is skipped,
  counted, and considered first on the next eligible tick; it is not queued for
  catch-up. Registry and definition counts are bounded (64 aliases, 32
  arguments/alias, 32 mappings/definition; each argument ≤ 512 bytes; combined
  argv ≤ 8 KiB) so configuration cannot defeat RB-01.
- **EC-09** FTMON supports the documented execution/output convention, not
  every Nagios plugin, NRPE, or Nagios configuration feature. Plugins and user
  scripts are installed and licensed separately and are never vendored into
  the MIT repository. Compatibility documentation MUST call out plugins that
  require privilege, inherited environment, unsupported multiline perfdata,
  or secret command-line arguments. FTMON-native JSON checks are preferred
  when richer typed output is needed.
- **EC-10** `protocol = "ftmon-json"` accepts one UTF-8 JSON object surrounded
  only by ASCII whitespace, with total stdout capped at 64 KiB:
  `{"schema":1,"state":0,"message":"...","metrics":{"label":
  {"value":1.5,"uom":"s"}}}`. Known top-level keys are `schema`, `state`,
  `message`, and `metrics`; unknown keys or schema versions fail the run as
  state 3. `state` is integer 0–3, message is a string capped like EC-03, and
  metrics is a map of at most 64 labels to `{value: finite number, uom:
  string}`. EC-04 mappings remain authoritative for names, units, kinds, and
  scaling; JSON output cannot declare or override FTMON schema. Extra
  non-whitespace stdout, malformed UTF-8/JSON, nesting, arrays,
  booleans-as-numbers, or oversized output make the run unknown rather than
  partially trusted. The process exit code MUST be `0`; severity lives only in
  JSON `state`. A nonzero exit MUST yield unknown with failure `exit_status`
  and MUST discard the JSON object (including any `state` and metrics), even
  when stdout would otherwise parse successfully.

### 6.5 Curated extra-monitor recipes

The repository's `extra-monitors/` directory is a compatibility cookbook, not
an executable marketplace. It documents known integrations while keeping
third-party acquisition, trust and licensing decisions with the operator.

- **XR-01** Every recipe has a Markdown article plus bounded `recipe.toml`
  metadata: stable directory-matching id, title, summary, protocol, platforms,
  authoritative HTTPS upstream, licence, evidence status, privilege class,
  network flag and last verified version. Evidence status distinguishes
  fixture-tested, real-system-verified and recipe-only claims.
- **XR-02** Every recipe supplies `checks.toml.example` and `monitor.toml`.
  Executable argv appears only in the registry example; the definition refers
  to its alias and validates through MD-11. Privileged examples use `sudo -n`
  plus one exact root-owned wrapper and document why elevation is necessary.
- **XR-03** Every recipe supplies deterministic protocol-output fixtures with
  expected state and labels. The generic CI harness parses those fixtures and
  validates metadata, documentation headings, registry/definition agreement
  and monitor schema without network, root or the third-party package.
- **XR-04** Third-party plugins are linked and installed separately, never
  copied into this MIT repository. Recipes name the upstream licence and cover
  credentials, data disclosure and permissions; compatibility evidence is not
  an endorsement or warranty.
- **XR-05** A script may ship inside a recipe only when FTMON is its original
  maintainer, its licence is explicit, it follows the bounded FTMON JSON or
  Nagios convention, and direct tests cover success and failure behavior.
- **XR-06** Publication metadata adds one bounded category, zero or more
  lowercase tags, and a minimum compatible FTMON version. These fields are the
  catalogue's search and compatibility authority; generated pages MUST NOT
  infer them from prose, executable output, popularity or filenames.
- **XR-07** `exchange.ftmon.org` is a deterministic static rendering of the
  committed recipe catalogue. It provides an HTML index, one stable page per
  recipe and a versioned JSON search index. Every recipe remains usable without
  JavaScript; client-side search and filters are progressive enhancement only.
- **XR-08** The publisher treats every recipe byte as untrusted data: it never
  imports or executes recipe scripts or commands, follows symlinks, accepts raw
  HTML, emits active URL schemes, or writes outside its fresh destination.
  Markdown is rendered through an explicit safe subset and all other values
  are escaped. A malformed recipe fails the build rather than producing a
  partial catalogue.
- **XR-09** Pull requests and ordinary CI build and test the site without
  deployment authority. Only a push to protected `main` may deploy the tested
  artifact through GitHub Pages with `contents: read`, `pages: write` and
  `id-token: write`; recipe changes never execute contributor-controlled code.
- **XR-10** Publication is curated documentation, not an executable
  marketplace or endorsement. The site links to upstream acquisition and the
  reviewed repository recipe, does not host third-party binaries, accounts,
  ratings, comments or uploads, and identifies confidence, privilege, network,
  licence and compatibility on every recipe page.

The initial catalogue proves three distinct paths: an unprivileged networked
HTTP/TLS Nagios plugin, a read-only SMART/NVMe check with constrained privilege,
and an original dependency-light FTMON JSON script whose metric feeds a Trend.

### 6.6 Shared AI contribution skills

Repository-owned skills make repetitive, security-sensitive contribution
workflows available to different coding agents without making any vendor's
private configuration authoritative.

- **AS-01** Canonical shared skills live at `.ai/skills/<name>/SKILL.md` and use
  portable Agent Skills frontmatter containing exactly `name` and `description`.
  A skill MUST direct an agent to read current repository authority before
  acting: `AGENTS.md`, SPEC, DESIGN, templates and tests override skill prose
  whenever they differ.
- **AS-02** The initial `ftmon-add-extra-monitor` skill covers Nagios and FTMON
  JSON recipes end to end: evidence level, installed/upstream version and
  licence, bounded execution authority, privilege and credential boundaries,
  observed metric/UOM mappings, meaningful rules/Trends, deterministic
  fixtures, operator article, Exchange metadata, validation and rationale-led
  commit. It MUST prohibit fabricated live evidence and vendored third-party
  executables.
- **AS-03** Shared skills grant no additional authority. They MUST preserve the
  user's approval boundary, protect unrelated worktree changes, avoid pushing,
  publishing or changing external systems unless explicitly requested, and
  never weaken parser/security tests to accommodate surprising plugin output.
- **AS-04** Vendor adapters are installation links or copies of the canonical
  skill, not independently edited sources. Documentation covers Codex personal
  installation and Claude Code personal/project installation while remaining
  explicit that discovery behavior belongs to those products and can change.
- **AS-05** CI validates skill naming/frontmatter, bounded size, absence of
  placeholders, referenced repository paths, documented validation commands,
  vendor UI metadata when present and the required workflow/security concepts.
  This structural test supplements realistic use; it cannot prove that every
  model will follow instructions correctly.

---

## 7. Calculations, baselines, and built-in monitors

### 7.1 Derived values

Monitors compute derived metrics from raw samples using expressions (§8) evaluated per entity per cycle. Derived metrics are stored like raw metrics and usable in rules, queries, MCP, and the web UI identically.

### 7.2 Function library (frozen surface)

Available in all expressions. `w` is a duration string (`"90s"`, `"10m"`, `"3h"`). Series functions operate on the **current entity's** series only (cross-entity references are not in v1). No keyword arguments exist anywhere in the language (EX-05).

| Function | Meaning |
|---|---|
| `last(m)` | most recent value of metric `m` (same as bare name `m`) |
| `avg(m, w)` / `min(m, w)` / `max(m, w)` | aggregate over window |
| `delta(m, w)` | last − first over window |
| `rate(m, w)` | per-second rate from delta (counter-safe: negative delta → 0, counts a reset) |
| `slope(m, w)` | least-squares slope, units/second; `None` with < 3 points |
| `monot(m, w)` | fraction of consecutive deltas > 0 in window (0.0–1.0) — the legacy "Filling" test |
| `coverage(m, w)` | fraction of the window actually observed: `(t_newest − t_oldest) / w`, clamped to 0.0–1.0; `None` with < 2 points. Window functions treat `w` as a maximum, so a "45m" verdict can otherwise rest on three samples — `coverage` lets a rule demand its window be *represented* (v0.19, issue #20) |
| `age(m)` | seconds since the last sample of `m` |
| `baseline(m)` | learned baseline (§7.4); `None` until learned |
| `pct(a, b)` | `100*a/b`; `None` if `b == 0` |
| `abs(x)`, `roundv(x, n)`, `clamp(x, lo, hi)`, `coalesce(x, d)` | numeric helpers |
| `matches(s, regex)` / `contains(s, sub)` | string tests (events and attrs) |
| `during("HH:MM-HH:MM")`, `dow()` | local-time window test; day-of-week string `"mon"…"sun"` |

- **CA-01** This table is the complete v1 function surface. Adding a function is a spec change. Implementers MUST NOT add conveniences.
- **CA-02** Any function receiving insufficient data returns `None`. `None` propagates by the three-valued semantics defined normatively in EX-06. A rule whose `when` evaluates to anything other than `True` does not fire; `None` additionally does not reset confirmation counters (IN-01).
- **CA-03** `rate` on counters MUST treat counter resets (negative delta) as 0 for that pair and increment a self-metric.

### 7.3 Windows and memory

- **CA-04** Series functions read from in-memory per-(entity, metric) ring buffers sized to the longest window any loaded expression references for that metric (validation computes this). Hard limits: no expression window may exceed 6 h or imply > 10 000 points (rejected at validation); total ring-buffer memory is capped at 64 MB — on breach, buffers of non-watchlist, non-promoted entities are evicted LRU with a self-event.

### 7.4 Baselines

- **CA-05** `baseline(m)` is an **exponentially weighted mean**, precisely: updated once per 5-minute rollup of `m` as `b ← b + α·(rollup_avg − b)` with `α = 1 − 2^(−Δt/half_life)`, half-life default 3 d (config per monitor). It is stored persistently per (monitor, entity, metric) with its update count and the effective `half_life_s`. That half-life is immutable for one baseline lifetime: changing it reseeds that series from the next rollup (`value = rollup_avg`, `updates = 1`) so one row can never mix EWMA coefficients. It returns `None` until **coverage** ≥ 240 rollup updates (~24 h of actual data — counted updates, not elapsed time), although read-only consumers may show the learning level and `min(updates/240, 1)`. A new entity_id (process restart) starts a fresh baseline. Metrics may reconstruct the current lifetime backwards from the stored value through retained 5-minute rollup averages, using at most `updates − 1` inverse steps and never reversing the seed. Reconstructed points retain native five-minute timestamps; missing or pruned buckets are never interpolated, and truncation is reported relative to the first five-minute bucket in the requested range. Data sampled during open incidents is *not* excluded (documented contamination caveat; acceptable for v1). Seasonality: NG-07.
- **CA-06** `ftmon baseline reset <monitor> [entity]` clears learned baselines.

### 7.5 Exemptions

- **CA-07** Every monitor supports an `exempt` list of entity-match expressions evaluated before rules (e.g. process name regexes for compilers/browsers on the hog monitor; fs types on the disk monitor, succeeding legacy `SKIP_FS_P`). Exempt entities are sampled only into the bounded in-memory context required to evaluate exemptions; no rules fire and no entity, sample, rollup or baseline state is persisted. When an entity first matches, any previously persisted metric/baseline state for that monitor/entity is removed atomically, so Metrics, Trends, Baselines and glance cannot retain an excluded entity.

### 7.6 Entity disappearance

- **CA-08** When a **discovered** entity (process, mount) stops appearing in snapshots, its metrics simply stop (rules go `None` via CA-02). After `gone_grace` (default 5 m) the entity is marked gone (`gone_ts` in DM-03): its confirmation counters reset and any open incident for it clears with `clear_reason = entity_gone` and a recovery notification whose message says so (a leaking process that exits is a resolved leak). **Watchlist** entities (service units, expected listeners) never disappear: they are synthetic, always present, with a `present` (0/1) metric — absence is their alerting signal, not their removal.
- **CA-09** The disk monitor persists a signed `fill_rate_bph` derived from the 70-minute least-squares slope of `used_bytes`. A projected-full time is displayable only when the rate is positive, `filling >= filling_frac`, and the slope window has sufficient coverage; otherwise consumers MUST report that no reliable projection is available. This gate prevents flat, shrinking, sparse, or irregular history from producing a mathematically finite but operationally misleading date.

- **CA-10** Generic trend rates MUST reference persisted raw or derived metrics; the trend layer never differentiates display points. Value and rate panels are required; confidence and projection panels are optional. Projection requires a declared remaining metric, positive rate, and—when declared—a passing confidence threshold. An absent panel is `null`, not synthetic or empty data, so clients distinguish “not meaningful” from “temporarily no observations.”

### 7.7 Built-in monitors (seven user monitors + `self`)

v1 ships seven user-facing monitors plus the always-installed **`self`** monitor (§13, RB-02) — `self` is tunable but not deletable. Each ships as a commented TOML file (FS-02); defaults below are starting points reviewable in the file, but the *shape* (parameters, metrics, rule structure) is normative. `OPEN-1`: default numbers need owner review — to be exercised against recorded fixture data and a short real-system observation period before v1.0.

#### 7.7.1 `leak` — per-process memory-leak detector
Metrics: `rss_bytes` (+ derived `rss_slope_bph` = slope in bytes/hour). Promotion heuristic (SA-05): `monot(rss_bytes, "15m") >= 0.8 and delta(rss_bytes, "15m") > 16*MB`. Rules (one group `leak`, v0.19 shape): both rungs MUST require, alongside their slope threshold, `coverage(rss_bytes, "45m") >= min_coverage` (default 0.8 — a 45-minute verdict needs the window represented, not three samples) and `delta(rss_bytes, "45m") > min_net_mb * MB` (default 16 — an earlier rise followed by a fall is not an active leak): warning when `slope(rss_bytes, "45m") * 3600 > 32*MB` with `confirm_cycles = 3`; error rung when `slope(rss_bytes, "45m") * 3600 > 128*MB`. Growth confidence (`monot`) is deliberately **not** an alert gate: a genuine stepwise leak (grow, plateau, grow) scores low on consecutive-delta confidence, and with the window covered, full-window slope plus net delta already reject oscillation; `monot` remains the promotion/trend signal. Messages say "sustained RSS growth", not "leaking" — slope is evidence of growth, not proof of a leak. Exempt-by-default: none (browsers are the *point*); the file shows a commented example targeting executable identity (`exe_base`, SA-09) rather than the generic runtime process name. Glance: maximum `rss_slope_mbph`, labelled with the matching warning and error rate parameters.

#### 7.7.2 `hog` — CPU hog detector
Metrics: `cpu_pct`. Rules (group `hog`): warning when `avg(cpu_pct, "5m") > 80` for `confirm_cycles = 5`; error rung at `avg(cpu_pct, "15m") > 90`. Default exempt examples (commented): `matches(name, "^(cc1|rustc|ld|clang|make|cargo|ffmpeg)")`. Glance: maximum five-minute CPU average with only its matching warning parameter; the fifteen-minute error threshold MUST NOT be presented as though it applied to that value.

#### 7.7.3 `events` — journal/event-log entries of interest
Consumes the event stream; rules are **episode** rules (IN-08). Example shipped enabled: `severity >= error and not matches(provider, "^(tracker-|gnome-shell$)")`; a specific-ID example (`event_id == "6008"`, styled for future Windows use) ships commented; a third rule targeting `provider == "kernel"` OOM messages also ships enabled on the generic/Linux tree. Episode identity: `(rule, provider, event_id if present else msg_hash)`. `msg_hash` is normatively defined: lowercase the message, collapse whitespace, replace digit runs and hex runs (≥ 8 chars) with `#`, then SHA-256, first 16 hex chars — collisions merely group unrelated events, which is harmless. Per-rule `cooldown` (default `"10m"`) limits renotification; `clear_after` (default `"30m"` without a matching event) closes the episode with `clear_reason = quiet_period` and **no recovery notification** by default (`notify_recovery = false` for event rules). A new matching event after clearing opens a new episode; the flap guard (IN-05) applies. The `windesktop`/`winserver` profile tree drops the `provider == "kernel"` OOM rule: `"kernel"` is a journald syslog-identifier convention no Windows Event Log provider is ever named, so the rule would sit in the file permanently dead rather than degrading gracefully; no replacement Windows low-memory event is wired up yet.

#### 7.7.4 `disk` — space + filling
Metrics per mount: `used_pct`, `free_bytes`, `used_bytes`, `inode_used_pct`; derived `filling = monot(used_bytes, "70m")`. Rules: ladder group `space` — notice/warning/error rungs at `used_pct >` 85/92/97 (plus commented baseline-relative alternative `free_bytes < baseline(free_bytes) * 0.3`); separate group `inodes` (rungs at 75/80/90); separate single-rule group `filling` — warning on `filling >= 0.85` with projected-full time in the message. Exempt: `matches(fstype, "^(tmpfs|iso9660|squashfs)$")`. The `windesktop`/`winserver` profile tree drops the `inodes` group entirely: NTFS has no POSIX inode concept, so `inode_used_pct` is always absent there and the ladder would never fire — dropped rather than left in the file to imply coverage that doesn't exist.

#### 7.7.5 `load` — system pressure
Metrics: `load1`, `cpu_pct`, `mem_available_bytes`, `mem_total_bytes`, `swap_used_pct`, PSI `psi_some_cpu`/`psi_some_mem`/`psi_some_io` (60 s avg) where present. Rules: group `pressure` — warning when `avg(psi_some_cpu, "5m") > 40` or `pct(mem_available_bytes, mem_total_bytes) < 5` for 5 cycles; error rung on `slope(swap_used_pct, "10m") > 0 and avg(psi_some_mem, "5m") > 25`. Glance: five-minute CPU PSI with its warning parameter. On kernels without PSI the readout is absent rather than replaced by an inferred secondary metric.

#### 7.7.6 `service` — process/unit presence
Watchlist-driven (no auto-discovery): each target is a systemd unit or process-name regex, expected state, optional `during` schedule. Metrics: `present` (0/1), `restarts`. Rules: error when `present == 0` for `confirm_cycles = 2`; notice on flapping (`delta(restarts, "30m") >= 3`).

#### 7.7.7 `net` — sockets
Watchlist of expected listeners (`proto:port`) → `present` metric, error when absent (as `service`). System-wide `conn_total` and per-state counts with a warning on `conn_total > baseline(conn_total) * 4` sustained 5 cycles. Per-process attribution: NG-06 (deferred).

---

## 8. Monitor definitions and the expression language

### 8.1 TOML schema (normative shape)

```toml
schema = 1                       # definition-format version (VC-02)

# Top-level keys (schema, exempt) MUST appear before the first [table]
# header - TOML binds bare keys to the most recently opened table otherwise.
exempt = [ "matches(fstype, '^(tmpfs|iso9660|squashfs)$')" ]

[monitor]
name = "disk"                    # [a-z][a-z0-9_]{1,31}, unique
description = "Disk space and filling"
version = 3                      # integer, bumped on edit
enabled = true                   # false = loaded nowhere, kept in place
platforms = ["linux"]            # subset of linux|windows|darwin
interval = "60s"
source = "disk"                  # sampler name, or "events"

[source_options]                 # only for sources that take targets
# watchlist = [ { unit = "sshd.service" }, { process = "^syncthing$", during = "09:00-18:00" } ]

[parameters]                     # legacy *_P successors; referenced by bare name in expressions
warn_pct  = { value = 92, doc = "Warning threshold, percent used" }
crit_pct  = { value = 97, doc = "Critical threshold" }

[[derived]]                      # optional derived metrics (may reference other derived, MD-08)
name = "filling"
expr = "monot(used_bytes, '70m')"

[[rule]]
id = "space-warn"                # unique within monitor, [a-z0-9-]+
group = "space"                  # optional; default = rule id (IN-03)
when = "used_pct > warn_pct"
severity = "warning"
confirm_cycles = 3
clear_cycles = 3                 # optional; default = confirm_cycles
message = "Disk {entity} at {used_pct:.0f}% used"
# action = "cleanup"             # optional: name of script in actions/ (no path, no args)
# cooldown = "10m"               # episode (event) rules only
# clear_after = "30m"            # episode (event) rules only
```

- **MD-01** The full schema (all keys, types, bounds, which keys are required/forbidden per `source` kind — including `source_options` shapes for `service`, `net`, and `external`) is defined once as a versioned JSON-Schema-equivalent in code; `ftmon check`, `define_monitor`, and daemon loading all use the *same* validator. Error messages MUST name the file, key, and reason.
- **MD-02** `message` is a Python `str.format`-style template; only entity attrs, parameters, and metric names are available; formatting errors at validation time, not fire time.
- **MD-03** Unknown keys are validation errors (protects against silent typos in AI-authored drafts).
- **MD-04** A definition referencing a sampler, metric, attr, or function that doesn't exist (per PL-05 declarations) fails validation with a suggestion (closest name).
- **MD-07** The eight built-in definition files and the JSON-Schema are **normative deliverables of the design phase**: all must pass `ftmon check`, and every expression appearing in this spec must pass the validator, *before* the expression language and schema are frozen for implementation. (This exercise is the completeness check the external review called for.)
- **MD-08** Derived metrics may reference other derived metrics; evaluation order is topological, computed at validation; dependency cycles are a validation error naming the cycle.

### 8.2 Expression language

- **EX-01** Expressions are parsed with Python's `ast` module in `eval` mode. Permitted node types, exactly: `Expression, BoolOp, BinOp, UnaryOp, Compare, Call, Name, Constant, List, Tuple, IfExp, And, Or, Not, USub, Add, Sub, Mult, Div, Mod, Eq, NotEq, Lt, LtE, Gt, GtE, In, NotIn`. Everything else — `Attribute`, `Subscript`, comprehensions, lambdas, f-strings, walrus, starred, and **all keyword arguments** — is rejected at parse time with the offending fragment quoted.
- **EX-02** `Call` targets must be bare `Name`s in the CA function table (§7.2). `Name` lookups resolve, in order: entity metrics → entity attrs (string-valued; declared per PL-05, `None` if absent at runtime) → monitor parameters → language constants. The language constants are: `None`, `True`, `False`; unit multipliers `KB, MB, GB, TB` (binary, so `32*MB` reads naturally); severity levels `info, notice, warning, error, critical` (integers 0–4, usable as `severity >= error`). Names resolving to nothing fail validation.
- **EX-03** Evaluation is pure: no I/O, no state mutation, deterministic given (series windows, attrs, parameters, clock). Evaluation of one expression is capped at 10 ms CPU (defense in depth; the whitelist should make this unreachable, and it is also the backstop for pathological regexes, EX-07).
- **EX-04** The parser/evaluator is a standalone module with **zero** imports from the rest of ftmon, property-tested (§16.3) — it is the security boundary and the most-reused component (rules, derived, exemptions, promotion heuristics, MCP query filters all use it).
- **EX-05** No keyword arguments, no cross-entity references, no user-defined functions. (Restates the §7.2 scope as a testable parse-time rule.)
- **EX-06** Three-valued semantics, normative truth table (`?` = `None`):

  | Expression | Result |
  | --- | --- |
  | any arithmetic op with a `?` operand; `?` compared with anything (`==`, `<`, `in`, …); any chained comparison containing `?` | `?` |
  | `not ?` | `?` |
  | `? and False` / `False and ?` | `False` |
  | `? and True` / `True and ?` | `?` |
  | `? or True` / `True or ?` | `True` |
  | `? or False` / `False or ?` | `?` |
  | `x / 0`, `x % 0` | `?` (+ self-metric) |
  | any float result that is NaN or ±inf | `?` |
  | `coalesce(?, d)` | `d` |
  | `IfExp` with `?` condition | `?` |

  A rule fires iff its `when` is exactly `True`. `and`/`or` short-circuit left-to-right (so `x != None and x > 5` — spelled `coalesce(x, -1) > 5` or relying on `? > 5 → ?` — never raises). No expression evaluation ever raises to the caller.
- **EX-07** Regexes in `matches` are compiled at validation time; invalid patterns are validation errors; pattern length ≤ 512 chars. Runtime pathological backtracking is bounded by EX-03's evaluation cap (result `?`, self-metric incremented).

### 8.3 Definition lifecycle

- **MD-05** States: **draft** (in `monitors/drafts/`, never loaded by the daemon) → **enabled** (in `monitors/`) → **disabled** (`enabled = false` key retained in place, so disabling is a one-line edit and history stays in git/file). Approval = `ftmon monitor approve <name>` (CLI or web UI button) performing PM-06(d).
- **MD-06** Editing an enabled file (PM-04) resets that monitor's incidents to `cleared (superseded)` and its confirm counters — a changed rule never inherits confirmation progress from its previous self.
- **MD-09** Removing or renaming a definition: open incidents clear with `clear_reason = superseded`; stored samples/rollups age out by normal retention; baselines for the monitor are deleted; entity records are retained until their data ages out. A renamed monitor is a removal plus an addition (no identity continuity). "Ages out" is concrete (v0.43 amendment, issue #74): retention reaps a `gone` entity — and its `series`/`baselines` — once none of its series retain any `samples`/`rollup5m`/`rollup1h` row and no non-cleared incident references it. Watchlist/synthetic entities never reach this state: CA-08 keeps their `last_seen` refreshed every tick (they are always present), so `gone_ts` never gets set for them and they are structurally exempt, not specially cased. Reap runs unconditionally alongside normal pruning (it never removes data still inside its DM-04 window, so it is not a DM-05 degradation step) in bounded catalog-visited batches, the same catch-up shape as rollup rollforward.

---

- **MD-10** Sampler monitor definitions MAY declare validated `[[trend]]` profiles. Each profile has a unique `id`, `kind = "growth"|"capacity"`, title, value/rate metric and units, optional confidence metric + threshold parameter, optional value/rate threshold-parameter lists, and optional incident group. Capacity additionally requires a remaining metric. Every referenced metric and parameter MUST exist in that definition. Presentation behavior is declared, never inferred from metric names.
- **MD-11** `source = "external"` requires `source_options.check`,
  `source_options.entity`, and zero or more `[[source_options.perfdata]]`
  mappings exactly as bounded by EC-04/08. The check alias must exist in the
  currently valid registry during daemon load; `ftmon check <definition>` may
  validate syntax without a live registry but reports the unresolved alias as
  a distinct readiness warning, while approval/enabling requires resolution.
  Mapping changes are ordinary schema changes under MD-06: removed metrics stop
  sampling and age out; they are never silently renamed or reinterpreted.
- **MD-12** Sampler monitor definitions MAY declare one validated `[glance]`
  primary readout with required persisted `metric`, explicit display `unit`,
  and required `aggregate = "max"|"min"`. It MAY contain up to four ordered,
  uniquely labelled threshold references to existing parameters. Metrics,
  units, aggregation and threshold meaning are definition metadata; the loader
  and presentation layer MUST NOT infer them from rules, metric names,
  parameter names or trend profiles. Event monitors cannot declare a glance.
  `aggregate` stays `max|min` in TOML: UI-18's fixed ingest readout is reported
  with the response-level label `last`, which no definition may declare.
- **MD-13** A `source = "events"` monitor's `[source_options]` MAY declare
  `channels` (§5.3 DM-19): an array of `{path, query}` tables, `path` required
  and unique within the array (≤16 entries, ≤256 chars), `query` optional
  (≤2048 chars) — and `store_min_severity` (an override of DM-09's default
  threshold, as a severity name or 0–4 int). Unknown keys in either are
  validation errors, same as every other source's `[source_options]` shape
  (MD-11).

## 9. Incident lifecycle and notifications

### 9.1 State machine

```
        rung when==True for confirm_cycles consecutive cycles
(none) ─────────────────────────────────────────────────────▶ open ──▶ notify #1 (+action)
  ▲                                                             │
  │ all rungs False for clear_cycles   ┌── higher rung confirms ┤──▶ severity ↑, backoff reset, notify
  │ (or clear_after / entity_gone)     │   top rung clears,     │──▶ severity ↓ in place, silent
  └────────────── cleared ◀────────────┘   lower still true     │──▶ renotify per backoff while open
                     │                                    acked ◀── user/AI ack (suppresses renotify)
                     └──── recovery notification (per rule config)
```

- **IN-01** Confirmation and clear counters are per (rule, entity). `when == None` (EX-06) neither increments nor resets any counter (missing data is not evidence of recovery); `when == False` resets the confirm counter and increments the clear counter.
- **IN-02** An open incident renotifies on backoff **5 m → 15 m → 1 h → 6 h (repeating)**, computed from the notification history in the outbox (DM-14) so daemon restarts don't re-fire. `acked` suppresses renotification but the incident stays visible and still clears normally (ack ≠ resolve).
- **IN-03** **Ladder groups.** Incident identity is (monitor, entity, group); a rule's `group` defaults to its own id, so ungrouped rules behave as independent per-rule incidents. Within a group: each rung (rule) keeps independent confirm/clear counters; the incident opens when the first rung confirms; **severity = highest currently-confirmed rung**, and `owning_rule_id`, message, and action come from that rung. A higher rung confirming raises severity in place, resets the backoff schedule, and notifies. The top rung clearing (its `clear_cycles`) while a lower rung remains true lowers severity in place **silently** (recorded in history, no notification). The incident clears when **all** rungs in the group have been false for their `clear_cycles`.
- **IN-04** Clearing sends exactly one recovery notification (severity `info`) referencing the incident duration and peak severity — except episode rules with `notify_recovery = false` (the default for event rules, §7.7.3) and as noted in CA-08's `entity_gone` wording.
- **IN-05** Flap guard: an incident (or episode) that re-opens within 10 m of clearing 3+ times marks itself `flapping` (attr), switches to the 6 h backoff tier immediately, and says so in the notification.
- **IN-06** The state machine is implemented as a pure function `(state, evaluations, now, config) → (state', effects)` with effects (`enqueue_notification`, `run_action`, `record`) executed by the caller — this is a hard requirement so it can be exhaustively table-driven-tested (§16.3).
- **IN-07** Entity disappearance interacts with incidents per CA-08 (`entity_gone` clearing after `gone_grace`).
- **IN-08** **Episodes** (event rules) are incidents whose identity is `(rule, provider, event_id|msg_hash)` (§7.7.3) and whose lifecycle differs in exactly three ways: a matching event opens (no confirm cycles unless `confirm_count > 1` within `confirm_window`), refreshes `last_seen`, and increments `occurrences`; renotification is governed by `cooldown` instead of the IN-02 backoff; clearing is by `clear_after` quiet period (`clear_reason = quiet_period`) instead of false evaluations. Everything else (ack, flap, history, outbox) is shared.
- **IN-09** Startup reconciliation (v0.19, issue #20). CA-08's disappearance tracking is in-memory; a restart between an entity's last sample and `gone_grace` expiry would otherwise strand its incident open forever (rules evaluate `None`, so clear cycles never accumulate). On startup, after rebuilding open incidents (IN-02), the daemon MUST seed disappearance tracking for each open/acked incident's discovered entity from the stored entity `last_seen`, so an entity that vanished while the daemon was down still clears with `clear_reason = entity_gone` once `gone_grace` has elapsed since it was genuinely last seen. Restarts do not manufacture immortal incidents; CA-08/IN-07 semantics hold across restarts.

### 9.2 Notification contract

- **NO-01** A notification carries: severity glyph + monitor + entity, the rendered rule `message`, and (where the platform allows) a "details" hint pointing at `ftmon incident <id>` / the web UI URL. Body ≤ 200 chars; truncation is deliberate — depth lives in the UI/CLI.
- **NO-02** The notifier is an adapter interface (PL-01). Its foundational
  implementations are `desktop` (`notify-send`) and mandatory `file` (append
  JSON-lines at `~/.local/state/ftmon/notifications.jsonl`, also used by tests);
  the bounded remote implementations are specified by NO-05. The desktop
  adapter bounds its tray footprint: `renotify` and `recover` deliveries are
  transient (banner only), each incident's lifecycle reuses one replaceable
  notification slot, and only severity 4 maps to `critical` urgency — a
  monitor's tray pile-up is what arms gnome-shell's notification/calendar
  SIGABRT (LP #2138529, issue #40). Capabilities are probed from the installed
  `notify-send`; a missing flag degrades that behavior to plain persistent
  delivery, never to a delivery failure. The macOS adapter invokes
  `osascript display notification` without requiring an FTMON app bundle; the
  OS attributes these notifications to Script Editor
  (`com.apple.ScriptEditor2`). Exit 0 means accepted for best-effort delivery,
  not proof that Notification Center displayed a banner. The adapter MUST NOT
  depend on private `com.apple.ncprefs` flags to infer global notification,
  Focus, or per-app state; command failure/timeout is reported normally, while
  OS suppression after exit 0 degrades silently. A future FTMON-specific
  authorization preflight requires a bundled `UNUserNotificationCenter`
  helper and is outside the `osascript` adapter.
- **NO-03** Global quiet hours (`config.toml`, default off): during quiet hours, `warning`-and-below notifications are held and delivered as one digest at quiet-hours end; `error`+ always notify. Incidents open/clear regardless — quiet hours affect delivery only. Global-only in v1 (per-monitor overrides deferred).
- **NO-04** **Delivery guarantee — at-least-once, honestly.** The notification
  and its DM-18 channel deliveries are committed with the incident transition;
  each delivery is marked delivered only after its adapter returns success. A
  crash after send but before that update can duplicate at most the one
  in-flight delivery per process; exactly-once is not promised. Retry and
  terminal-failure policy is NO-07. No committed incident transition silently
  loses its notification or local audit record.
- **NO-05** Supported delivery channels are `file`, `desktop`, `ntfy`,
  `webhook`, and `smtp`. File remains the mandatory local audit channel.
  ntfy uses its HTTP publish API; webhook sends FTMON's documented JSON shape;
  SMTP uses authenticated message submission. Messenger-specific adapters are
  deferred until a generic webhook cannot represent a required capability.
- **NO-06** Each optional channel has `enabled` and `min_severity` settings. Delivery is
  fan-out, not fallback: every enabled and severity-eligible channel gets an
  independent DM-18 row. Quiet-hours decisions happen before fan-out, so all
  remote channels receive the same digest semantics as desktop delivery.
- **NO-07** A failed remote delivery retries independently after
  `30 s, 2 m, 10 m, 1 h, 6 h`, repeating at 6 h for no longer than 24 h.
  Timeouts, connection failures, HTTP 408/429/5xx, and SMTP 4xx are retryable;
  other HTTP 4xx and SMTP 5xx are permanent. `Retry-After` may lengthen but
  never shorten the next delay. At exhaustion the delivery becomes `failed`,
  a self-event is recorded, and file audit delivery remains unaffected.
- **NO-08** Remote requests have a 10 s total timeout, bounded response/error
  bodies, default platform TLS verification, no redirects from HTTPS to HTTP,
  and no proxying of untrusted incident content into headers or URLs. Webhook
  payloads include schema version, incident ID, kind, severity, title, body,
  monitor, entity, and timestamp; receivers must tolerate additive fields.
- **NO-09** ntfy credentials use a bearer token read through SE-05; topics are
  configuration, not secrets, but documentation recommends an authenticated
  non-guessable topic. Public ntfy service users are warned that notification
  content leaves the host and may be retained by that service. SMTP requires
  STARTTLS or implicit TLS unless the host is loopback.
- **NO-10** Channel configuration is validated at startup and reload. An
  invalid channel is disabled with a visible config error while monitoring and
  other delivery channels continue. `ftmon doctor` reports channel readiness
  without sending a test message or exposing credentials; an explicit future
  `--send-test` operation is outside this milestone. Configuration readiness
  alone is not a delivery health claim: doctor MUST additionally report the
  dispatcher state published under PM-12 and a backlog split of total pending,
  claimable-due, quiet-held, and failed deliveries plus the age of the oldest
  claimable-due delivery, and MUST fail when a live daemon's dispatcher is dead
  or its oldest claimable-due delivery is overdue. Pending rows held by quiet
  hours are durable debt but are not claimable and MUST NOT count as overdue.
  Because these predicates describe a *running* daemon, they apply only while
  the daemon is live; against a stopped daemon the backlog is reported without
  failing. The `self` source exposes the same aggregates, the PM-12 store-error
  counter, a worker-liveness gauge, and a bounded count of terminally failed
  deliveries, and `/self` presents them with the per-channel breakdown, so a
  broken notification path is observable without notifying about the notifier.

---

## 10. Actions

- **AC-01** An action is an executable file in `~/.config/ftmon/actions/`; a rule references it by bare filename (no path separators, no arguments). At load time a rule naming a nonexistent/non-executable action fails validation.
- **AC-02** Actions run on incident **open** only (not renotify/escalate/downgrade/clear — a documented v1 limitation) with a 30 s timeout, rate limit 1 run / action / 10 m, environment: `FTMON_MONITOR, FTMON_RULE, FTMON_ENTITY, FTMON_SEVERITY, FTMON_MESSAGE, FTMON_INCIDENT_ID, FTMON_VALUE` — nothing else beyond a minimal PATH. stdout/stderr (capped 8 KB) and exit code recorded into incident history.
- **AC-03** Nothing in ftmon ever creates, edits, or chmods files in `actions/` — including MCP and web UI. Drafts may *reference* actions; approval of a draft referencing a not-yet-existing action fails validation (AC-01) until the user creates the script themselves.

---

## 11. MCP server

Served over stdio by `ftmon mcp` (FastMCP). All tools are synchronous reads of the DB except the three marked ✎.

| Tool | Signature (abridged) | Behavior |
|---|---|---|
| `get_status` | () | daemon liveness, last cycle, monitor list w/ state, open incident counts, budget self-metrics; additive `glances[]` of dashboard primary readouts (`monitor`, `entity_id`, `metric`, `value`, `unit`, `aggregate`, `thresholds[{label,value}]`) for UI-14 trustworthy states only, bounded to ≤64 with always-present truncation metadata (`glances_returned`/`glances_matched`/`glances_truncated`, `limits.max_glances`) |
| `query_metrics` | (monitor, metric, entity?, range, agg?, filter_expr?) | series data, resolution auto-chosen (DM-06); bounded to ≤50 entities and ≤10 000 total points with truncation metadata; empty `series` includes `empty_reason` (`unknown_metric` \| `no_data_in_range` \| `filtered_out`) and `available_metrics` (declared ∪ persisted); `filter_expr` uses §8.2 language over entity attrs |
| `top_consumers` | (resource: cpu\|rss\|io, range, n=10) | ranked entities with aggregates over range |
| `get_process_history` | (name_or_pid, range) | metrics + lifecycle (starts/stops/gone) for matching process entities |
| `list_events` | (range, min_severity?, provider?, match_expr?, limit=200) | canonical events |
| `list_incidents` | (state?, range?, monitor?) | incidents/episodes with summaries |
| `explain_incident` | (id) | rule text + parameter values, evaluation series around opening, related events ±10 m, full history (DM-12) |
| `list_monitors` / `get_monitor` | (name) | definitions incl. drafts (marked), validation status, load history (PM-07) |
| `monitor_paths` | () | resolved filesystem layout an author needs (monitors, drafts, actions, check registry, db) — the JSON form of `ftmon paths` (CL-06) |
| `diagnose_monitor` | (name) | where the file lives (enabled/draft/missing), validation errors, enabled state, last load hash and age, for external monitors whether the alias is registered and its executable trusted (no argv exposure, SE-07), and `last_result` for the configured `source_options.entity` (`plugin_state`/`plugin_ok`/`duration_s`/`plugin_message`/`sample_age_s`, or null when never sampled / non-external / no DB) |
| `list_baselines` | (monitor?, entity?, metric?, ready?, limit=100, cursor?) | learned level, update-count coverage/readiness, effective half-life and last update for stored baseline rows; deterministic keyset pagination |
| `validate_monitor` ✎(no writes) | (toml_text) | full validation, returns errors or normalized form |
| `define_monitor` ✎ | (toml_text) | validate → write to `drafts/` (PM-06) → return draft path plus structured `next_steps` (CLI approve command and web UI) |
| `ack_incident` ✎ | (id, note?) | sets acked with `by = "mcp"`, note into history |

- **MC-01** The tool list above is the complete v1 tool surface; names and required parameters are frozen by this spec (exact JSON schemas in the design doc). Every tool answers within 2 s on a DM-05-sized database. `query_metrics` MUST use an observed-first, work-bounded read (list entities with in-range observations, then fetch points only for returned entities after a capped-count preflight) so high entity cardinality cannot force full point materialization for discarded series. `get_status` additionally returns `glances`: the UI-17/UI-18 readouts of monitors whose UI-14 state permits one, each record identifying monitor, winning entity, metric, raw value, declared unit, aggregate and ordered labelled thresholds with raw values. It is bounded at 64 records ordered by monitor name ascending, and `glances_returned`, `glances_matched`, `glances_truncated` and `limits.max_glances` are present on every response including empty ones. `get_status` MUST load definitions with the same action and check-registry authority as the dashboard, so a monitor whose external alias is unavailable is reported as `config_error` rather than as a loaded monitor.
- **MC-02** Range parameters accept `"90m"`-style durations or ISO-8601 pairs; all responses carry UTC timestamps plus the host's IANA timezone name once per response for the model to localize.
- **MC-03** `define_monitor` MUST refuse (not silently overwrite) a name that already exists as enabled/disabled; drafts may be overwritten (iterating on a draft is the normal flow).
- **MC-04** Error responses are structured (`code`, `message`, `hint`) — a less capable model must be able to self-correct from validation errors (MD-01's quality bar applies).
- **MC-05** The server exposes exactly three packaged MCP **resources**:
  `ftmon://docs/definitions` (DO-01), `ftmon://docs/check-authoring` and
  `ftmon://docs/external-checks` (DO-07). A model authoring a definition or
  external check on an installed host can pull the canonical guides without a
  repository checkout. Tool descriptions MUST steer authors to those resources
  for definition traps, attribute-only `filter_expr`, and the ftmon-json
  exit-0 contract; the resource count remains exactly three. The full SPEC is
  not exposed (operational noise).
- **MC-06** `monitor_paths` and `diagnose_monitor` are strictly read-only diagnostics: they answer "where do files go?" and "why isn't this monitor running?" in one round-trip each. `diagnose_monitor` may surface validation errors verbatim (already exposed by `get_monitor`) but MUST NOT expose registry argv or credentials — trust status is reported as booleans and stable categories only (SE-07). For every found monitor it also returns `last_result`: null when there is no DB, the monitor is non-external, or the **currently configured** `source_options.entity` has never produced a coherent EC-05 sample; otherwise the stored `plugin_state` (0–3), `plugin_ok`, `duration_s`, sanitized `plugin_message`, and `sample_age_s` for that entity at one shared sample timestamp. That block re-exposes already-persisted EC-05 fields under the local single-user trust model (SE-04): no registry argv or credentials; stderr remains excluded. `plugin_message` is control-stripped/truncated plugin stdout — FTMON does not apply secret-pattern redaction (NG-08). Write paths remain exactly drafts (`define_monitor`) and `ack_incident`; approval stays a human action (MD-05).
- **MC-07** `list_baselines` is read-only, bounded and deterministic. It lists all stored baseline rows (never rows inferred from definitions) in `(monitor, entity, metric)` order, with optional exact filters and readiness filter. `limit` defaults to 100 and MUST be in `1..500`; pagination uses an opaque keyset cursor containing the last key and canonical filters so malformed or filter-mismatched cursors return MC-04 `invalid_params`. Learning rows expose their current level plus `updates`, `required_updates`, capped coverage, `ready`, UTC update bucket and effective half-life; `next_cursor` is null at the end.

---

## 12. Web UI

A local, single-user, AI-optional interface — the modern successor to legacy's generated HTML, and deliberately much better.

- **UI-01** `ftmon web` serves on 127.0.0.1:8420: no external network assets whatsoever (all JS/CSS/fonts vendored; must work fully offline), no auth (NG-05).
- **UI-02** v1 pages: **Dashboard** (per-monitor status tiles, open incidents, daemon health/budget strip, sparklines); **Incidents** (filter, detail view = `explain_incident` rendered, ack button); **Metrics explorer** (pick monitor/entity/metric/range → chart; shareable URL state); **Baselines** (read-only, paginated learned levels and coverage linking into Metrics); **Events** (filterable browser); **Monitors** (definitions rendered with docs, enable/disable toggle, drafts with rich validation view and **Approve** button); **Self** (daemon log tail, self-metrics, DB size, config errors).
- **UI-03** Write operations are exactly: ack incident, enable/disable monitor, approve/delete draft. Each is a POST hitting the same code paths as the CLI equivalents (incl. PM-06).
- **UI-04** Data freshness uses full-page polling without SSE: dashboard, incident and Events views reload every 5 s; Monitors and Self reload every 15 s. Metrics, Trends and Baselines do not auto-refresh in v1. A stale daemon (last cycle > 3× base interval) shows an unmistakable banner. That boundary is one shared predicate: the daemon is stale when the last-tick age is unknown or strictly greater than 15 s, and alive at exactly 15 s. Every read consumer (web UI, MCP) MUST use it rather than deriving its own comparison.
- **UI-05** Charts must remain legible with 400 d hourly data (downsampled server-side to ≤ 2 000 points per series per request).
- **UI-06** Server-side rendering with minimal vendored JS (htmx-style partials + one small chart library — chosen in the design doc for size, accessibility, and long-range rendering) is the required *style*: no SPA framework, no frontend build step beyond file copying.
- **UI-07** The web server process is optional at runtime: nothing else may depend on it.
- **UI-08** Request hardening despite loopback: exact `Host` header allowlist (`127.0.0.1:<port>`, `localhost:<port>`) — anything else is 400 (defeats DNS rebinding); POSTs require a matching `Origin`; no CORS headers are ever emitted. Every response, including rejected requests and synthetic-demo responses, sets the SE-02 CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`, and `Cross-Origin-Opener-Policy: same-origin`.
- **UI-09** Accessibility: severity is never conveyed by color alone (icon + text label); all interactive elements keyboard-operable; `prefers-reduced-motion` respected; every chart has a text alternative (current value + trend sentence).
- **UI-10** Historical disk trends MUST show synchronized capacity, signed fill-rate/confidence, and projection views for one mount and a shareable range. Capacity includes configured threshold lines and stored min/max rollup envelopes; incident transitions are overlaid. The response and page state identify units, resolution, coverage, and UTC timestamps.
- **UI-11** Forecast presentation MUST be honest: unstable/unqualified projections are rendered as a gap and explanatory text, never as a huge sentinel value. Every disk-trend chart has a textual summary containing current use, change over the selected range, signed rate when qualified, filling confidence, and either a projected-full date or the reason it is unavailable.

---

- **UI-12** Primary navigation MUST expose one generic **Trends** explorer selecting monitor, profile, entity, and shareable range. Its entity selector MUST list recently seen active entities rather than every retained historical identity; an explicitly requested historical entity MUST remain selectable so incident links and bookmarks keep working. Dashboard monitor tiles, monitor details, and incident details link into that explorer with context preselected. `/disks` remains a compatibility redirect to the disk capacity profile. The page renders only declared panels and provides a profile-specific textual summary and incident overlays.
- **UI-13** Metrics Explorer remains the diagnostic single-series surface for any persisted metric, including metrics without a trend profile. Its cascading selectors MUST include only series with observations in the selected range and resolution tier; an explicitly requested persisted series remains selected after its observations expire and renders a textual no-observations state rather than an empty graph or silent fallback. It MUST use the same vendored chart renderer, time-axis/cursor behavior, gap semantics, min/max rollup envelopes, incident markers, and accessible summary as Trends. It additionally exposes statistic selection (`avg|min|max|last`) and links to a matching Trend profile when one exists; it MUST NOT fabricate rate, confidence, or projection semantics for an undeclared metric. When CA-05 has a stored row for the selected series, Metrics also reports the current learning level, update-count coverage/readiness and effective half-life, visibly labels the Baseline as `learning` or `ready`, includes every retained baseline value in the chart Y-domain, and overlays only the reconstructable native five-minute baseline points. Consecutive buckets may be joined as clearly distinguishable dashed segments, but gaps larger than five minutes, raw-sample timestamps and hourly interpolation MUST NOT be invented; ranges without retained baseline history show the labelled current state in text without a historical reference line.
- **UI-14** Every dashboard monitor tile MUST show one accessible health state derived from current configuration, daemon freshness, and live open/acked incidents. Fixed precedence is `config_error > stale_or_unknown > disabled > error_or_critical > notice_or_warning > clear`. States use color plus icon and visible text: grey `? unknown`/`● disabled`, red `✖ error`, yellow `▲ warning`, green `✓ clear`. Acknowledgment does not reduce severity or turn a tile green. Affected tiles show live incident count and link to incidents filtered by monitor; color never flashes or animates.
- **UI-15** `ftmon web --demo` is a separate public-demonstration mode. It
  opens only a generated, deterministic synthetic database read-only; registers
  GET/HEAD routes only (all POSTs and write helpers are absent, not merely hidden);
  accepts one explicitly configured public hostname; and displays a persistent
  "synthetic demonstration data" banner. It MUST contain no real telemetry,
  configuration, credentials, actions, MCP endpoint, or operational daemon.
  The ordinary web mode and its loopback/Host/Origin rules are unchanged.
- **UI-16** The demo dataset exercises clear/warning/error/disabled states,
  open and recovered incidents, disk and process-growth trends, chart gaps, and
  stale-daemon presentation, plus one learning and one ready baseline with
  matching five-minute history. It is regenerated from a versioned seeded scenario
  at deployment/startup, is never mutated by visitors, and may be replaced on a
  schedule without schema migration or preserving visitor state.
- **UI-17** A dashboard tile with declared MD-12 metadata MAY show one current
  primary value and its ordered labelled thresholds without affecting UI-14
  state. The read side reduces the latest raw sample per active entity using
  the declared `max|min` after applying that definition's CA-07 exemptions
  against persisted metric/attribute context, with deterministic ties by newest
  timestamp then entity ID. It omits the readout for stale dashboard state, unknown, disabled
  or configuration-error tiles, and when no active sample is newer than twice
  the monitor interval. Retained rollups and disappeared entities MUST NOT be
  used as current evidence. Selection — liveness, UI-14 precedence, active
  filtering, exemptions, aggregate choice and omission — is one shared read-side
  path that also serves MCP `get_status` (MC-01), and it yields raw values,
  units and threshold values; display formatting belongs to each consumer and
  MUST NOT replace them.
- **UI-18** A healthy Events dashboard tile MUST show the latest fresh
  `event_rate_per_min` self metric as `ingest … events/min`. This operational
  readout does not define thresholds and MUST NOT alter UI-14 health state; it
  follows the same stale, unknown, disabled, and configuration-error omission
  rules as other glance values. Where it is reported structurally it carries
  entity `ingest`, metric `event_rate_per_min`, unit `events/min`, aggregate
  `last` and no thresholds.

## 13. Resource budget (self-enforced)

- **RB-01** Daemon steady-state: ≤ 1 % of one CPU averaged over 10 m; RSS ≤ 100 MB; DB ≤ 200 MB (DM-05). Web UI and MCP processes: RSS ≤ 80 MB each. Feasibility is demonstrated, not asserted: DM-16's capacity worksheet.
- **RB-02** The daemon samples **itself** (cpu, rss, cycle duration, per-source duration, DB size, event queue depth, ring-buffer memory, event_source_last_activity_age) into the built-in `self` monitor (§7.7) with rules that open a `warning` incident on sustained budget breach — the monitor must not become the hog, and if it does, it tells on itself. (v0.46 amendment, issue #104.) "DB size" is five distinct quantities, not one: the physical database file, SQLite's logical page allocation, used pages, reusable freelist bytes, and signed headroom against DM-05's target. The physical file and the logical allocation are **not** interchangeable — WAL mode lets committed pages live outside the main file until a checkpoint — so only `used + freelist == allocated` holds, and the physical file participates in no budget identity. A metric that previously reported the physical file MUST keep reporting it, since redefining a persisted series introduces a step no database ever took. Headroom MUST be measured against that normative target rather than against whatever level a definition alarms at, so retuning a threshold cannot move the reported distance to the budget. The self source MUST also expose the counts of entities and series for which durable history is currently being written (DM-16). Unrelated budgets MUST occupy distinct incident groups: a single group shared by CPU, memory and storage lets one incident stay open while ownership moves between them, so its duration and recovery history describe nothing in particular.
- **RB-03** Tier-1 e2e tests assert cycle-time and DB-growth invariants under a synthetic 300-process, 10-events/s load (§16.4).

---

## 14. Security & privacy

- **SE-01** Attack surface by construction: no listening sockets except web UI on loopback (hardened per UI-08); MCP on stdio; definitions are data validated against MD-01; expressions cannot reach the interpreter (EX-01..07); actions are pre-existing user-created executables only (AC-03); the daemon runs as the user, never root; anything needing elevation is skipped per PL-03.
- **SE-02** Event messages and process cmdlines are untrusted strings: every sink (web UI templates, notifications, CLI, MCP JSON) escapes appropriately; the web UI sets a restrictive CSP with `default-src 'self'`, `frame-ancestors 'none'`, `form-action 'self'`, and `base-uri 'none'`.
- **SE-03** The legacy CipherSaber password feature is **not** carried forward.
  FTMON's configuration and database store no secret values; remote-channel
  credentials remain external references under SE-05. SNMP/remote checks, if
  ever added, require a separately specified secret mechanism.
- **SE-04** Privacy posture: process command lines are collected by default, truncated to 256 chars, storable off via `collect_cmdline = false` in `config.toml` (then only the executable basename is stored). Event messages truncate at 2 KB (DM-13). The DB, daemon log, and notification audit file are mode 0600 in 0700 directories (PM-06/FS). MCP and the web UI see the same data (local, single-user trust model); no redaction machinery in v1 (NG-08).
- **SE-05** Remote-channel secrets are indirect references to environment
  variables or service-account-readable credential files, never literal
  tokens/passwords in `config.toml`, CLI arguments, URLs, database rows, logs,
  errors, `doctor`, MCP, or web output. Missing references fail that channel
  closed. Error redaction removes credential values and URL user-info. A
  credential file is opened without following its final component; on Windows,
  ownership and DACL validation MUST query that same open handle rather than
  resolving the path again.
- **SE-06** A reverse proxy is the public TLS and rate-limiting boundary for
  demo mode. The backend still enforces the exact configured Host, existing CSP
  and output escaping, a maximum request-target length, and read-only routing.
  Proxy headers grant no authority. Demo mode is not an approved pattern for
  exposing an operational FTMON database.
- **SE-07** External checks are an explicit local code-execution trust boundary.
  Registration is administrator-only, execution follows EC-02, and check
  output is untrusted at every renderer/sink. FTMON MUST reject a symlink,
  non-regular/non-executable target, a target writable by group/other, or an
  executable located under FTMON data/state/runtime directories. A target may
  be owned by the service user (desktop/user-authored check) or root (hardened
  server/system plugin); documentation recommends root-owned checks on servers.
  The runner repeats target identity/type/mode validation immediately before
  every launch so registry-time validation is not a TOCTOU promise. Approval
  of a monitor cannot grant more authority than a pre-existing alias.

---

## 15. CLI

- **CL-01** Single entry point `ftmon` with subcommands: `daemon`, `mcp`, `web`, `init`, `status`, `top`, `incidents`, `incident <id>`, `ack <id>`, `events`, `query`, `monitors`, `monitor approve|enable|disable|rescan [name]`, `check [file]`, `check trust <path>`, `paths`, `baseline reset`, `doctor`, `version`. All read paths work with the daemon down (PM-01).
- **CL-02** `ftmon check` validates all definitions (or one file) and exits non-zero on any error — the successor of legacy `-c`, and the pre-commit/CI hook for definitions.
- **CL-03** Every list-producing subcommand supports `--json` (stable, documented shape shared with MCP responses) — the CLI is also scripting surface.
- **CL-04** `ftmon status` is the legacy `-z` successor: one screen, exit code 0/1/2 mapping to (all-clear / warnings / errors+) for scripting.
- **CL-05** `ftmon doctor`: runs `PRAGMA quick_check` (full `integrity_check` with `--deep`), WAL checkpoint, reports DB/table sizes, orphaned rows, cursor ages, and config errors; `ftmon doctor --backup <path>` produces a consistent snapshot via the SQLite backup API. Naive file-copy of the live WAL database is documented as unsupported (VC-03). Exit non-zero on any problem found. (v0.43 amendment, issue #74) Also reports catalog pressure against the DM-16 worksheet — **(v0.46 amendment, issue #104: the figures compared against ≤400/~270 are the persisted entity and series counts the daemon publishes, not `gone_ts IS NULL` presence counts, which DM-16 forbids using as pressure; presence counts remain reported under names that say what they count and without a budget comparison)** — separately from total retained catalog rows (which legitimately exceed those assumptions under process churn even when reap is healthy; DM-16 §9), plus MD-09 reap recency (last pass timestamp and row count). (v0.44 amendment, issue #74) Database capacity is split into file allocation, used bytes, and reusable freelist pages/bytes/percentage, with last DM-05 degradation recency. Doctor MUST NOT collapse catalog assumptions or fragmentation into a single pass/fail flag: active counts, used-page budget, and physical packing are distinct signals, and only integrity/orphan/config failures affect doctor health. (v0.46 amendment, issue #104.) The same capacity split doctor already makes MUST hold across `ftmon status`, MCP, and the dashboard, so an operator cannot reach a different conclusion about capacity depending on which surface they consult. Doctor MUST distinguish the physical database file from SQLite's logical page allocation: in WAL mode the two differ until a checkpoint, so only the logical allocation participates in the used/freelist identity.
- **CL-06** `ftmon paths` prints the resolved filesystem layout an author or operator needs — config dir, monitors dir, drafts dir, actions dir, check registry file, data dir, database file, state dir, log and notifications files, runtime dir and lock file — honoring the `FTMON_*` overrides, with `--json` (CL-03). Works with the daemon down (PM-01); prints paths only, never file contents.
- **CL-07** `ftmon monitor rescan` requests an immediate PM-11 reload from the running daemon instead of waiting out the PM-04 window, using the daemon pid recorded in the PM-02 lock file. When no daemon is running (lock not held), it exits non-zero with a clear message rather than signalling a stale pid.
- **CL-08** `ftmon check trust <path>` evaluates the shared executable trust policy (EC-01/SE-07 — the same predicate the registry and runner enforce) and reports **every** failed condition by name (absolute path, symlink-free, regular file, trusted owner, no group/other write, executable), exiting 0 when trusted and 1 otherwise. It never executes the candidate.

---

## 16. Testing requirements

### 16.1 Principles

- **TS-01** Every `XX-nn` requirement in this document maps to ≥ 1 test carrying the ID in its name or docstring; `tests/traceability.py` fails CI if a requirement (marked `testable: yes` in the requirements index the design doc will generate) has no test.
- **TS-02** Implementation work packages will be delivered tests-first where feasible: interfaces + tests frozen before implementation is requested from implementing models.
- **TS-19** Document-version coherence is machine-checked: this document's
  `Status:` header version MUST equal the newest §21 changelog entry, the
  newest entry MUST be the highest version in §21, and DESIGN.md's
  "Companion to `SPEC.md` vX.Y" reference MUST match. Enforced by a unit
  test, because the header drifted from the changelog twice during
  v0.10–v0.14 — both times by AI implementers who appended a changelog entry
  and forgot the header ("lint rules are enforced as tests").

### 16.2 Determinism substrate

- **TS-03** All time access goes through an injected `Clock` (wall-now, monotonic-now, sleep-until-tick); production uses system clocks, tests use `FakeClock` advanced explicitly (including divergent wall/monotonic advancement to test SA-07). **No component may call `time.time`/`time.monotonic`/`datetime.now` directly** (enforced by a lint rule / grep test).
- **TS-04** `Sampler` and `EventSource` have fixture implementations driven by **scenario files** (JSONL: at relative time T, entity E has metrics {...} / event {...}) — the same format is used by unit, e2e, and manual-repro tooling. The scenario library ships named cases: `steady`, `firefox-leak-2mb-min`, `cpu-hog-spike-vs-sustained`, `disk-filling-linear`, `disk-ladder-updown` (escalate → downgrade → clear), `oom-event-burst`, `event-episode-quiet-clear`, `service-flap`, `entity-vanishes-mid-incident`, `counter-reset`, `suspend-resume-gap`, `proc-churn-300`.

### 16.3 Unit test surface (highlights, not exhaustive)

- Expression language: parse whitelist (every forbidden node type rejected — enumerated test, incl. keyword args per EX-05), name resolution order incl. severity constants (EX-02), the EX-06 truth table verbatim as a table-driven test, property-based tests (hypothesis) asserting no exception ever escapes eval and EX-03 purity; regex limits (EX-07).
- Calc functions: golden numeric tests per function including edge cases (empty window, single point, NaN-at-ingest rejection per DM-01, counter reset for `rate`, `monot` boundaries, division-by-zero → `None`).
- Baseline: CA-05 update formula golden tests (known rollup sequence → exact expected value), coverage gate (updates, not elapsed time), reset.
- Incident state machine (IN-06): exhaustive table-driven transitions including ladder escalate/silent-downgrade/all-clear (IN-03), flap (IN-05), backoff arithmetic across restarts and wall-clock steps (IN-02, SA-07), `None` handling (IN-01), entity-gone (CA-08/IN-07), episode open/refresh/cooldown/quiet-clear/reopen (IN-08).
- Validator: a corpus of invalid TOML definitions each asserting the specific error message (MD-01, MD-03, MD-04, MD-08 cycle detection); all eight built-ins pass (MD-07).
- Event pipeline: per-source severity mapping tables (DM-08) with captured real samples as fixtures; msg_hash normalization vectors (§7.7.3); cursor resume/replay (DM-15); storm collapse (DM-10); queue overflow (SA-08).
- Retention/rollup: rollup math golden tests; degradation order (DM-05); attrs/history caps (DM-03/DM-13).
- Outbox: NO-04 crash window plus DM-18/NO-07 independent channel retry and
  terminal-failure rules.

### 16.4 Tier-1 e2e (CI, deterministic)

- **TS-05** Harness: launch the real `ftmon daemon` binary with `--clock=controlled` (FakeClock stepped over a control socket/file), `--fixtures <scenario>`, temp XDG dirs, `file` notifier. Assertions run against the DB, `notifications.jsonl`, and CLI/MCP/web responses. Scenario cases: each built-in monitor's happy-path fire-and-clear; ladder escalate → downgrade → clear; episode lifecycle; backoff timing; ack; quiet hours digest; config hot-reload incl. invalid file (PM-04); draft → approve flow incl. approval race (PM-06); budget invariants under `proc-churn-300` (RB-03); suspend/resume gap (SA-07); daemon kill -9 mid-delivery → restart → **at most one duplicate per in-flight channel delivery** (NO-04), no lost committed notifications, cursor-correct event resume (DM-15), no DB corruption (WAL).
- **TS-06** MCP is tested end-to-end by driving `ftmon mcp` over stdio with recorded tool-call sequences (including a scripted "AI authors a monitor with two validation errors then a correct one" flow exercising MC-03/MC-04, and fetches of every packaged resource per MC-05).
- **TS-07** Web UI: HTTP-level tests for every page and POST (UI-03) against a fixture-populated DB; HTML assertions on data presence and escaping (SE-02); exact UI-04 refresh cadences; UI-08 hardening tests (bad Host → 400, missing/foreign Origin on POST → rejected, exact operational/demo response headers, no CORS); UI-09 checks that severity markup carries text labels.

### 16.5 Tier-2 (opt-in, real system)

- **TS-08** Marked `@pytest.mark.realsystem`, excluded from CI default: daemon starts under systemd user unit, samples real psutil ≥ 3 cycles, journald reader ingests a `logger`-injected marker event and resumes across a daemon restart via cursor, notify-send fires (assert via `notifications.jsonl` + non-fatal check of desktop), CLI/status/web respond, `ftmon doctor` clean, teardown cleans state.
- **TS-09** Historical visualization tests MUST cover signed growth and cleanup, projection qualification/suppression, sparse-data gaps, rollup `avg/last` selection and min/max envelopes, incident overlays, the 2 000-point cap, hostile labels, shareable URL state, and textual alternatives. Tier-1 uses `disk-filling-linear` and `disk-ladder-updown`; browser-library behavior is tested at the HTTP/data-contract boundary rather than by pixel snapshots.

---

- **TS-10** Generic trend tests MUST cover profile schema and cross-reference errors, optional-panel `null` semantics, disk compatibility, leak value/rate/confidence history, profile-aware thresholds and incident groups, contextual links, `/disks` redirect preservation, and one real-daemon-to-HTTP leak journey. Tests assert data and accessibility contracts, not chart pixels.
- **TS-11** Metrics visualization tests MUST cover the `/api/series` contract, catalog selectors, all rollup statistics, aligned min/max envelopes, missing-data gaps, incident filtering, unit discovery/fallback, the 2 000-point cap, hostile labels, accessible summary, matching-Trend links, absence of invented panels, baseline learning/readiness labels, baseline-inclusive Y-domain inputs, exact inverse reconstruction, range-relative truncation, and explicit contiguous native-five-minute run segmentation without spanning gaps. Browser-library behavior remains tested at the HTTP/data boundary rather than by pixel snapshots.
- **TS-12** Dashboard tile tests MUST cover clear, warning, error/critical, acknowledged, disabled, stale/no-data, and configuration-error states; precedence conflicts; incident counts and filtered links; escaping; icon+text accessibility; absence of flashing/animation dependence; and declared glance aggregation, active/fresh sample filtering, threshold rendering, omission and state independence.
- **TS-13** Notification tests cover configuration validation, severity fan-out,
  quiet-hours digests, independent channel success/failure, exact retry classes
  and schedule, restart recovery, the kill-after-send duplicate bound per
  channel, secret/error redaction, TLS/timeout policy, ntfy request shape,
  webhook schema, and SMTP 4xx/5xx behavior using local fakes only.
- **TS-14** Demo tests build the seeded database twice for logically equivalent
  results, exercise every UI-16 state, enumerate the route table to prove no
  writes are registered, reject localhost/foreign Hosts in public demo
  configuration, reject attempts to open a real writable DB, and crawl all
  pages without external asset requests or sensitive fixture strings.
- **TS-15** External-check tests use local fake executables only and cover exact
  argv/no-shell/minimal-env behavior, registry authority and reload failure,
  path/ownership/mode rejection, exit codes 0–3 and out-of-range mapping,
  process-group timeout, output/control/size caps, Nagios quoting and first-line
  perfdata, JSON schema strictness, missing/unknown/duplicate/malformed/UOM
  values, scaling and counter kinds, declared-name validation, shared-alias
  single execution, source-budget fairness, unknown-versus-missing semantics,
  self-metrics, doctor redaction, and one controlled-clock journey from mapped
  plugin metric through derived growth/confidence to incident, Metrics, Trend,
  CLI and MCP. No CI test contacts a network service or requires Nagios to be
  installed.
- **TS-16** Extra-monitor tests discover recipe directories, validate their
  manifest/article/configuration contract, parse all declared fixtures and run
  any repository-maintained script tests. Default CI remains offline and
  unprivileged; network, hardware and installed-plugin checks are opt-in only.

### 16.6 Release readiness gates (pre-v1.0)

Deterministic CI proves the logic; it cannot prove longevity. The entire v2
implementation landed in days, so the slow failures a monitor exists to catch
(leaks, unbounded growth, retry storms, cursor drift) have never had time to
occur. These gates convert the resource-budget and durability *claims*
(RB-01/RB-02, DM-05, NO-04) into recorded evidence before the first stable tag.

- **TS-17** Before the v1.0 tag, FTMON MUST complete a recorded **soak**: at
  least 30 consecutive days of the real daemon on at least two real hosts, one
  `desktop` and one `server` profile, with (a) no unexplained daemon restarts;
  (b) RB-01 budgets held, verified from the `self` monitor's own stored
  history, not external observation; (c) DB size within DM-05 after retention
  has cycled; (d) the notification outbox draining (no unbounded
  `notification_deliveries` growth); (e) zero unexplained `self`-monitor
  incidents; and (f) a clean `ftmon doctor` at the end. The evidence (exported
  self-metric series, doctor output, incident list) is attached to the release
  notes. A soak restarts its clock only for daemon-crash fixes, not for
  unrelated commits.
- **TS-18** `tests/traceability_pending.json` MUST be empty at the v1.0 tag,
  and it may only shrink (the existing ratchet). Burn-down order is
  risk-first: `SE-*` IDs first (a security requirement may not remain untested
  across more than one further milestone), then `UI-*`/`PL-*`, then the rest.
  A pending ID that proves untestable is resolved by amending this document
  (mark exempt or retire with rationale), never by silent deletion.
- **TS-19** Exchange tests build twice for byte-identical output and cover
  metadata bounds, stable paths, complete catalogue/detail/search output,
  no-JavaScript navigation, HTML/script/URL escaping, symlink and traversal
  rejection, inert command examples, broken local links and workflow trigger,
  permission and deploy-job boundaries. Tests never contact the network or
  execute a recipe command.
- **TS-20** Shared-skill tests discover `.ai/skills/`, validate AS-05 without
  installing a vendor tool, and assert that the extra-monitor skill names both
  protocols, evidence states, privilege/secret limits, repository precedence,
  required recipe artifacts, Exchange generation, targeted/full tests and the
  no-push boundary. The test reads skill files as inert data and runs none of
  their example commands.

## 17. Documentation deliverables (v1)

- **DO-01** `docs/definitions.md`: complete monitor-definition reference (schema, every function with examples, the EX-06 truth table, authoring traps for weaker models, CI-validated cookbook recipes, MCP `query_metrics.filter_expr` attribute-only guidance, and cookbook entries such as "watch this log pattern" / "alert when X grows"). Written to be pasted into an AI context and exposed as the MCP resource (MC-05) — the primary consumer is `define_monitor` authors, human or model.
- **DO-02** `docs/install.md`: uv install, `ftmon init`, systemd unit, Windows
  per-user MSI (silent install, upgrade, repair, uninstall, PATH), Task
  Scheduler lifecycle (`Install-FTMONTasks.ps1`), macOS launchd, MCP client
  registration snippet (Claude Code/Desktop), web UI.
- **DO-03** Man-page-style `--help` for every CLI subcommand.
- **DO-04** `docs/manual.md`: the user manual — installation, concepts (monitors, rules, incidents, baselines, episodes), daily use (CLI, web UI, notifications), tuning thresholds, writing definitions (pointer to DO-01), AI/MCP setup, troubleshooting (`ftmon doctor`, config errors, budget breaches). Grows one chapter per milestone; a milestone's user-visible feature is not done until its manual chapter exists.
- **DO-05** Code documentation follows `CONTRIBUTING.md`: module/function docstrings record rationale and cite requirement IDs; comments explain *why* (constraints, trade-offs), never mechanics; test docstrings carry bracketed requirement tags (feeds TS-01).
- **DO-06** Documentation includes a single-server installation and hardening
  guide, channel-specific privacy/credential/retry behavior, SSH-tunneled web
  access, and a reproducible `demo.ftmon.org` deployment guide with DNS, reverse
  proxy TLS, synthetic reset, rate limits, updates, backups-not-required, and
  an explicit warning never to substitute a real operational database.
- **DO-07** Documentation includes the external-check trust model, registry and
  definition references, an FTMON JSON check authoring guide, Nagios reuse and
  licensing caveats, credential-file guidance, troubleshooting for unknown and
  missing results, and worked HTTP/TLS examples whose declared performance data
  appears in Metrics and Trends. Product-facing documentation explains the
  value accurately: reuse a mature check ecosystem while FTMON adds bounded
  local history, confirmation, incidents, notifications and trend analysis;
  it MUST label the capability planned until M9 ships.
- **DO-08** `extra-monitors/README.md` explains recipe confidence labels,
  contribution structure and offline validation; each recipe documents why,
  installation, configuration, testing, security/permissions, upstream and
  licence.
- **DO-09** Documentation-drift audit. TS-01 traceability covers SPEC↔tests
  only; nothing machine-checks `docs/manual.md`, `docs/install.md`,
  `docs/definitions.md`, or `README.md` against behavior. Each milestone that
  changes user-visible behavior therefore ends with a recorded audit pass:
  every documented command is executed as written, every documented default is
  compared to code, and every **external claim** (repository clone URL, live
  demo URL, published package names) is verified to resolve. Review artifacts
  (AI or human) are not documentation: they live outside `docs/` (or in a
  clearly labelled `docs/history/`), and `CLAUDE.md`/`AGENTS.md` are checked
  for staleness as part of the audit — a repo-guidance file that describes a
  previous architecture is a defect.
- **DO-10** Documentation explains Exchange contribution, local preview,
  generated-file policy, GitHub Pages environment and custom-domain DNS setup,
  deployment verification and rollback. It states that catalogue publication
  is compatibility evidence, not endorsement or a security audit.
- **DO-11** Contributor documentation explains the shared-skill trust model,
  canonical layout, invocation and optional Codex/Claude installation. It warns
  users to audit a skill like code, records that native discovery is
  product-specific, and keeps ordinary manual contribution instructions usable
  when no skill-aware agent is present.

---

## 18. Versioning & compatibility

- **VC-01** SQLite schema carries `PRAGMA user_version`; the daemon migrates forward automatically with a pre-migration backup taken via the SQLite backup API (`ftmon.db.bak-<ver>`, keep 1); processes refuse to run against a *newer* schema than they understand.
- **VC-02** Monitor definition files carry top-level `schema = 1`; the validator accepts only known versions.
- **VC-03** The only supported backup mechanisms are `ftmon doctor --backup` and VC-01's automatic pre-migration backup (both use the SQLite backup API). Copying the live DB file is unsupported and documented as such.

---

## 19. Open questions

| ID | Question | Status |
|---|---|---|
| OPEN-1 | Default thresholds/windows in §7.7 | **RESOLVED v0.2**: owner accepts the proposals as shipping defaults; they remain tunable in the installed TOML files and will be revisited against fixture data and real-system observation during M2 (no doc change needed to tune) |
| OPEN-2 | Per-process connection attribution | **RESOLVED v0.2**: deferred (NG-06) |
| OPEN-3 | Per-monitor quiet hours | **RESOLVED v0.2**: global-only in v1 (NO-03) |
| OPEN-4 | Docs as MCP resource | **RESOLVED v0.2**: DO-01 exposed, SPEC not (MC-05) |
| OPEN-5 | Web freshness + chart lib | **RESOLVED v0.2**: 5 s polling (UI-04); smallest vendorable chart lib chosen in design doc (UI-06) |
| OPEN-6 | Daemon/web coupling | **RESOLVED v0.2**: fully separate services (§3, UI-07) |
| OPEN-7 | License | **RESOLVED v0.8**: this repository is MIT; the separate original SourceForge project remains GPLv2 (§3) |
| OPEN-8 | Container monitoring (Docker/Podman): a v1.1 core source behind the PL-01 sampler seam, or an extra-monitor recipe? Core would give per-container entities, restart/OOM tracking and state-change events — the largest capability gap versus Glances/Netdata for the server audience — but adds a socket-permission surface and a new dependency to the frozen daemon. A recipe ships today with no core change but cannot observe state transitions between check runs. | **RESOLVED v0.21**: FTMON 2.0 uses an external `check_docker` recipe and adds no core container source. Access to a rootful container socket, directly or through container-engine group membership, is outside the shipped SE-01 posture. The acceptable recipe path is a rootless socket already owned by the same user running FTMON; the recipe never grants socket access or weakens the service unit. Evidence from the unfrozen workstation canary during the TS-17 calendar window may justify a separately specified post-2.0 source, but does not reopen this decision automatically. Podman compatibility requires its own evidence. |

---

## 20. Delivery milestones

Implementation lands in stages; each stage is independently usable, ships the §16 determinism substrate from day one, and must leave `main` green.

| Milestone | Contents | Usable as |
|---|---|---|
| **M1** | Clock/paths/DB substrate, expression language (EX-*), validator (MD-*), `process`/`disk`/`system` samplers, sampling pipeline (SA-*), CLI `check`/`status`/`query`, `file` notifier | "sample & query" tool |
| **M2** | Incident engine (IN-*), outbox + desktop notifications (NO-*), retention/rollups/baselines (DM-04..06, CA-05), built-in defs `leak`/`hog`/`disk`/`load`/`self`, fixtures + tier-1 harness | the actual desktop monitor |
| **M3** | Event pipeline (journald, cursor, storm/overflow), `events` monitor, `service`/`net` samplers + defs | full seven-monitor scope |
| **M4** | MCP server (MC-*), draft/approve flow (PM-06/MD-05) | AI integration |
| **M5** | Web UI (UI-*) | human dashboard |
| **M6** | Actions (AC-*), `doctor` (CL-05), tier-2 suite, docs (DO-*), packaging polish | v1.0 |
| **M7** | Historical disk trends (DM-17, CA-09, UI-10/11, TS-09) | honest capacity forecasting |
| **M7.1** | Generic trend profiles (MD-10, CA-10, UI-12, TS-10) | reusable growth investigation |
| **M7.2** | Shared Metrics/Trends chart foundation (UI-13, TS-11) | consistent single-series diagnostics |
| **M7.3** | Accessible legacy-style dashboard health tiles and declared current-value glances (MD-12, UI-14/17, TS-12) | at-a-glance operational status |
| **M7.4** | Baseline visibility in Metrics, MCP and a read-only index (CA-05, MC-07, UI-02/13, TS-11) | learned normals operators and agents can inspect and trust |
| **M8** | Server profile, per-channel outbox, ntfy/webhook/SMTP, server service/docs (PM-08/09, DM-18, NO-05..10, SE-05, TS-13, DO-06) | lightweight single-server monitor |
| **M8.1** | Synthetic read-only public demo mode and deployment (UI-15/16, SE-06, TS-14, DO-06) | safe `demo.ftmon.org` experience |
| **M9** | Administrator check registry, external subprocess source, FTMON JSON and Nagios adapters, declared perfdata history/Trends (EC-*, MD-11, SE-07, TS-15, DO-07) | bring-your-own checks without a monitoring stack |
| **M9.1** | Curated `extra-monitors/` recipe contract, offline validator, HTTP/TLS, SMART/NVMe and native JSON examples (XR-*, TS-16, DO-08) | a tested integration cookbook without vendoring an ecosystem |
| **M9.2** | Deterministic safe Exchange generator, searchable static catalogue, Pages/custom-domain deployment and publication tests (XR-06..10, TS-19, DO-10) | discoverable curated integrations at `exchange.ftmon.org` |
| **M9.3** | Tool-neutral shared-skill contract, `ftmon-add-extra-monitor`, vendor installation guidance and structural tests (AS-*, TS-20, DO-11) | safer AI-assisted catalogue contributions without vendor lock-in |
| **M10** | Release readiness: 30-day two-host soak with recorded evidence (TS-17), traceability pending burn-down to zero, security IDs first (TS-18), documentation-drift and external-claim audit (DO-09), repo hygiene (review artifacts out of `docs/`, root kept to living documents), dependency-deprecation sweep | a v1.0 whose operational claims are evidence, not assertion |

---

## 21. Changelog & review disposition

**v0.48 (2026-08-09)** — closes issue #102. A live desktop had been in
permanent DM-05 degradation: retention pruned on roughly a quarter of all
passes, indefinitely, and the cost was paid in a guarantee rather than a
failure. DM-04 promises 48 h of raw samples; that host had 28.8 h and falling,
because degradation's first step trims raw data and nothing said so.

The cause was a retention *shape* problem, not a retention *speed* problem.
`rollup5m` was 97 MB of a 200 MB budget, 87.9% of it process-sourced and 40%
belonging to entities that no longer existed. The hourly tier has split
durable from process retention since v0.3 for exactly this reason; the
5-minute tier — twelve times denser, and the largest table in the database —
never received the same treatment. It does now, at 7 d for process series.

Degradation also becomes observable. `db_degradations` was counted and never
projected, so the rate was invisible; it is now a self metric, alongside a
`db_degrading` gauge that rules window with `avg()` rather than the daemon
hard-coding what "persistent" means. Per-pass self-events are rate-limited and
say how many passes they cover, because ~15 an hour is a stream, not a signal.

Deliberately deferred: pruning 5-minute rollups by entity liveness rather than
age alone. It would remove more, sooner, but needs a three-table join on the
largest table every pass — plausibly trading total size for the per-pass cost
issue #107 exists to reduce. The age split is cheap and mirrors a query
already in this module, so it lands first and the liveness tail is decided on
re-measurement rather than assumption.

**v0.47 (2026-08-08)** — ships issues #94 and #95: Windows Task Scheduler
helpers (`Install-FTMONTasks.ps1` / `Invoke-FTMONTask.ps1`) as the PL-01
service-wrapper seam, and a self-contained per-user x64 MSI wrapping a
PyInstaller onedir payload. DO-02 now requires MSI silent install / upgrade /
repair / uninstall guidance, Task Scheduler lifecycle documentation, and keeps
PyPI/`uv` as a supported alternative. The MSI never creates Scheduled Tasks or
runs `ftmon init`; configuration and state stay in platformdirs locations.
Chocolatey (#96) remains out of scope but is expected to consume the immutable
ZIP produced here.

**v0.46 (2026-08-06)** — closes issue #104, the first of four workstreams split
out of #97. A live desktop held an endlessly flapping `self/budget` incident
while the database was, by the definition DM-05 actually gives, healthy: the
rule compared file allocation at 209,743,872 bytes against a 209,715,200 byte
threshold while used pages stood at 209,100,800 — seven pages over, on the
wrong side of a quantity that had never been breached, with a fully reclaimed
freelist. Two independent defects had to be fixed together, because correcting
either alone leaves the alarm useless. The rule measured the wrong quantity;
and `db_budget_mb` was simultaneously the DM-05 target and the alarm level, so
even measured correctly it would oscillate, since retention's whole purpose is
to hold the footprint just under that target. The alarm now sits above the
target and means *retention is failing*, not *retention is working*.

Two further corrections came out of the same investigation. Catalog pressure
was reported as entities with `gone_ts IS NULL`, which under SA-05 track-all
counts every sampled process rather than those actually being persisted — it
read 2,246 against a 400 budget on a host whose persisted set was far smaller,
so the headline "5.6× over capacity" was an artefact of the measure, not a
finding. And CPU, memory and storage rules shared one incident group, letting a
single incident stay open while ownership moved between them.

Deliberately not done: `db_bytes` was **not** aliased to used bytes. Aliasing
would have made every installed definition DM-05-correct on upgrade at no
apparent cost, but the persisted `db_bytes` series holds historical points
meaning the database file, and redefining it would put a step into every chart
spanning the upgrade that no database ever took.

Review caught that the first attempt did not actually deliver that promise. It
had moved `db_bytes` from `stat()` to `page_count * page_size`, which are not
the same in WAL mode — the main file lags logical allocation between
checkpoints by up to the auto-checkpoint threshold, measured at ~1 MB on a live
database. The two are now separate metrics: `db_bytes` is the physical file, as
it always was, and `db_allocated_bytes` is SQLite's logical size. Only
`used + freelist == allocated` holds. Review also caught that the DM-16
amendment had been made without updating doctor, leaving the spec contradicting
itself and the implementation still reporting the presence counts DM-16
forbids; entity *and* series pressure now come from what the pipeline persists,
with presence counts retained under names that say what they count. This issue exists because a
budget signal silently measured the wrong thing; the cure must not repeat that
in the opposite direction. The consequence — installs keep alarming on file
bytes until their definition is updated, since FS-02 forbids overwriting user
config — is covered by a loader warning rather than left silent.

**v0.45 (2026-08-06)** — closes issue #98. A live desktop kept sampling across a
sleep/resume cycle while the background notification dispatcher was dead: six
file and six desktop deliveries sat `pending` with `attempt_count=0` for nearly
fourteen hours, and `doctor` still called both channels `ready` because it only
read configuration. The cause is structural rather than platform-specific — the
worker thread had no recovery boundary around its store access, so a SQLite
lock or connection fault escaped a thread whose exceptions no one observes.
**PM-12** gives the dispatcher the same survive-the-lock contract PM-10 already
gives the tick loop, and makes its liveness durable so the failure is
observable without recursively notifying about the notifier. **NO-10** gains
the read side: doctor reports a quiet-safe backlog split and dispatcher state,
and MUST NOT call delivery healthy when the worker is dead or claimable debt is
overdue. Deliberately deferred: automatic respawn of a dead worker — durable
state plus a failing doctor is this release's observability contract, and
in-thread recovery already covers the transient paths that soak evidence shows.
Quiet-held pending rows are explicitly *not* overdue debt, so overnight quiet
hours cannot masquerade as a stuck outbox.

**v0.44 (2026-08-04)** — completes issue #74 without adding a live full-
`VACUUM` worker. PR #89's v0.43 catalog reap fixed the root unbounded-metadata
defect; deleted pages immediately become reusable and stop counting against
DM-05's existing used-page calculation, while the bounded
`incremental_vacuum(200)` already run after every retention pass progressively
returns them to the filesystem. DM-05 now names that used-page formula and
removes DESIGN's unimplemented weekly full-`VACUUM` promise: rebuilding a
bounded 200 MB store under an exclusive SQLite write lock would require new
tick, retention, outbox, thread, retry, shutdown, and cross-platform recovery
semantics for tighter packing rather than better monitoring. CL-05 doctor now
reports file allocation, used bytes, freelist pages/bytes/percentage, and last
lossy-degradation recency alongside v0.43's catalog/reap fields. Full compaction
remains explicit offline operator maintenance; no live command or worker is
introduced.

**v0.43 (2026-08-04)** — closes issue #74's catalog-lifecycle gap: on
long-running installs, dead process identities (`entities`, `series`,
`baselines`) never aged out even after their observations did, so the
DM-05 200 MB budget was partly consumed by metadata that could not shrink —
observed on the maintainer workstation at ~248k `entities` rows (~246k
`gone`) against the ≤400 DM-16 worksheet assumption. MD-09's "retained until
their data ages out" is now concrete: a `gone` entity reaps (with its
series/baselines) once none of its series retain a samples/rollup5m/rollup1h
row and no non-cleared incident references it; watchlist/synthetic entities
are structurally exempt (CA-08 never marks them gone) with no schema change.
Reap runs unconditionally alongside normal pruning, in bounded
catalog-rows-visited batches (a cursor over the entity catalog, same
catch-up shape as rollup rollforward) — it never removes data still inside
its DM-04 window, so it is cleanup, not a DM-05 degradation step. DM-16's §9
worksheet (DESIGN.md) now distinguishes active (concurrently persisted)
catalog from total retained catalog, since the latter legitimately exceeds
the worksheet's ≤400/~270 assumptions under process churn even when reap is
healthy — CL-05 doctor reports both, plus MD-09 reap recency, without
collapsing them into one pass/fail signal. The live compaction policy and
remaining capacity diagnostics are resolved by the v0.44 follow-up.

**v0.42 (2026-08-04)** — MCP `get_status` exposes the dashboard's primary
readouts as `glances` (issue #64): one shared read-side module now owns
liveness, UI-14 precedence, UI-17 active/exemption/aggregate selection and
UI-18's fixed ingest readout, so web and MCP cannot drift. Records are
raw-first (value, unit and threshold values as stored; formatting stays with
each consumer), bounded at 64 ordered by monitor name, and the truncation
metadata (`glances_returned`, `glances_matched`, `glances_truncated`,
`limits.max_glances`) is present on every response — there was no real
"≤30 tiles" invariant to lean on. UI-04's staleness boundary is now one
predicate (unknown age or age > 15 s is stale; exactly 15 s is alive); MCP
previously treated 15 s as stale and an unknown age as neither alive nor stale.
MD-12's TOML `aggregate` is unchanged at `max|min` — `last` exists only as the
response label for UI-18. One semantic change to an existing field:
`get_status` now loads with the dashboard's check-registry authority, so an
external monitor whose alias is missing or whose registry is invalid moves out
of `monitors[]` as a loaded entry and appears as `state = "config_error"`,
matching the web UI (issue #64 requires web/MCP parity). That is distinct from
daemon reload behavior: a malformed replacement registry leaves the last valid
registry active (EC-06). No new MCP tools or resources (MC-05 remains three);
`get_status` parameters remain frozen and empty.

**v0.41 (2026-08-03)** — MCP authoring discoverability (issue #62): DO-01
gains authoring traps, CI-validated marked cookbook recipes, and attribute-only
`filter_expr` guidance; check-authoring/MCP tool descriptions steer to the
ftmon-json exit-0 contract; EC-06 scopes exit-as-severity evidence to Nagios
(EC-03) without altering EC-08/EC-09; EC-10 states that ftmon-json process exit
MUST be `0` and nonzero exits discard JSON as `exit_status` unknown. No new
MCP tools or resources (MC-05 remains three); `query_metrics` response fields
unchanged from v0.40.

**v0.40 (2026-08-03)** — MCP `query_metrics` gains a total response bound
(≤50 entities, ≤10 000 points) with deterministic truncation metadata, and
empty `series` responses report `empty_reason` plus `available_metrics`
(declared ∪ persisted history) so agents can distinguish unknown metric,
quiet window, and filter wipeout (DM-06/MC-01, issue #61). Resolution follows
DM-06 even when empty; quiet known metrics omit empty-point shells; query
work lists observed candidates first and uses a capped-count preflight so
discarded entities are not point-materialized. Parameter schemas unchanged.

**v0.39 (2026-08-03)** — process sampler emits per-PID `fd_limit_soft` from
`RLIMIT_NOFILE` soft limit where available (SA-04/PL-05, issue #60). The
metric is omitted on `AccessDenied`, unsupported platforms (missing `rlimit`
or `RLIMIT_NOFILE`), zero, or infinite limits so fd-utilization expressions
never see a silent bogus ratio. The value is re-read each sample (not
lifetime-cached on the Process handle) because a process may `setrlimit()`
after startup. Docs list the new process metric; no package `fds.toml` is
added — operator monitors re-key `fd_pct` to `num_fds / fd_limit_soft` after
upgrade.

**v0.38 (2026-08-02)** — closes the second PR #80 Windows hardening review.
External checks retain only the small set of host-root/temp variables required
by ordinary Windows runtimes while arbitrary parent environment remains
scrubbed (EC-02). Managed directory DACL writes now reject final-component
reparse points and operate on a verified handle, preventing an unelevated NTFS
junction from redirecting initialization or atomic writes (PM-06/FS-02).
Secret credential owner/DACL validation likewise stays anchored to the
already-open no-follow handle (SE-04/SE-05). Native tests cover junctions,
runtime environment, NULL-DACL handle checks, and descendant process reaping;
DM-15 storage coverage injects a cursor-write failure to prove events and their
checkpoint roll back atomically.

**v0.37 (2026-08-02)** — resolves PR #80's cross-platform review blockers.
Generic `desktop`/`server` initialization now selects the Windows or macOS
calibrated tree on those hosts, while the generic builtin tree is Linux-only
(PM-08/PL-01). Windows Event Log startup snapshots a filtered per-channel tail
bookmark, or an explicit oldest-record boundary for an empty channel, before
subscription; partial drains can therefore never omit an undrained sibling or
newly added channel from the durable composite checkpoint (DM-15/DM-19).
Windows NULL/absent DACLs fail closed at every shared trust caller, matching
their real world-accessible semantics (EC-01/SE-04/SE-07), and external-check
termination remains bounded when `taskkill` fails (EC-02).

**v0.36 (2026-08-01)** — integrates Windows Event Log channel selection and
per-channel subscribe-time filtering (MD-13, DM-19, SA-10) from
feature/windows-support into this lineage. Grew out of analyzing two days
of real desktop incident data: the only channels available (hardcoded
System/Application) miss the events people actually want for
security-relevant monitoring, and Windows' Event Log query engine (the
XPath-subset language shared by `EvtQuery`/`EvtSubscribe`/`wevtutil`/
`Get-WinEvent` — confirmed *not* WEC/WEF-specific, so it works standalone
on a single local host) already supports the filtering needed to make a
high-volume channel like Security usable without flooding the bounded
event queue. `events.toml`'s `[source_options]` can now declare `channels`
(`{path, query}` tables); channels are unioned across every loaded event
monitor since there is one shared `EvtSubscribe` pass for the whole daemon,
not one per monitor, and a conflicting query for the same channel path
keeps the first-seen one and reports the conflict rather than silently
picking one. Channel/query configuration is read once at the event
reader's first start — an explicit, documented exception to PM-04's
hot-reload guarantee, not an oversight; a monitor loaded after the reader
already started that requests a not-yet-subscribed channel gets a clear
self-event saying a restart is needed, rather than sitting there silently
never receiving anything. Landed alongside a correctness fix this work
would otherwise have inherited and amplified: `EvtSubscribe` failures
(unknown channel, malformed query) previously aborted subscription setup
for *every* channel, not just the bad one — SA-10 isolates them per
channel and reports each once per daemon lifetime. Also closes a real
doc/code gap unrelated to Windows specifically: DM-09's `store_min_severity`
override has been documented since it was written but `schema.py` had no
`[source_options]` branch for `source = "events"` to actually accept it —
MD-13 fixes that for both platforms. Landed alongside a renumbering this
integration found necessary: v0.35's event-coalescing requirement had been
filed as DM-18, colliding with the pre-existing DM-18 (notification-delivery
fan-out); it is DM-20 as of this merge, since DM-19 was independently taken
by this same integration's channel-selection requirement above.

**v0.35 (2026-08-01)** — coalesces contiguous, origin-aware duplicate event
runs before queue admission without weakening cursor order, while preserving
raw episode occurrence totals in aggregate attrs. New self metrics expose raw
received/repeated counts and rolling events/min, and the Events dashboard tile
shows the fresh ingest rate without changing health policy (DM-20, UI-18;
issue #78).

**v0.34 (2026-08-01)** — hardens the standard macOS events monitor after a
live unrestricted reader dropped more than 27,000 records within minutes.
Replay and streaming now share a source-side operational allowlist for
third-party executable faults and explicit kernel storage-integrity
messages; the monitor is enabled by default with a canonical `critical` store
threshold. Downstream rules remain a semantic boundary, not queue protection
(SA-08, DM-09, PM-08).

**v0.33 (2026-07-26)** — ships the macOS implementation validated by the
v0.30 spike: unified-log replay/stream dedup checkpoints with observable
retention gaps (DM-15), best-effort `osascript` desktop delivery (NO-02), a
LaunchAgent service template preserving SIGHUP (PM-11), and conservative
Darwin-specific init profiles with behavior-tested rule bodies (PM-08).

**v0.32 (2026-07-25)** — replaces the `windowsdesktop` placeholder profile
(v0.31) with `windesktop`/`winserver` (PM-08), sharing one Windows monitor
tree built from checking every builtin rule's *body* against real data
from a live Windows daemon, not just whether the sampler crashes. Two
concrete, permanent gaps were found and fixed: `disk`'s `inodes` rule
group (NTFS has no POSIX inode concept, always absent) and `events`'
`provider == "kernel"` OOM rule (a journald-only convention no Windows
Event Log provider uses) are both dropped from the Windows tree rather
than left silently dead. `load`'s PSI-gated rules were evaluated the same
way and deliberately left unchanged: §7.7.5 already specifies that a
PSI-less system gets an absent readout, not a substitute metric, and
Windows is exactly that case — substituting `cpu_pct` thresholds would
have both contradicted that existing decision and invented an unvalidated
signal (PSI's stall-time measurement is not the same claim as raw CPU%).
`hog`/`leak`/`net`/`self` needed no changes, confirmed against real
process/connection/memory data from the same live daemon. `service` is
reworded (Windows service name examples) with no rule changes.

**v0.31 (2026-07-25)** — Windows implementation: adds the `windowsdesktop`
init profile (PM-08) so Windows users get sane desktop-notification defaults
without fabricated calibration data standing in for the GNOME `desktop`
profile's real tuning. Gives EC-01/SE-07's ownership and writability trust
check a real Windows ACL equivalent (file owner SID compared against the
current process token, DACL walked for grants beyond owner/SYSTEM/
Administrators) in place of the POSIX uid/mode-bit check it previously only
had — the check registry, external-check runner, SE-04 secret-credential
files, and the SE-06 demo database all share this one evaluator on both
platforms, so the trust contract cannot diverge between them. Windows
Event Log (`win32evtlog.EvtSubscribe`), toast notifications
(`windows-toasts`), and a named Win32 Event as the PM-11 reload-equivalent
(no `SIGHUP` on Windows) fill the three platform seams PL-01 already
reserved; the built-in `disk`/`hog`/`leak`/`load`/`net`/`service`/`events`
monitors are enabled on Windows now that their samplers (already
psutil-based) and event source have real implementations behind them, and
`service`'s `{unit=...}` watchlist kind gains a Windows Service Manager
backend alongside its existing systemd one (no restart-count metric there —
no single queryable counter exists the way `systemctl`'s `NRestarts` does).

**v0.30 (2026-07-26)** — records the macOS platform spike on real Intel
macOS 12 hardware, unelevated. A custom non-Apple `os_log` subsystem streams
without sudo or a TCC prompt, but `--style ndjson` includes non-JSON/status
lines and unified log exposes no persistent bookmark. DM-15 therefore uses an
overlapping wall-time replay with bounded event identities and an observable
retention-gap path, rather than claiming timestamp monotonicity is an exact
cursor. Zero-bundle `osascript display notification` succeeds under Script
Editor's identity, but has no supported global-disable preflight. A user-domain
LaunchAgent bootstraps without elevation and passes SIGHUP through to reload
the same PID; `kickstart -k` is explicitly a restart. The package's POSIX
guards and platform-definition filter work on Darwin, while full installation
on Intel macOS 12 is blocked by the current dependency wheel/native-build
story; none of these validated targets is a claim that macOS adapters ship.

**v0.29 (2026-07-23)** — bounds the Trends entity selector under process churn.
Exited process history remains retained under DM-04 and available through
incident links, bookmarks, APIs and Metrics, but only recently seen active
entities plus the explicitly requested historical entity appear in the Trends selector. CA-08
continues to mark vanished processes gone and clear their live incidents after
the five-minute grace period; this change fixes discovery rather than deleting
forensic history or weakening incident retention. Metrics selectors likewise
show only series with observations in their selected retention tier, replacing
expired-series empty graphs with an explicit no-observations bookmark state.

**v0.28 (2026-07-19)** — web clarity and response hardening (issues #21, #22,
#48). The operational and synthetic-demo middleware now share one exact header
contract that forbids framing, form retargeting and base-URL injection, with
same-origin isolation headers as defense in depth. Events refresh every five
seconds and the lower-churn Monitors and Self pages every fifteen seconds using
the existing full-page polling mechanism; chart explorers remain stable. The
Metrics baseline overlay now has a visible learning/ready key, contributes its
retained values to the Y-domain, and consumes explicit native-five-minute runs
so a drawing plugin cannot bridge pruned evidence.

**v0.27 (2026-07-19)** — installed-host MCP authoring guides (issue #37).
MC-05 now exposes the canonical definition, check-authoring and external-check
guides as three wheel-packaged resources. Package data is sourced directly from
`docs/` so MCP delivery cannot drift into a second maintained copy; source-tree
runs retain a development fallback. The external-check diagnostic description
also surfaces the shipped `NoNewPrivileges`/`sudo` sharp edge before an agent
designs an unusable privileged check.

**v0.26 (2026-07-18)** — explicit dashboard glance values (issue #16).
Sampler definitions can declare one primary persisted metric, display unit,
`max|min` entity reduction and labelled threshold parameters. The web app
shows only fresh raw values from active, non-exempt entities and never lets this additional
context alter UI-14 health state. CA-07 now also makes exemptions persistence
exclusions, atomically removing prior series/baselines. The shipped disk, load,
hog and leak definitions plus compatible temperature and iowait recipes declare
honest primary readouts; no database schema changes.

**v0.25 (2026-07-18)** — read-only Baselines index (issue #18). The primary
navigation now exposes every stored CA-05 row through bounded keyset pagination,
with exact filters, learning coverage/readiness, last update and links into the
matching shareable Metrics view. Reset authority remains CLI-only (UI-03).

**v0.24 (2026-07-18)** — MCP baseline visibility (issue #46). MC-07 adds the
read-only `list_baselines` tool to MC-01's frozen surface. All stored rows are
available through exact filters and an opaque filter-bound keyset cursor; the
100-row default and 500-row cap preserve MC-01's bounded-response contract
under entity churn without hiding learning levels below CA-05's rule gate.

**v0.23 (2026-07-18)** — honest Metrics baseline overlay (issue #17). CA-05
rows now retain their effective immutable half-life; changing it reseeds the
series rather than mixing EWMA coefficients. Metrics reconstructs only retained
native five-minute baseline points by reversing the EWMA from its current row,
draws contiguous runs without bridging gaps, and reports learning readiness and
range-relative truncation in its accessible summary. Persisting a duplicate
baseline-history table was rejected because the five-minute store already sits
against DM-05's capacity budget.

**v0.22 (2026-07-18)** — `diagnose_monitor` last runtime result (issue #36).
MC-06 already answered location, validation, load state, and external-alias
trust, but an agent could see a monitor as loaded and trusted while the check
failed every tick — the exact `plugin_message` lived only in
`entities.attrs`, reachable by raw SQL against the live DB (explicitly warned
against). `diagnose_monitor` now returns `last_result` for the configured
external entity (`plugin_state`/`plugin_ok`/`duration_s`/`plugin_message`/
`sample_age_s`) or null when N/A; message text remains the sanitized stored
form (SE-07/EC-05). No new write authority.

**v0.21 (2026-07-18)** — resolves OPEN-8 without expanding the frozen daemon.
The deciding security fact is now explicit: rootful Docker socket access and
container-engine group membership confer authority that the shipped SE-01
posture intentionally withholds. FTMON 2.0 therefore uses a separately
installed `check_docker` extra-monitor recipe, supported only when the same
unprivileged user already owns a rootless socket. The recipe cannot capture
between-poll transitions, so the unfrozen workstation canary records whether
that limitation creates a justified post-2.0 proposal; the two TS-17 evidence
hosts remain unchanged.

**v0.20 (2026-07-18)** — desktop notifier tray hygiene (issue #40). A live
desktop lost its session to gnome-shell's notification/calendar SIGABRT
(LP #2138529), a race armed by tray backlog — and ftmon itself had contributed
159 persistent entries over the four-day session because every delivery was a
permanent tray item and severity ≥ 3 mapped to non-expiring `critical`
urgency. NO-02 now requires the desktop adapter to send `renotify`/`recover`
transient, to reuse one replaceable notification slot per incident lifecycle
(`--print-id`/`--replace-id`, in-memory only — daemon-session IDs must not be
persisted), and to reserve `critical` for severity 4. Each behavior is gated
on probing the installed `notify-send`; missing flags degrade to the previous
persistent delivery rather than failing (NO-04 semantics are unchanged).

**v0.19 (2026-07-17)** — leak evidence quality and generic-process identity
(issue #20). A desktop leak warning identified Cursor's background agent only
as `MainThread`, fired after ~10 minutes because window functions treat
`"45m"` as a maximum rather than a requirement, and its incident survived a
daemon restart as permanently open because CA-08's grace state was
memory-only. Three changes: `coverage(m, w)` joins the CA-01 table so a
windowed verdict can demand its window be observed, and §7.7.1 rules require
coverage plus recent net growth alongside slope — growth confidence (`monot`)
deliberately stays out of the alert gate because a genuine stepwise leak
(grow, plateau, grow) scores low on consecutive-delta confidence, and
suppressing it would hide the detector's target (the deterministic
sawtooth/step fixtures pin this); messages now claim "sustained RSS growth",
not "leaking". SA-09 gives processes a display identity (`exe_base`,
`display`, `cmd_hint`) distinct from DM-02's stable identity, keeping SE-04's
cmdline posture. IN-09 seeds disappearance tracking from stored `last_seen`
at startup so entity-gone clearing survives restarts.

**v0.18 (2026-07-17)** — authoring discoverability (issue #25). An AI agent
adding a monitor from the docs alone guessed wrong four times: wrong drafts
directory, SIGHUP (then fatal), a double-applied perfdata scale, and no way
to ask "why isn't this monitor running?". CL-06 (`ftmon paths`), CL-07
(`ftmon monitor rescan`, riding PM-11), CL-08 (`ftmon check trust`, the
EC-01/SE-07 predicate with reasons) and MC-06 (`monitor_paths`,
`diagnose_monitor`) make the layout, reload, trust, and load-state questions
answerable in one step; `define_monitor` returns structured `next_steps`.
No new write authority anywhere (MD-05/EC-01 unchanged). The daemon records
its pid in the PM-02 lock file to give CL-07 a signalling target.

**v0.17 (2026-07-17)** — SIGHUP reloads instead of killing the daemon
(issue #24). Only SIGTERM/SIGINT had handlers, so the conventional Unix
reload signal fell through to the default disposition and terminated the
monitor — a trap that took down a live install during monitor authoring.
PM-11 makes the handler set a flag that the next tick's rescan path
consumes (same refresh as PM-04), and the packaged units gain
`ExecReload=` so `systemctl reload ftmon` works.

**v0.16 (2026-07-17)** — crash tolerance for tick write lock loss (issue #23).
An external writable SQL session that outlives `busy_timeout` made
`commit_tick`'s `BEGIN IMMEDIATE` raise `OperationalError("database is locked")`,
which escaped `Scheduler.run` and killed the daemon while the web UI kept
serving stale data. PM-10 requires that path to drop the tick's buffered
writes, count `sqlite_lock_errors`, emit a self-event, and continue —
one lost tick is acceptable; a silent daemon exit is not.

**v0.15 (2026-07-12)** — process hardening plus one reopened product
question. TS-19 turns document-version coherence into a machine-checked
invariant after the `Status:` header drifted from the changelog twice
(v0.10→v0.11 and v0.12→v0.14), each time caught by a later manual review —
exactly the class of defect this repository's "lint rules are enforced as
tests" rule exists to eliminate. OPEN-8 records the container-monitoring
decision (Docker/Podman as a v1.1 core source versus an extra-monitor
recipe) with an explicit resolution deadline inside the TS-17 soak window,
so the largest remaining capability gap is decided deliberately rather than
implemented impulsively against a frozen daemon.

**v0.14 (2026-07-12)** — defines a repository-owned Agent Skill for adding
extra monitors. The portable SKILL.md is canonical because recipe work crosses
execution, privilege, licensing and evidence boundaries; vendor discovery
locations remain installation adapters so Codex and Claude instructions cannot
silently diverge. Structural CI checks drift while repository authority and
human review remain above any agent workflow.

**v0.13 (2026-07-12)** — specifies `exchange.ftmon.org` as a generated static
view of the in-repository extra-monitor authority. Keeping recipes beside the
schema and their contract tests makes compatibility changes atomic; publishing
only an inert artifact avoids creating a second release boundary or accepting
unreviewed executable uploads. Search is progressive enhancement, publication
metadata is explicit, and pull requests can build but never deploy.

**v0.12 (2026-07-12)** — incorporates the 2026-07-12 whole-repository review.
The review found the code, tests and traceability strong but the operational
claims unweathered: the implementation landed in three days, security
requirements SE-01..03 sat in the pending list, nothing checked the prose docs
or README's external claims against reality, and repo guidance (`CLAUDE.md`)
still described the retired Perl architecture. Accepted as requirements: TS-17
(recorded ≥30-day two-host soak gating v1.0), TS-18 (pending list empty at
v1.0, SE-* burned down first), DO-09 (per-milestone documentation-drift and
external-claim audit; review artifacts are not documentation), and milestone
M10 collecting them. Fixed directly rather than specified: CLAUDE.md rewritten
for v2, and the review-artifact hygiene applied immediately — the historical
`CODEX-SPEC-REVIEW.md` and ad-hoc repository reviews were removed from the
tree (their content survives in git history and in this changelog's
dispositions). Noted, not specified: the starlette TestClient deprecation
warning (dependency sweep folded into M10).

**v0.11 (2026-07-12)** — specifies a curated, testable extra-monitor cookbook.
Human-readable articles are paired with bounded metadata, example registry and
definition files, and deterministic output fixtures. Third-party plugins remain
upstream-owned and separately licensed; an original FTMON script must meet a
higher direct-test and licence bar.

**v0.10 (2026-07-11)** — specifies M9's bounded external-check seam. An
administrator registers exact local argv once; declarative monitors may reuse
that authority but cannot create it. FTMON-native JSON and Nagios-compatible
exit/output adapters turn only explicitly mapped performance labels into typed
metrics, after which existing history, formulas, baselines, incidents, Metrics,
Trends, notifications and MCP work unchanged. This deliberately reuses the
large check ecosystem without importing third-party code, vendoring GPL
plugins, adopting NRPE, or becoming a fleet monitor.

**M9 implementation note (2026-07-12)** — the protected registry, bounded
runner, Nagios and strict FTMON JSON adapters, declared performance-data
projection, atomic reload, daemon scheduling, self diagnostics and
operator-facing documentation are implemented. Third-party plugins remain
separately installed and licensed; monitor definitions still cannot grant
execution authority.

**v0.9 (2026-07-11)** — extends the single-host scope from desktops to individually managed servers without adding fleet management. Notification fan-out gains independent durable channel state for file, desktop, ntfy, generic webhook, and SMTP delivery, with explicit retry, TLS, privacy, and credential rules. A separate synthetic, GET-only demo mode permits `demo.ftmon.org` without exposing an operational database or weakening the loopback-only production UI.

**v0.8 (2026-07-11)** — removes the original Perl source from the v2 repository and points to its authoritative SourceForge project instead. This makes provenance and the MIT/GPLv2 licensing boundary unambiguous without losing the historical reference.

**v0.7 (2026-07-11)** — restores the original FTMON green/yellow/red glanceability without restoring inaccessible color-only or flashing behavior. Tile state is an explicit precedence over config, freshness, enabled state, and highest live incident; acknowledged remains unhealthy until recovery.

**v0.6 (2026-07-11)** — keeps Metrics Explorer as the arbitrary single-series diagnostic surface but replaces its temporary SVG with the same uPlot foundation and historical semantics as Trends. The two pages share rendering and data contracts without conflating their purposes: Metrics reports observations; Trends adds only definition-declared interpretation.

**v0.5 (2026-07-11)** — generalizes M7 through declarative `[[trend]]` profiles, with disk capacity and process RSS growth as reference implementations. Panels are optional by meaning rather than fabricated from whatever metrics happen to exist. One Trends explorer and contextual links replace monitor-specific chart implementations; `/disks` remains compatible.

**v0.4 (2026-07-11)** — adds M7 historical disk-trend visualization. The query contract exposes rollup statistics and extrema; signed fill rate is persisted before downsampling; projections are suppressed unless positive, sufficiently covered, and corroborated by monotonic filling confidence. This deliberately follows v1/M6 so richer analytics cannot delay the operational release.

**v0.3 (2026-07-10)** — design-phase capacity amendments (DESIGN.md §9 worksheet, per DM-16): DM-04 hourly-rollup retention split (400 d durable series / 90 d process series); DM-09 event store-filter (severity ≥ notice or rule-matching; full journal volume cannot fit DM-05). No other changes.

**v0.2 (2026-07-10)** — incorporates the external review (`CODEX-SPEC-REVIEW.md`, removed from the tree in v0.12 per DO-09; see git history). Accepted and specified: ladder-group incident model (IN-03, owner decision); episode semantics + msg_hash for event rules (§7.7.3, IN-08); expression-language reconciliation (severity constants, no kwargs, EX-06 truth table, numeric/regex edges, derived-metric ordering); TOML example completed (`schema`, `enabled`, integer `version`, `source_options`) and MD-07 built-ins-must-validate gate; source-once-per-tick pipeline (SA-06) and honest timeout semantics (SA-02); capacity worksheet + degradation order + caps (DM-16, DM-05, DM-03, DM-13, CA-04); event cursor/queue/durability (DM-15, SA-08); config-file coordination (PM-06, PM-07); notification outbox with explicit at-least-once bound (NO-04, DM-14); reproducible baseline algorithm (CA-05 = EW mean, half-life 3 d); privacy posture (SE-04, owner decision: collect truncated); loopback hardening (UI-08); clock discipline (SA-07); entity disappearance (CA-08); removal/rename semantics (MD-09); `self` as explicit eighth built-in (§7.7); `ftmon doctor` + backup-API-only backups (CL-05, VC-03); accessibility (UI-09); delivery milestones (§20). Owner decisions this round: ladder groups; **MIT license**; cmdline collect-truncated; adopt reviewer positions on OPEN-2..6; OPEN-1 defaults accepted as-shipped (tunable in installed files). Deliberately rejected/deferred: baseline seasonality (NG-07), secret-pattern redaction (NG-08), SSE (UI-04), per-process net attribution (NG-06).

**v0.1 (2026-07-10)** — initial draft from grilling rounds 1–3.

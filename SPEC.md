# FTMON v2 — Specification

Status: **DRAFT v0.3** — v0.2 incorporated the external review (`CODEX-SPEC-REVIEW.md`, disposition in §21); v0.3 adds two capacity-driven amendments from the design phase (DM-04, DM-09 — see `DESIGN.md` §9). All §19 open questions are resolved.
Audience: implementers (including LLM-based implementers) and the reviewer (project owner).
Every requirement has a stable ID (`XX-nn`). Tests MUST reference requirement IDs. Renumbering is not allowed after v1.0 of this document; retired requirements are marked `[RETIRED]`, new ones appended.

---

## 1. Purpose

FTMON v2 is a lightweight, local, single-host systems monitor for desktops and workstations. It:

- detects memory leaks, CPU hogs, disks filling, and log/event-log entries of interest, and raises desktop notifications;
- records metric history so questions about past behavior can be answered;
- lets users (and, with approval, AI) define new monitors declaratively, including formula-based derived metrics;
- exposes everything to AI assistants through a local MCP server;
- is fully usable **without** AI through a CLI and a local web UI.

It is the successor to the legacy Perl FTMON (2001–2003, preserved in `ftmon-legacy/`). It ports that system's design ideas — delta/monotonic calculations, consecutive-cycle confirmation, baselining, threshold tables, escalation — not its code.

### 1.1 Non-goals (v1)

- **NG-01** Multi-host / fleet monitoring, remote agents, or any network listener other than the localhost web UI.
- **NG-02** Email, SMS, or webhook notification channels (desktop notifications only; the architecture must not preclude adding channels later).
- **NG-03** Being a Nagios/Zabbix/Prometheus replacement. If a feature is only needed for server fleets, it is out of scope.
- **NG-04** Windows and macOS *implementations* (interfaces and schema must support them; see §4). Linux ships first.
- **NG-05** Authentication/multi-user support in the web UI (localhost, single user).
- **NG-06** Per-process network connection attribution (deferred; needs elevated rights on some systems).
- **NG-07** Baseline seasonality (day-of-week / time-of-day patterns) — deliberately absent in v1; the baseline is a single smoothed level (CA-05).
- **NG-08** Secret-pattern redaction of command lines / log messages (privacy posture is SE-04: local single-user data, restrictive file modes, truncation, collection toggle).

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

---

## 3. Product decisions already made (context for implementers)

These were decided during specification and are not open for re-litigation by implementers:

- Language: **Python ≥ 3.11**, managed with **uv** (`pyproject.toml`, lockfile). Lint/format: **ruff**. Tests: **pytest**.
- Repo: monorepo at `PROJECTS/ftmon`; new code in `ftmon/` package; `ftmon-legacy/` retained read-only as design reference. Third-party vendored files in the legacy tree are never modified.
- License: new code **MIT**; `ftmon-legacy/` remains GPLv2 in its subtree (separate works, clearly marked).
- Storage: **SQLite** (WAL mode, `auto_vacuum=INCREMENTAL`). No external database, no RRDtool.
- Process model: daemon + CLI + MCP server + web UI are **separate processes** sharing the SQLite database and (for definitions) the config directory under the coordination rules of PM-06/PM-07. Web UI is a fully separate service from the daemon.
- Monitor definitions: **TOML** with expression strings in a **restricted Python-AST subset** (§8). Definitions are data, never executable code.
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

| Capability | Linux (v1) | Windows (v1.x, planned) | macOS (v1.x, planned) |
|---|---|---|---|
| Process/CPU/mem/disk sampling | psutil | psutil | psutil |
| Event source | journald (`journalctl -o json` subprocess) | `win32evtlog.EvtSubscribe` (pywin32) | `log stream --style ndjson` subprocess |
| Event cursor (DM-15) | journald cursor string | `EvtBookmark` XML | last-seen timestamp |
| Notification | `notify-send` (fallback: D-Bus) | toast (`windows-toasts`) | `osascript display notification` |
| Service wrapper | systemd user unit | Task Scheduler (logon) | launchd LaunchAgent |
| Config/data paths | XDG dirs | `%APPDATA%` / `%LOCALAPPDATA%` | `~/Library/Application Support` |

- **PL-01** All platform-specific behavior MUST live behind exactly four seams: `Sampler` implementations, `EventSource` implementations, the notification adapter, and the service wrapper/paths module (use `platformdirs`). No platform conditionals anywhere else.
- **PL-02** The canonical schemas (§5) MUST NOT assume any platform's shape. In particular `event_id` is an **optional string** (Windows has numeric IDs, journald has identifiers, macOS has none).
- **PL-03** Permission failures during sampling (e.g. psutil `AccessDenied`) MUST degrade gracefully: skip the entity, count it in self-metrics (§13), never crash or spam the log (log once per entity per daemon lifetime at DEBUG).
- **PL-04** v1 ships and is tested on Linux only, but the fake/fixture implementations of `Sampler` and `EventSource` (§16) count as second implementations, keeping the seams honest.
- **PL-05** Every `Sampler` and `EventSource` statically declares its schema: entity kind, metric names/units, attr names/types. Validation (MD-01) resolves expression names against these declarations; the declarations are also the documentation source for DO-01.

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
- **PM-07** On each successful load, the daemon persists the monitor's normalized definition, content hash, and load timestamp in the DB. This is the substrate for change detection (PM-04), `get_monitor` history, and MD-06 — not a fallback config store (see PM-04).

### 4.3 Filesystem layout (Linux)

```
~/.config/ftmon/config.toml            # global settings
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

- **DM-04** Raw samples are kept **48 h**. 5-minute rollups `(avg, min, max, last, count)` are kept **30 d**. 1-hour rollups are kept **400 d** for *durable* series (system, disk, self, and watchlist-synthetic entities) and **90 d** for process-sourced series (v0.3 amendment: the capacity worksheet shows process-entity churn makes 400 d hourly retention for all series infeasible within DM-05). Rollup jobs run in the daemon, incrementally, never more than 1 s of work per cycle.
- **DM-05** Total database size MUST stay under **200 MB**. On breach the daemon degrades in this fixed order until under budget: (1) oldest raw samples beyond 24 h, (2) oldest events beyond 7 d, (3) oldest 5-min rollups, (4) oldest 1-h rollups. Incidents are never pruned. Each degradation step records a self-event. The DB is created with `auto_vacuum=INCREMENTAL`; `PRAGMA incremental_vacuum` runs after prune batches, full `VACUUM` at most weekly, off-cycle.
- **DM-06** Queries spanning tiers (raw → 5 m → 1 h) MUST be answered transparently by the query layer choosing resolution by range; callers never pick tables.
- **DM-16** The design document MUST include a capacity worksheet deriving RB-01/DM-05 feasibility from stated assumptions — max tracked entities (budget: 400 persisted), metrics per entity (≤ 10), sample width in bytes, rows/day at 60 s intervals, event rates, ring-buffer memory (CA-04) — and the worksheet's assumptions become validation limits (a definition exceeding them is rejected).

### 5.3 Canonical event record

`(ts, source, provider, event_id: str|null, severity, message, attrs: JSON)`

- **DM-07** `source` ∈ {`journald`, `eventlog`, `oslog`, `file`, `self`}. `provider` is the platform's producer field (journald `SYSLOG_IDENTIFIER`/`_SYSTEMD_UNIT`, Event Log Provider, os_log subsystem). `self` is FTMON's own operational events (config errors, budget breaches, prune runs, clock gaps, event overflows).
- **DM-08** `severity` is normalized to the 5-level scale: `info(0) notice(1) warning(2) error(3) critical(4)`. Each `EventSource` documents and tests its mapping (journald PRIORITY 0–7 → this scale; Event Log Level; os_log messageType).
- **DM-09** Stored events are kept 30 d (subject to DM-05 degradation). A **store-filter** (v0.3 amendment, capacity-driven) decides what is stored: events with severity ≥ `notice` (configurable `store_min_severity`) plus any event matching a loaded event rule; info-level non-matching events are counted in a self-metric but not stored — a desktop journal's full volume (50–200 k lines/day) cannot fit the DM-05 budget. Event *rules* (§7.7.3) evaluate against the live stream before the store-filter (a rule can match info-level events; matching forces storage) and match on canonical fields only — a rule written against journald fields MUST be expressible identically against Event Log fields.
- **DM-10** Event ingestion MUST be rate-defended: per (source, provider), more than 100 stored events/min collapses into a single `event_storm` self-event with a count, until the rate drops. (A log-spamming app must not fill the DB.)
- **DM-15** Each `EventSource` persists a **cursor** in the DB after every drained batch (journald cursor string / EvtBookmark / last-seen timestamp). First run ever starts at "now" (no historical backfill). On daemon restart the reader resumes from the cursor, which replays events that occurred while the daemon was down; the cursor's monotonicity is the dedup guarantee. Events carry both source timestamp (stored as `ts`) and ingest timestamp; ordering for rules is ingest order, so late-arriving source timestamps cannot re-trigger past windows.

### 5.4 Incident

`(id, monitor, group, entity_id, state, severity, owning_rule_id, opened_ts, last_change_ts, cleared_ts|null, clear_reason|null, ack: {by, ts}|null, notify_count, occurrences, history: [...])`

- **DM-11** `state` ∈ {`open`, `acked`, `cleared`}. Identity is **(monitor, entity, group)** per IN-03. `clear_reason` ∈ {`recovered`, `entity_gone`, `superseded`, `quiet_period`}. Incidents are never deleted by retention; they are the system's long-term memory.
- **DM-12** `history` records every state/severity transition, every notification sent, and every action run, with timestamps — sufficient for `explain_incident` (§11) to reconstruct the full story (subject to the DM-13 cap).
- **DM-14** Notifications flow through a durable **outbox**: rows `(id, incident_id, kind, rendered_body, created_ts, delivered_ts|null, stale: bool)` written in the same transaction as the incident transition that caused them (see NO-04).

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

### 6.2 Scheduling

- **SA-01** The scheduler ticks every 5 s on the **monotonic** clock. Each monitor declares `interval` (default `"60s"`, min `"15s"`); it runs on the first tick at/after its due time. A monitor whose cycle overruns its interval is skipped (not queued) for the missed slot, with a self-metric counting overruns.
- **SA-02** Samplers run sequentially in the daemon (no thread pool in v1). Timeout semantics are honest about Python's limits: **subprocess-backed** sources (journalctl, systemctl, log) get hard deadlines enforced by kill; **in-process** samplers (psutil loops) check a cooperative deadline between entities (default budget 10 s); a truly stuck native call cannot be killed and is instead *detected* — the cycle-overrun self-metric and the `self` monitor's watchdog rule (RB-02) surface it.
- **SA-07** Clock discipline: scheduling and elapsed-time math use the monotonic clock; sample/event timestamps use wall UTC. After suspend/resume or any monotonic gap > 2× base tick, missed cycles are **skipped without catch-up** and a `clock_gap` self-event records the gap. Backoff arithmetic (IN-02) uses wall timestamps but treats negative elapsed time (wall clock stepped back) as zero and recomputes from now. Window functions simply see a gap in samples; CA-02's `None` semantics make rules silent rather than wrong across gaps.

### 6.3 Sources

- **SA-03** `EventSource`s run as supervised subprocess readers (e.g. `journalctl -f -o json --after-cursor=…`) feeding an in-daemon queue, drained each tick. A dead reader is restarted with exponential backoff (1 s → 60 s cap) and a self-event on first death.
- **SA-08** The event queue is bounded at 10 000 entries; on overflow the oldest are dropped and an `event_overflow` self-event records the count. Malformed lines are skipped and counted (self-metric), never fatal. Reader stall detection: `event_source_last_activity_age` is a self-metric; the `self` monitor warns when it exceeds 10 m while the reader process is alive.
- **SA-04** Built-in samplers v1: `process` (per-process cpu%, rss, and — where available without elevated rights — open fds, threads, io counters), `disk` (per-mount total/used/free bytes, inodes where supported), `system` (load1/5/15, cpu% total, mem available/used, swap, PSI where present), `net` (per-listen-socket presence, per-proto/state connection counts; **no per-process attribution in v1**, NG-06), `unit` (systemd unit active-state + NRestarts via `systemctl show`).
- **SA-05** The `process` source implements **track-all + promote**: every process is sampled into a bounded in-memory window (last 15 of its samples) each tick it's due; long-term persistence happens only for entities that are (a) on a monitor's watchlist, (b) in the top-N (default 15) by cpu or rss that cycle, or (c) **promoted** by a trend heuristic (§7.6.1). Promotion/demotion transitions are recorded as self-events. This keeps DM-05/DM-16 achievable with hundreds of processes.

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

- **CA-05** `baseline(m)` is an **exponentially weighted mean**, precisely: updated once per 5-minute rollup of `m` as `b ← b + α·(rollup_avg − b)` with `α = 1 − 2^(−Δt/half_life)`, half-life default 3 d (config per monitor). It is stored persistently per (monitor, entity, metric) with its update count. It returns `None` until **coverage** ≥ 240 rollup updates (~24 h of actual data — counted updates, not elapsed time). A new entity_id (process restart) starts a fresh baseline. Data sampled during open incidents is *not* excluded (documented contamination caveat; acceptable for v1). Seasonality: NG-07.
- **CA-06** `ftmon baseline reset <monitor> [entity]` clears learned baselines.

### 7.5 Exemptions

- **CA-07** Every monitor supports an `exempt` list of entity-match expressions evaluated before rules (e.g. process name regexes for compilers/browsers on the hog monitor; fs types on the disk monitor, succeeding legacy `SKIP_FS_P`). Exempt entities are still *sampled* (history remains queryable) but no rules fire.

### 7.6 Entity disappearance

- **CA-08** When a **discovered** entity (process, mount) stops appearing in snapshots, its metrics simply stop (rules go `None` via CA-02). After `gone_grace` (default 5 m) the entity is marked gone (`gone_ts` in DM-03): its confirmation counters reset and any open incident for it clears with `clear_reason = entity_gone` and a recovery notification whose message says so (a leaking process that exits is a resolved leak). **Watchlist** entities (service units, expected listeners) never disappear: they are synthetic, always present, with a `present` (0/1) metric — absence is their alerting signal, not their removal.

### 7.7 Built-in monitors (seven user monitors + `self`)

v1 ships seven user-facing monitors plus the always-installed **`self`** monitor (§13, RB-02) — `self` is tunable but not deletable. Each ships as a commented TOML file (FS-02); defaults below are starting points reviewable in the file, but the *shape* (parameters, metrics, rule structure) is normative. `OPEN-1`: default numbers need owner review — to be exercised against recorded fixture data and a short real-system observation period before v1.0.

#### 7.7.1 `leak` — per-process memory-leak detector
Metrics: `rss_bytes` (+ derived `rss_slope_bph` = slope in bytes/hour). Promotion heuristic (SA-05): `monot(rss_bytes, "15m") >= 0.8 and delta(rss_bytes, "15m") > 16*MB`. Rules (one group `leak`): warning when `slope(rss_bytes, "45m") * 3600 > 32*MB` with `confirm_cycles = 3`; error rung when `slope(rss_bytes, "45m") * 3600 > 128*MB`. Exempt-by-default: none (browsers are the *point*); the file shows a commented example.

#### 7.7.2 `hog` — CPU hog detector
Metrics: `cpu_pct`. Rules (group `hog`): warning when `avg(cpu_pct, "5m") > 80` for `confirm_cycles = 5`; error rung at `avg(cpu_pct, "15m") > 90`. Default exempt examples (commented): `matches(name, "^(cc1|rustc|ld|clang|make|cargo|ffmpeg)")`.

#### 7.7.3 `events` — journal/event-log entries of interest
Consumes the event stream; rules are **episode** rules (IN-08). Example shipped enabled: `severity >= error and not matches(provider, "^(tracker-|gnome-shell$)")`; a specific-ID example (`event_id == "6008"`, styled for future Windows use) ships commented. Episode identity: `(rule, provider, event_id if present else msg_hash)`. `msg_hash` is normatively defined: lowercase the message, collapse whitespace, replace digit runs and hex runs (≥ 8 chars) with `#`, then SHA-256, first 16 hex chars — collisions merely group unrelated events, which is harmless. Per-rule `cooldown` (default `"10m"`) limits renotification; `clear_after` (default `"30m"` without a matching event) closes the episode with `clear_reason = quiet_period` and **no recovery notification** by default (`notify_recovery = false` for event rules). A new matching event after clearing opens a new episode; the flap guard (IN-05) applies.

#### 7.7.4 `disk` — space + filling
Metrics per mount: `used_pct`, `free_bytes`, `used_bytes`, `inode_used_pct`; derived `filling = monot(used_bytes, "70m")`. Rules: ladder group `space` — notice/warning/error rungs at `used_pct >` 85/92/97 (plus commented baseline-relative alternative `free_bytes < baseline(free_bytes) * 0.3`); separate group `inodes` (rungs at 75/80/90); separate single-rule group `filling` — warning on `filling >= 0.85` with projected-full time in the message. Exempt: `matches(fstype, "^(tmpfs|iso9660|squashfs)$")`.

#### 7.7.5 `load` — system pressure
Metrics: `load1`, `cpu_pct`, `mem_available_bytes`, `mem_total_bytes`, `swap_used_pct`, PSI `psi_some_cpu`/`psi_some_mem`/`psi_some_io` (60 s avg) where present. Rules: group `pressure` — warning when `avg(psi_some_cpu, "5m") > 40` or `pct(mem_available_bytes, mem_total_bytes) < 5` for 5 cycles; error rung on `slope(swap_used_pct, "10m") > 0 and avg(psi_some_mem, "5m") > 25`.

#### 7.7.6 `service` — process/unit presence
Watchlist-driven (no auto-discovery): each target is a systemd unit or process-name regex, expected state, optional `during` schedule. Metrics: `present` (0/1), `restarts`. Rules: error when `present == 0` for `confirm_cycles = 2`; notice on flapping (`delta(restarts, "30m") >= 3`).

#### 7.7.7 `net` — sockets
Watchlist of expected listeners (`proto:port`) → `present` metric, error when absent (as `service`). System-wide `conn_total` and per-state counts with a warning on `conn_total > baseline(conn_total) * 4` sustained 5 cycles. Per-process attribution: NG-06 (deferred).

---

## 8. Monitor definitions and the expression language

### 8.1 TOML schema (normative shape)

```toml
schema = 1                       # definition-format version (VC-02)

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

exempt = [ "matches(fstype, '^(tmpfs|iso9660|squashfs)$')" ]

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

- **MD-01** The full schema (all keys, types, bounds, which keys are required/forbidden per `source` kind — including `source_options` shapes for `service` and `net`) is defined once as a versioned JSON-Schema-equivalent in code; `ftmon check`, `define_monitor`, and daemon loading all use the *same* validator. Error messages MUST name the file, key, and reason.
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
- **MD-09** Removing or renaming a definition: open incidents clear with `clear_reason = superseded`; stored samples/rollups age out by normal retention; baselines for the monitor are deleted; entity records are retained until their data ages out. A renamed monitor is a removal plus an addition (no identity continuity).

---

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

### 9.2 Notification contract

- **NO-01** A notification carries: severity glyph + monitor + entity, the rendered rule `message`, and (where the platform allows) a "details" hint pointing at `ftmon incident <id>` / the web UI URL. Body ≤ 200 chars; truncation is deliberate — depth lives in the UI/CLI.
- **NO-02** The notifier is an adapter interface (PL-01) with two v1 implementations: `desktop` (notify-send) and `file` (append JSON-lines; used by tests and available as an audit log, default on: `~/.local/state/ftmon/notifications.jsonl`).
- **NO-03** Global quiet hours (`config.toml`, default off): during quiet hours, `warning`-and-below notifications are held and delivered as one digest at quiet-hours end; `error`+ always notify. Incidents open/clear regardless — quiet hours affect delivery only. Global-only in v1 (per-monitor overrides deferred).
- **NO-04** **Delivery guarantee — at-least-once, honestly.** The outbox row (DM-14) is committed in the same transaction as the incident transition; delivery then happens; `delivered_ts` is set after. A crash between delivery and the `delivered_ts` update can duplicate **at most the single in-flight notification** — TS-05's kill-9 test asserts exactly this bound (≤ 1 duplicate), not exactly-once. On restart, undelivered rows older than 10 m are marked `stale` and dropped, **except** incident-opening notifications of severity `error`+ which are delivered with a "(delayed)" prefix. No committed incident transition ever silently loses its notification.

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
| `get_status` | () | daemon liveness, last cycle, monitor list w/ state, open incident counts, budget self-metrics |
| `query_metrics` | (monitor, metric, entity?, range, agg?, filter_expr?) | series data, resolution auto-chosen (DM-06); `filter_expr` uses §8.2 language over entity attrs |
| `top_consumers` | (resource: cpu\|rss\|io, range, n=10) | ranked entities with aggregates over range |
| `get_process_history` | (name_or_pid, range) | metrics + lifecycle (starts/stops/gone) for matching process entities |
| `list_events` | (range, min_severity?, provider?, match_expr?, limit=200) | canonical events |
| `list_incidents` | (state?, range?, monitor?) | incidents/episodes with summaries |
| `explain_incident` | (id) | rule text + parameter values, evaluation series around opening, related events ±10 m, full history (DM-12) |
| `list_monitors` / `get_monitor` | (name) | definitions incl. drafts (marked), validation status, load history (PM-07) |
| `validate_monitor` ✎(no writes) | (toml_text) | full validation, returns errors or normalized form |
| `define_monitor` ✎ | (toml_text) | validate → write to `drafts/` (PM-06) → return "pending approval: run `ftmon monitor approve <name>` or use web UI" |
| `ack_incident` ✎ | (id, note?) | sets acked with `by = "mcp"`, note into history |

- **MC-01** The tool list above is the complete v1 tool surface; names and required parameters are frozen by this spec (exact JSON schemas in the design doc). Every tool answers within 2 s on a DM-05-sized database.
- **MC-02** Range parameters accept `"90m"`-style durations or ISO-8601 pairs; all responses carry UTC timestamps plus the host's IANA timezone name once per response for the model to localize.
- **MC-03** `define_monitor` MUST refuse (not silently overwrite) a name that already exists as enabled/disabled; drafts may be overwritten (iterating on a draft is the normal flow).
- **MC-04** Error responses are structured (`code`, `message`, `hint`) — a less capable model must be able to self-correct from validation errors (MD-01's quality bar applies).
- **MC-05** The server exposes one MCP **resource**: the monitor-definition guide (DO-01) — so a model authoring a definition can pull the reference without leaving the session. The full SPEC is not exposed (operational noise).

---

## 12. Web UI

A local, single-user, AI-optional interface — the modern successor to legacy's generated HTML, and deliberately much better.

- **UI-01** `ftmon web` serves on 127.0.0.1:8420: no external network assets whatsoever (all JS/CSS/fonts vendored; must work fully offline), no auth (NG-05).
- **UI-02** v1 pages: **Dashboard** (per-monitor status tiles, open incidents, daemon health/budget strip, sparklines); **Incidents** (filter, detail view = `explain_incident` rendered, ack button); **Metrics explorer** (pick monitor/entity/metric/range → chart; shareable URL state); **Events** (filterable browser); **Monitors** (definitions rendered with docs, enable/disable toggle, drafts with rich validation view and **Approve** button); **Self** (daemon log tail, self-metrics, DB size, config errors).
- **UI-03** Write operations are exactly: ack incident, enable/disable monitor, approve/delete draft. Each is a POST hitting the same code paths as the CLI equivalents (incl. PM-06).
- **UI-04** Data freshness: dashboard and incident views poll every 5 s (no SSE in v1); a stale daemon (last cycle > 3× base interval) shows an unmistakable banner.
- **UI-05** Charts must remain legible with 400 d hourly data (downsampled server-side to ≤ 2 000 points per series per request).
- **UI-06** Server-side rendering with minimal vendored JS (htmx-style partials + one small chart library — chosen in the design doc for size, accessibility, and long-range rendering) is the required *style*: no SPA framework, no frontend build step beyond file copying.
- **UI-07** The web server process is optional at runtime: nothing else may depend on it.
- **UI-08** Request hardening despite loopback: exact `Host` header allowlist (`127.0.0.1:<port>`, `localhost:<port>`) — anything else is 400 (defeats DNS rebinding); POSTs require a matching `Origin`; no CORS headers are ever emitted; responses set `X-Content-Type-Options: nosniff` and the SE-02 CSP.
- **UI-09** Accessibility: severity is never conveyed by color alone (icon + text label); all interactive elements keyboard-operable; `prefers-reduced-motion` respected; every chart has a text alternative (current value + trend sentence).

---

## 13. Resource budget (self-enforced)

- **RB-01** Daemon steady-state: ≤ 1 % of one CPU averaged over 10 m; RSS ≤ 100 MB; DB ≤ 200 MB (DM-05). Web UI and MCP processes: RSS ≤ 80 MB each. Feasibility is demonstrated, not asserted: DM-16's capacity worksheet.
- **RB-02** The daemon samples **itself** (cpu, rss, cycle duration, per-source duration, DB size, event queue depth, ring-buffer memory, event_source_last_activity_age) into the built-in `self` monitor (§7.7) with rules that open a `warning` incident on sustained budget breach — the monitor must not become the hog, and if it does, it tells on itself.
- **RB-03** Tier-1 e2e tests assert cycle-time and DB-growth invariants under a synthetic 300-process, 10-events/s load (§16.4).

---

## 14. Security & privacy

- **SE-01** Attack surface by construction: no listening sockets except web UI on loopback (hardened per UI-08); MCP on stdio; definitions are data validated against MD-01; expressions cannot reach the interpreter (EX-01..07); actions are pre-existing user-created executables only (AC-03); the daemon runs as the user, never root; anything needing elevation is skipped per PL-03.
- **SE-02** Event messages and process cmdlines are untrusted strings: every sink (web UI templates, notifications, CLI, MCP JSON) escapes appropriately; the web UI sets a restrictive CSP (`default-src 'self'`).
- **SE-03** The legacy CipherSaber password feature is **not** carried forward. v1 stores no secrets. (SNMP/remote checks, if ever added, will use the OS keyring — recorded here so implementers don't improvise.)
- **SE-04** Privacy posture: process command lines are collected by default, truncated to 256 chars, storable off via `collect_cmdline = false` in `config.toml` (then only the executable basename is stored). Event messages truncate at 2 KB (DM-13). The DB, daemon log, and notification audit file are mode 0600 in 0700 directories (PM-06/FS). MCP and the web UI see the same data (local, single-user trust model); no redaction machinery in v1 (NG-08).

---

## 15. CLI

- **CL-01** Single entry point `ftmon` with subcommands: `daemon`, `mcp`, `web`, `init`, `status`, `top`, `incidents`, `incident <id>`, `ack <id>`, `events`, `query`, `monitors`, `monitor approve|enable|disable <name>`, `check [file]`, `baseline reset`, `doctor`, `version`. All read paths work with the daemon down (PM-01).
- **CL-02** `ftmon check` validates all definitions (or one file) and exits non-zero on any error — the successor of legacy `-c`, and the pre-commit/CI hook for definitions.
- **CL-03** Every list-producing subcommand supports `--json` (stable, documented shape shared with MCP responses) — the CLI is also scripting surface.
- **CL-04** `ftmon status` is the legacy `-z` successor: one screen, exit code 0/1/2 mapping to (all-clear / warnings / errors+) for scripting.
- **CL-05** `ftmon doctor`: runs `PRAGMA quick_check` (full `integrity_check` with `--deep`), WAL checkpoint, reports DB/table sizes, orphaned rows, cursor ages, and config errors; `ftmon doctor --backup <path>` produces a consistent snapshot via the SQLite backup API. Naive file-copy of the live WAL database is documented as unsupported (VC-03). Exit non-zero on any problem found.

---

## 16. Testing requirements

### 16.1 Principles

- **TS-01** Every `XX-nn` requirement in this document maps to ≥ 1 test carrying the ID in its name or docstring; `tests/traceability.py` fails CI if a requirement (marked `testable: yes` in the requirements index the design doc will generate) has no test.
- **TS-02** Implementation work packages will be delivered tests-first where feasible: interfaces + tests frozen before implementation is requested from implementing models.

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
- Outbox: NO-04 stale-drop and delayed-delivery rules.

### 16.4 Tier-1 e2e (CI, deterministic)

- **TS-05** Harness: launch the real `ftmon daemon` binary with `--clock=controlled` (FakeClock stepped over a control socket/file), `--fixtures <scenario>`, temp XDG dirs, `file` notifier. Assertions run against the DB, `notifications.jsonl`, and CLI/MCP/web responses. Scenario cases: each built-in monitor's happy-path fire-and-clear; ladder escalate → downgrade → clear; episode lifecycle; backoff timing; ack; quiet hours digest; config hot-reload incl. invalid file (PM-04); draft → approve flow incl. approval race (PM-06); budget invariants under `proc-churn-300` (RB-03); suspend/resume gap (SA-07); daemon kill -9 mid-cycle → restart → **at most one duplicate notification** (NO-04), no lost committed notifications, cursor-correct event resume (DM-15), no DB corruption (WAL).
- **TS-06** MCP is tested end-to-end by driving `ftmon mcp` over stdio with recorded tool-call sequences (including a scripted "AI authors a monitor with two validation errors then a correct one" flow exercising MC-03/MC-04, and a resource fetch per MC-05).
- **TS-07** Web UI: HTTP-level tests for every page and POST (UI-03) against a fixture-populated DB; HTML assertions on data presence and escaping (SE-02); UI-08 hardening tests (bad Host → 400, missing/foreign Origin on POST → rejected); UI-09 checks that severity markup carries text labels.

### 16.5 Tier-2 (opt-in, real system)

- **TS-08** Marked `@pytest.mark.realsystem`, excluded from CI default: daemon starts under systemd user unit, samples real psutil ≥ 3 cycles, journald reader ingests a `logger`-injected marker event and resumes across a daemon restart via cursor, notify-send fires (assert via `notifications.jsonl` + non-fatal check of desktop), CLI/status/web respond, `ftmon doctor` clean, teardown cleans state.

---

## 17. Documentation deliverables (v1)

- **DO-01** `docs/definitions.md`: complete monitor-definition reference (schema, every function with examples, the EX-06 truth table, cookbook: "watch this log pattern", "alert when X grows"). Written to be pasted into an AI context and exposed as the MCP resource (MC-05) — the primary consumer is `define_monitor` authors, human or model.
- **DO-02** `docs/install.md`: uv install, `ftmon init`, systemd unit, MCP client registration snippet (Claude Code/Desktop), web UI.
- **DO-03** Man-page-style `--help` for every CLI subcommand.

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
| OPEN-7 | License | **RESOLVED v0.2**: new code MIT; legacy subtree stays GPLv2 (§3) |

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

---

## 21. Changelog & review disposition

**v0.3 (2026-07-10)** — design-phase capacity amendments (DESIGN.md §9 worksheet, per DM-16): DM-04 hourly-rollup retention split (400 d durable series / 90 d process series); DM-09 event store-filter (severity ≥ notice or rule-matching; full journal volume cannot fit DM-05). No other changes.

**v0.2 (2026-07-10)** — incorporates the external review (`CODEX-SPEC-REVIEW.md`). Accepted and specified: ladder-group incident model (IN-03, owner decision); episode semantics + msg_hash for event rules (§7.7.3, IN-08); expression-language reconciliation (severity constants, no kwargs, EX-06 truth table, numeric/regex edges, derived-metric ordering); TOML example completed (`schema`, `enabled`, integer `version`, `source_options`) and MD-07 built-ins-must-validate gate; source-once-per-tick pipeline (SA-06) and honest timeout semantics (SA-02); capacity worksheet + degradation order + caps (DM-16, DM-05, DM-03, DM-13, CA-04); event cursor/queue/durability (DM-15, SA-08); config-file coordination (PM-06, PM-07); notification outbox with explicit at-least-once bound (NO-04, DM-14); reproducible baseline algorithm (CA-05 = EW mean, half-life 3 d); privacy posture (SE-04, owner decision: collect truncated); loopback hardening (UI-08); clock discipline (SA-07); entity disappearance (CA-08); removal/rename semantics (MD-09); `self` as explicit eighth built-in (§7.7); `ftmon doctor` + backup-API-only backups (CL-05, VC-03); accessibility (UI-09); delivery milestones (§20). Owner decisions this round: ladder groups; **MIT license**; cmdline collect-truncated; adopt reviewer positions on OPEN-2..6; OPEN-1 defaults accepted as-shipped (tunable in installed files). Deliberately rejected/deferred: baseline seasonality (NG-07), secret-pattern redaction (NG-08), SSE (UI-04), per-process net attribution (NG-06).

**v0.1 (2026-07-10)** — initial draft from grilling rounds 1–3.

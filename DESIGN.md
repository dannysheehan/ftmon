# FTMON v2 — Design

Status: **DRAFT v0.36**. Companion to `SPEC.md` v0.54 — every design element
cites the requirement(s) it satisfies. Where this document says FROZEN,
implementers MUST NOT alter names, signatures, or semantics; changes go through
this document first.

Design-phase artifacts:

- this document;
- `design/builtins/*.toml` — the eight built-in monitor definitions (MD-07), normative;
- two SPEC amendments recorded in SPEC §21 (v0.3): hourly-rollup retention split and event store-filter, both forced by the capacity worksheet (§9 here).

`TESTPLAN.md` and per-milestone work packages are the next phase and build on §16.

## Process overview

FTMON runs as separate processes on one host. The daemon owns sampling and
writes; CLI, web, and MCP share the SQLite store:

```mermaid
flowchart TB
    subgraph processes [Separate processes]
        CLI[CLI]
        DAEMON[Daemon]
        WEB[Web UI]
        MCP[MCP server]
    end

    subgraph engine [Monitoring engine]
        SCHED[Scheduler]
        PIPE[Pipeline]
        INC[Incident engine]
        WRITER[Store writer]
    end

    subgraph io [I/O adapters]
        SRC[Samplers and event sources]
        CHK[External checks]
        STORE[(SQLite)]
        NOTIFY[Notifications]
    end

    CLI --> STORE
    WEB --> STORE
    MCP --> STORE
    DAEMON --> SCHED
    SCHED --> SRC
    SCHED --> CHK
    SCHED --> PIPE
    PIPE --> INC
    INC --> WRITER
    WRITER --> STORE
    DAEMON --> NOTIFY
```

---

## 1. Repository and package layout

```
PROJECTS/ftmon/                  # monorepo root (git)
├── .ai/skills/                  # canonical portable contribution skills (AS-*)
│   └── ftmon-add-extra-monitor/{SKILL.md,agents/openai.yaml}
├── SPEC.md  DESIGN.md  LICENSE   # LICENSE is MIT
├── pyproject.toml  uv.lock      # single Python project at repo root
├── design/
│   └── builtins/*.toml          # normative built-in defs; copied into package data by WP
├── extra-monitors/              # articles + testable external-check recipes (XR-*)
│   ├── _template/
│   └── <recipe>/{README.md,recipe.toml,checks.toml.example,monitor.toml,fixtures/}
├── exchange/                    # static templates/assets; never generated output
├── src/ftmon/                   # the package (SPEC §3)
│   ├── __main__.py              # `python -m ftmon` entry; delegates to cli
│   ├── paths.py                 # FS-01: all filesystem paths (platformdirs)
│   ├── config.py                # config.toml load/validate/defaults (FS-02)
│   ├── clock.py                 # TS-03: Clock protocol + SystemClock + ControlledClock
│   ├── model.py                 # §4 core dataclasses (FROZEN)
│   ├── expr/                    # EX-04: stdlib-only, imports nothing from ftmon.*
│   │   └── parse.py  ir.py  eval.py  functions.py  tribool.py
│   ├── definitions/
│   │   ├── schema.py            # MD-01 validator (single source of truth)
│   │   ├── loader.py            # TOML → MonitorDef, normalization, topo-sort (MD-08)
│   │   ├── manage.py            # MD-05 approve/enable/disable/rescan operations
│   │   └── builtins/*.toml      # package data, installed by `ftmon init` (FS-02)
│   ├── sources/
│   │   ├── base.py              # Sampler/EventSource protocols + SourceDecl (PL-05)
│   │   ├── process.py disk.py system.py net.py unit.py
│   │   ├── journald.py oslog.py win_evtlog.py   # per-platform EventSource (PL-01)
│   │   ├── repeats.py           # DM-08 repeat collapsing for event sources
│   │   └── fixtures.py          # TS-04 scenario-driven fakes (ship in prod pkg: PL-04)
│   ├── checks/
│   │   ├── registry.py          # administrator argv authority + reload (EC-01/06)
│   │   ├── trust.py             # EC-01/SE-07 executable trust predicate
│   │   ├── runner.py            # no-shell process-group deadline (EC-02)
│   │   ├── sampler.py           # fair alias execution + declared projection (EC-04/08)
│   │   ├── model.py  text.py    # check result types + bounded output handling
│   │   └── nagios.py jsoncheck.py # strict output adapters (EC-03/04/10)
│   ├── engine/
│   │   ├── scheduler.py         # SA-01 tick loop
│   │   ├── pipeline.py          # SA-06 source→snapshot→project→derive→rules
│   │   ├── context.py           # per-entity evaluation context handed to expr
│   │   ├── rings.py             # CA-04 ring buffers
│   │   ├── incidents.py         # IN-06 pure state machine (FROZEN)
│   │   ├── episodes.py          # IN-* episode grouping over incident transitions
│   │   ├── events.py            # DM-07/08 event ingest filtering
│   │   ├── render.py            # message/template rendering for notifications
│   │   ├── actions.py           # AC-* action execution
│   │   └── effects.py           # effect executor: outbox, actions (AC-*), notify dispatch
│   ├── store/
│   │   ├── db.py                # connection factory, pragmas, migrations runner
│   │   ├── migrations/*.sql     # numbered, gated by PRAGMA user_version
│   │   ├── writer.py            # daemon-side batched writes
│   │   ├── query.py             # DM-06 tier-transparent reads (shared by CLI/MCP/web)
│   │   ├── retention.py         # DM-04/05 rollups, prune, vacuum, and CA-05 baselines
│   │   ├── doctor.py            # CL-05 diagnostics + VC-03 backup
│   │   └── outbox.py            # NO-04 durable queue + DispatchWorker (PM-12)
│   ├── notify/                  # adapters + per-channel delivery state
│   │   ├── base.py file.py ntfy.py webhook.py smtp.py http.py
│   │   └── desktop.py osascript.py toast.py   # per-platform desktop (PL-01)
│   ├── recipes/
│   │   └── catalogue.py install.py            # XR-* curated recipe listing/install
│   ├── web/                     # §14: operational + isolated demo factories
│   │   └── app.py demo_app.py   # operational app; isolated synthetic demo app
│   ├── daemon.py                # composition root; owns the only bulk-write connection
│   ├── glance.py                # UI-04/14/17/18 read-side policy shared by web + MCP
│   ├── selfmon.py               # RB-02 self metrics + the `self` SelfSampler source
│   ├── mcp_server.py            # §13
│   ├── demo.py                  # seeded synthetic DB builder (UI-15/16)
│   ├── systemd/                 # user unit + hardened server system unit
│   ├── launchd/                 # macOS LaunchAgent templates (PL-01)
│   ├── windows/                 # Task Scheduler helpers (PL-01 / #94)
│   ├── deploy/                  # deployment templates
│   ├── scenarios/               # TS-04 fixture scenarios
│   └── cli.py                   # §15 argparse tree, every subcommand
├── packaging/windows/           # PyInstaller spec, WiX v7 MSI, pins (#95)
├── tests/                       # §16; mirrors src layout + e2e/ + scenarios/
├── tools/gen_reqindex.py        # TS-01 traceability index generator
├── tools/build_exchange.py      # deterministic, inert catalogue publisher
├── tools/windows/               # freeze/MSI/sign/smoke helpers (#95)
└── docs/                        # definitions.md install.md manual.md + records (DO-09)
```

Every `src/ftmon/**/*.py` module appears above, excluding `__init__.py`. A
lint test asserts both directions — no path here that does not exist, and no
shipped module missing from here — because this map is what contributors and
agents are told to consult before changing code, so silent drift misroutes
work (issue #121).


Windows service-wrapper rationale: Task Scheduler logon tasks are registered
by the operator-facing `Install-FTMONTasks.ps1` after `ftmon init`, never by
MSI custom actions. The daemon task is default; persistent web is opt-in
(`-IncludeWeb`). Registration never auto-starts processes. The frozen MSI
installs under `%LOCALAPPDATA%\Programs\FTMON` (per-user, no elevation) and
adds only that directory to the user PATH; state remains in platformdirs.
WiX Toolset v7 build tooling is used under OSMF v1.1 terms recorded in
`packaging/windows/README.md`.

The original GPLv2 Perl tree remains at
<https://sourceforge.net/projects/ftmon/>. Reused Nagios/Monitoring plugins are
also separately installed external programs. Keeping both out of the MIT
package makes the licensing boundary obvious to users, packagers, and automated
license scanners while retaining an authoritative historical reference.

Layering rule (enforced by a lint test): `expr` imports only stdlib; `model` imports stdlib (+`expr.tribool`); `sources`, `store`, `engine` import `model`/`expr` but never each other except `engine → sources.base`; `daemon`/`mcp_server`/`web`/`cli` are the only modules that may import across the board. No module imports `daemon`.

### 1.1 Dependencies (runtime, pinned by uv.lock)

| Package | Why | Notes |
| --- | --- | --- |
| psutil | samplers | the entire PRECALCS layer |
| platformdirs | FS-01 | |
| mcp | §13 server | official SDK, `mcp.server.fastmcp.FastMCP`, stdio |
| starlette + uvicorn | web UI | small ASGI; no FastAPI (no pydantic needed) |
| jinja2 | web templates | autoescape on (SE-02) |
| tomli-w | writing drafts/normalized TOML | reads use stdlib `tomllib` |

Dev: pytest, pytest-timeout, hypothesis, ruff. Vendored static (MIT/BSD, checked in under `web/static/vendor/`): htmx (~14 kB), uPlot (~50 kB) — chosen as the smallest chart library that renders 2 000-point series fast and lets us attach the UI-09 text alternatives ourselves.

Stdlib bias everywhere else: `argparse` (CLI), `sqlite3`, `tomllib`, `hashlib`, `json`.
Remote notification delivery also stays stdlib-only: `urllib.request` + `ssl`
for bounded HTTPS calls and `smtplib` + `email.message` for SMTP. Avoiding a
general notification framework keeps the supported security and retry surface
small; every adapter is contract-tested against local fakes (NO-05, TS-13).

---

## 2. Runtime composition

```
ftmon daemon ──► Scheduler(clock)
                   │ per tick (5 s monotonic):
                   │ 1. drain EventSources → event pipeline (§11)
                   │ 2. for each due monitor: run pipeline (§10) using shared snapshots
                   │ 3. incident engine step → effects → outbox/actions
                   │ 4. writer.flush()  (ONE write txn per tick, PM-03)
                   │ 5. retention slice (≤1 s, DM-04) ; self metrics (RB-02)
                   └─ ControlledClock hook for tier-1 e2e (TS-05)

ftmon mcp / web / CLI ──► store.query (read) + small-write helpers (ack/approve/draft)
```

The daemon's sampling and incident pipeline is synchronous and single-threaded.
Each `EventSource` owns one reader subprocess/thread that only moves lines into
a bounded deque (SA-08). M8 adds one notification-dispatch thread with its own
SQLite connection; it claims durable delivery rows and performs potentially
slow network I/O wholly outside sampling transactions. Tests replace both
threaded boundaries with synchronous fakes, preserving deterministic control.

Confirm/clear counters (IN-01) are in-memory only; a daemon restart loses in-progress confirmation and re-accumulates (documented, acceptable — incidents and backoff state survive via DB per IN-02/DM-14).

### 2.1 Single-server deployment (PM-08/09)

`ftmon init --profile server` and the packaged `ftmon-server.service` target a
dedicated `ftmon` account with a real, non-login home/state directory. The unit
uses `User=ftmon`, `Group=ftmon`, `NoNewPrivileges=true`, `PrivateTmp=true`,
`ProtectSystem=strict`, `ProtectHome=read-only`, and explicit `ReadWritePaths`
for FTMON's config/data/state directories. `ProtectProc=invisible` is deliberately
not set: hiding other users' `/proc` entries would make process monitoring lie.
The account gains no supplementary groups by default and the service never uses
ambient capabilities. Administrators who need journal coverage grant the
narrow platform group/read ACL themselves and accept that visibility trade-off.
M9 provides `Environment=FTMON_CHECK_REGISTRY=/etc/ftmon/checks.toml`; the unit
does not add `/etc/ftmon` to `ReadWritePaths`. Packaging and real-system tests
assert both facts because application-level “MCP cannot edit this file” is not
a sufficient command-execution boundary on a server (FS-03, EC-01, SE-07).

The operational dashboard still listens on `127.0.0.1:8420`. The documented
remote path is `ssh -L 8420:127.0.0.1:8420 host`; a reverse proxy does not make
the unauthenticated operational UI safe. Desktop user units remain the default
for workstations and are not replaced by the server unit.

---

## 3. Filesystem & configuration (FS-01, PM-06)

`paths.py` exposes a frozen `Paths` dataclass built once from `platformdirs` + `$FTMON_*` env overrides (tests use temp dirs via env). M8 extends the explicit
`config.toml` shape as follows (PM-08, NO-05..10, SE-05):

```toml
[daemon]
tick_seconds = 5
gone_grace = "5m"

[privacy]
collect_cmdline = true

[quiet_hours]
enabled = false
start = "22:30"
end = "07:30"

[web]
port = 8420

[notify.desktop]
enabled = true
min_severity = "info"

[notify.ntfy]
enabled = false
min_severity = "warning"
base_url = "https://ntfy.sh"
topic = "ftmon-hostname"
token_env = "FTMON_NTFY_TOKEN" # or token_file, exactly one when enabled

[notify.webhook]
enabled = false
min_severity = "warning"
url_env = "FTMON_WEBHOOK_URL"  # or url_file; URL often embeds a secret

[notify.smtp]
enabled = false
min_severity = "warning"
host = "smtp.example.net"
port = 587
tls = "starttls"               # starttls | implicit
username = "ftmon@example.net"
password_env = "FTMON_SMTP_PASSWORD" # or password_file
from = "ftmon@example.net"
to = ["operator@example.net"]

```

The file audit channel is mandatory and therefore has no enable switch. An
`*_file` contains only the secret, is opened without following symlinks, must be
owned by the service account and not group/world-readable, and has surrounding
ASCII whitespace stripped. Environment and file forms are mutually exclusive.
Literal `token`, `password`, or webhook `url` keys are rejected rather than
deprecated, because silently accepting them would defeat SE-05. The generated
desktop variants write desktop enabled; server variants write it disabled.
Before that scaffold is written, generic `desktop`/`server` aliases resolve to
`windesktop`/`winserver` on Windows and `macdesktop`/`macserver` on Darwin;
omitting the option is the generic desktop case.
`cli.py::_PROFILE_CALIBRATED_DIRS` then maps profile name to calibrated-tree
subdirectory: Linux `desktop` → `profile/desktop` (real
GNOME host-tuning data, `docs/tuning-desktop-xps15.md`); `windesktop` and
`winserver` both → `profile/windows`, one shared tree since the fixes it
carries are OS-semantic (dead rules removed because the metrics they key on
can never exist on Windows), not a desktop-vs-server tuning distinction —
there is no Windows tuning data to justify two separate trees. The remaining
Linux `server` profile falls through to the normative, Linux-only uncalibrated
`design/builtins` set. Profile effects are visible
text in the generated file and disappear as a runtime concept after
initialization.

M9 provides `Paths.check_registry_file`. It defaults to private
`config_dir/checks.toml` for the desktop/single-user trust model. The hardened
server unit sets `FTMON_CHECK_REGISTRY=/etc/ftmon/checks.toml`; that root-owned
file and its parent remain outside `ReadWritePaths`, so compromising the daemon
cannot grant a new command while monitor/draft management remains writable.
The separate file contains:

```toml
[check.website_https]
argv = ["/usr/lib/nagios/plugins/check_http", "-H", "example.org", "-S", "--sni", "-E"]
protocol = "nagios"
timeout = "10s"
```

`checks.registry.load(path)` first lstat/checks the registry and its parent
chain per EC-01, then validates the whole `[check]` table before
publishing an immutable `CheckRegistry`. Registry aliases use the monitor-name
syntax; `argv` is 1–32 strings (combined UTF-8 ≤ 8 KiB), its first element is an
absolute path, and timeout is 1–30 s. Validation opens the executable with
`lstat`, rejects symlinks/non-regular/non-executable or group/world-writable
files and paths under data/state/runtime, and records only a stable readiness
category. The registry object, not raw TOML, is passed to `ExternalSampler`.
Config reload swaps the complete object only after validation; failure retains
the previous object (EC-01/06/08, SE-07).

`checks/trust.py` is the single evaluator behind all of this — ownership
(`trusted_owner`/`owned_by_self`) and writability
(`writable_beyond_owner`/`accessible_beyond_owner`) each have one
implementation shared by the registry loader, the external-check runner's
pre-launch revalidation, config.py's SE-04 secret-credential-file check, and
web/demo_app.py's SE-06 demo-database check — a second copy of the predicate
anywhere would let one caller's notion of "trusted" drift from another's,
exactly the TOCTOU-adjacent failure SE-07 calls out. On POSIX this is the
familiar `uid in {0, os.geteuid()}` plus `st_mode & (S_IWGRP|S_IWOTH)`. On
Windows, which has neither a real uid (`os.stat().st_uid` is always 0 there)
nor meaningful mode bits (`st_mode`'s write bits are a fixed synthesized
value, not real permissions), the same two questions are answered with the
Win32 security APIs instead: `trusted_owner` compares the file's owner SID
(`GetFileSecurity` + `GetSecurityDescriptorOwner`) against the current
process token's user SID (`OpenProcessToken` + `GetTokenInformation`,
`TokenUser`), treating the well-known SYSTEM and Administrators SIDs
(`CreateWellKnownSid`) as the Windows analogue of POSIX root; the
writability checks walk the file's DACL (`GetSecurityDescriptorDacl`/
`GetAce`) and fail if any `ACCESS_ALLOWED` entry grants a write-capable (or,
for the stricter secrets check, any) right to a trustee outside that same
owner/SYSTEM/Administrators set. An unreadable, absent, or NULL DACL fails
closed; Windows grants everyone full access when no DACL restricts it. Secret
credential checks obtain the same descriptor with `GetSecurityInfo` from the
CRT fd's already-open OS handle, so the safe open is not undone by a second
path lookup. The Linux-only `masked_system_executable` escape hatch (NoNewPrivileges masking
distro plugin ownership to an overflow uid) has no Windows counterpart —
it is a narrow systemd sandboxing workaround, not a general rule.

There is deliberately no environment table. A generic secret-to-environment
feature would make process output, diagnostics and third-party behavior part of
FTMON's secret boundary. A plugin that needs credentials receives the path to
its own administrator-managed protected file as a non-secret argv value; its
format, ownership and lifecycle remain that plugin's responsibility (EC-07).

Atomic write helper `paths.atomic_write(path, bytes)` (tmp + fsync + rename,
0600) is the only function that writes into the config tree (PM-06a/b); loader
rejects symlinks (PM-06c). On Windows, private permission setup opens the
file/directory itself with `FILE_FLAG_OPEN_REPARSE_POINT` (and
`FILE_FLAG_BACKUP_SEMANTICS` for directory support), rejects the reparse
attribute, and calls `SetSecurityInfo` on that handle. `Paths.ensure` and
`atomic_write` therefore stop before a junction can redirect their DACL or
content mutation.

---

## 4. Core types (`model.py`) — FROZEN

```python
class TriBool(Enum): TRUE; FALSE; UNKNOWN          # expr/tribool.py, re-exported

@dataclass(frozen=True) class MetricDecl:  name: str; unit: str; kind: Literal["gauge","counter"]; doc: str
@dataclass(frozen=True) class AttrDecl:    name: str; doc: str
@dataclass(frozen=True) class SourceDecl:  # PL-05
    name: str; kind: Literal["sampler","events"]; entity_kind: str
    metrics: tuple[MetricDecl, ...]; attrs: tuple[AttrDecl, ...]

@dataclass(frozen=True) class EntitySample:
    entity_id: str; attrs: Mapping[str, str]; metrics: Mapping[str, float]
    synthetic: bool = False            # v0.29: CA-08 watchlist provenance
@dataclass(frozen=True) class Snapshot:            # SA-06: one ts for all entities
    source: str; ts: float; entities: tuple[EntitySample, ...]

@dataclass(frozen=True) class EventRecord:          # DM-07/08
    ts: float; ingest_ts: float; source: str; provider: str
    event_id: str | None; severity: int; message: str; attrs: Mapping[str, str]

@dataclass(frozen=True) class Notification:        # NO-01
    incident_id: int; kind: Literal["open","escalate","renotify","recover","digest"]
    severity: int; monitor: str; entity_id: str
    title: str; body: str; created_ts: float

# Incident engine I/O (§10.4)
@dataclass(frozen=True) class RungState:   confirmed: bool; confirm_count: int; clear_count: int
@dataclass(frozen=True) class IncidentCore:
    incident_id: int | None; state: Literal["open","acked","cleared"]
    severity: int; owning_rule: str; opened_ts: float
    last_notify_ts: float | None; notify_count: int
    backoff_tier: int; flap_clears: tuple[float, ...]; occurrences: int
@dataclass(frozen=True) class GroupState:  rungs: Mapping[str, RungState]; core: IncidentCore | None

Effect = NotifyEffect(Notification) | ActionEffect(action: str, env: Mapping[str,str]) \
       | RecordEffect(kind: str, detail: Mapping) | PersistEffect(...)   # tagged union via dataclasses
```

**v0.35 (issue #106): the aggregate sampling counter has a closed
decomposition.** `SAMPLER_SOURCE_NAMES` is the compile-time vocabulary:
`process, disk, system, net, unit, self, external`. `Pipeline` charges each
SA-06 cache miss to the aggregate and exactly one source accumulator; cache
hits add neither. `DaemonCore` advances the aggregate and all seven cumulative
self counters together on both the successful and PM-10 locked-commit exits.
`SelfSampler` emits only the seven declared names, so a monitor, external alias,
or plugin can never widen the metric namespace. Their deltas reconcile to the
aggregate within floating-point precision.

The external bucket measures `ExternalSampler.sample()` projection, matching
the boundary of `sampling_seconds_total`. Alias preparation/execution precedes
the monitor loop, is outside both the aggregate and its decomposition, and
retains EC-02's separate deadline. Moving that phase into this family would be
a different stage-boundary decision, not an attribution fix. `/self` labels
that child `external projection`, displays explicit parent relationships, and
derives every visible rate from one pair of timestamps common to the available
counters. The common span prevents older aggregate history from being compared
with child metrics that began only after an upgrade.

**v0.36 (issue #143): the post-sample half of the tick has a closed
decomposition too.** `PIPELINE_PHASES` is the compile-time vocabulary:
`ingest, derived, exempt, rules, persist`. `Pipeline` times each span within
`run_monitor` and the five accumulate to exactly `evaluate_s` — the sample's own
cost is subtracted from `ingest` rather than left straddling two buckets, and
`evaluate_s` is derived from the same final reading the phases use, so the
partition holds by construction rather than to a tolerance.

`exempt` and `rules` share one pass over entities, so their boundary is the only
one needing a per-entity reading. At a measured 101 ns per `monotonic()` call
that is ~0.02% of one core worst case (520 entities x 5 process monitors x 12
ticks/min) against the ~1.5% the instrument exists to explain; every other
boundary costs two readings per monitor. Building `EntityCtx` is charged to
`exempt` because that is what consumes it first, and per-entity loop overhead
the two readings do not cover is charged to `rules` rather than an
"other" bucket, which would break the exact partition.

Keeping `persist` distinct is the point: `_persist` and `_track_gone` run inside
`evaluate_s`, so growth there means catalog/SQLite pressure while growth in
`rules`/`exempt` means an in-memory walk. `/self` renders the five beneath
`pipeline` with the same explicit parent relationships as the sampling sources.

**v0.32 (issue #106): stage costs are cumulative counters, not last-tick
gauges.** v0.31's gauges were correct per tick and useless in practice. The
self monitor samples every 60 s while ticks run every 5 s, and the self
*sampler* runs inside the monitor loop, so each sample reports the previous
tick. That works only for stages every tick performs. Measured on the canary
over ten samples: `commit` and `actions_outbox` read non-zero six times, while
`sampling`, `pipeline` and `retention` read zero **ten** times out of ten —
retention runs once per twelve ticks, and the tick before a self-due tick
rarely has any monitor due.

The shape, not the measurement, was wrong. `*_seconds_total` counters make
utilization the derived quantity it always was: `delta(counter) / elapsed
wall`, ×100 for percent of one core. That survives sparse stages, tolerates a
missed sample, needs no read-and-reset state, and makes the one-tick lag
harmless because every sample observes all work completed so far. Restarts are
handled by the existing counter-reset semantics — a negative delta means the
daemon restarted and is reported as unavailable rather than as a spike.

`cycle_s` stays a last-completed-tick gauge: every tick has one, so the
reading is always meaningful. `prune_seconds_total` and `reap_seconds_total`
remain **subcomponents** of `retention_seconds_total`, never additive peers.
`/self` renders rates over a stated window rather than the cumulative totals,
which answer no operator question on their own.

**v0.30 → v0.31 (issue #106): the tick breakdown is seven fixed gauges.**
DESIGN previously described `sampler_s{per-source attr}`, which was never
implemented. That *representation* is incompatible with the current metric
model — metrics carry no attribute dimension, and an attribute-shaped series
would make the self entity's series count vary with the sources a host runs
(DM-16). The requirement itself is not the problem: the source registry is
finite, so per-source duration could be expressed as bounded fixed gauges
(`sampling_disk_s`, `sampling_net_s`, …) whenever it is implemented.

What this pass delivers is the aggregate stage measurement: `sampling_s`,
`pipeline_s`, `commit_s`, `actions_outbox_s`, `retention_s`, and within that
pass `prune_s` and `reap_s`, which are **subcomponents of `retention_s`**
rather than additional stages. `sampling_s` covers every SA-06 shared sample,
not only the process source; external check *preparation* runs before the
monitor loop and is outside it. All seven are declared in
`SOURCE_DECLS["self"]` (PL-05) and measured on the injected Clock (TS-03).

At v0.32, **RB-02's per-source-duration clause remained outstanding**, tracked
in #106. `sampling_s` was only an aggregate and did not satisfy it. v0.35 adds
the fixed decomposition above rather than weakening the requirement to accept
an aggregate.

**v0.29 (issue #119): `EntitySample.synthetic` — sources report provenance,
the pipeline owns retention policy.** DM-04 grants the durable window to
"system, disk, self, **and watchlist-synthetic entities**", but durability was
derived per monitor from `_DURABLE_SOURCES`, so `unit` and `net` watchlist
entities were stored non-durable and given the process windows. The last
clause of DM-04 cannot be expressed per monitor: `net` emits a synthetic
listener watchlist beside a discovered `totals`, so one monitor holds both
kinds and a blanket source add would simply mislabel `totals` instead.

The field carries a *fact* the source alone knows — this entity was
synthesized from a validated `source_options.watchlist` entry — and never a
retention decision. `Pipeline._persist` applies the DM-04 policy
(`monitor_durable or ent.synthetic`), so a source can never widen its own
retention and the policy stays in one place. Defaulted `False`, so every
discovered entity and every existing source is unaffected.

This changes the frozen interface, not product semantics: DM-04 already
required the behaviour, which is why DESIGN remains companion to SPEC v0.50
rather than forcing a SPEC bump.

---

## 5. Interfaces — FROZEN

```python
class Clock(Protocol):                              # TS-03
    def now(self) -> float: ...                     # wall, UTC epoch seconds
    def monotonic(self) -> float: ...
    def sleep_until(self, mono_deadline: float) -> None: ...

class Sampler(Protocol):                            # PL-01
    decl: ClassVar[SourceDecl]
    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot: ...
    # now = wall ts to stamp on the Snapshot (samplers never read clocks, TS-03);
    # deadline is cooperative for in-process samplers, hard (kill) for subprocess ones (SA-02)

class DynamicSampler(Protocol):                     # EC-04/05 amendment to PL-05
    def declaration(self, options: Mapping) -> SourceDecl: ...
    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot: ...
    # external is the sole dynamic implementation; declaration() is composed
    # from fixed plugin_* fields plus validated mappings before expressions compile

class EventSource(Protocol):                        # PL-01, DM-15
    decl: ClassVar[SourceDecl]
    def start(self, cursor: str | None) -> None: ...
    def drain(self, max_items: int) -> tuple[list[EventRecord], str | None]: ...  # (events, new_cursor)
    def alive(self) -> bool: ...
    def stop(self) -> None: ...

class Notifier(Protocol):                           # NO-02
    def deliver(self, n: Notification) -> None: ... # raises NotifyError on failure

# expr — the security boundary (EX-01..07)
def compile_expr(text: str, names: NameEnv) -> CompiledExpr      # raises ExprSyntaxError/ExprNameError
class CompiledExpr:
    windows: tuple[tuple[str, float], ...]          # (metric, seconds) — feeds CA-04 sizing
    def eval(self, ctx: EvalContext) -> float | str | TriBool | None: ...   # NEVER raises (EX-06)
class NameEnv:   # built at validation from SourceDecl + parameters (MD-04, EX-02)
class EvalContext(Protocol):
    def metric_last(self, m: str) -> float | None
    def metric_window(self, m: str, seconds: float) -> Sequence[tuple[float, float]]
    def attr(self, a: str) -> str | None
    def param(self, p: str) -> float
    def baseline(self, m: str) -> float | None
    def now(self) -> float                          # for during()/dow()/age()

# incident engine — pure (IN-06)
def step_group(cfg: GroupConfig, st: GroupState, evals: Mapping[str, TriBool],
               now: float) -> tuple[GroupState, tuple[Effect, ...]]
def step_episode(cfg: EpisodeConfig, st: EpisodeState,
                 matches: Sequence[tuple[float, str] | tuple[float, str, int]],
                 now: float) -> tuple[EpisodeState, tuple[Effect, ...]]         # IN-08/DM-20

# storage facade (all non-daemon processes use only Query + SmallWrites)
class Query:      # DM-06; shared by CLI/MCP/web
    def series(self, monitor, metric, entity=None, start=..., end=..., max_points=2000) -> SeriesResult
    def current_baseline(self, monitor, entity, metric) -> BaselineRecord | None
    def baseline_history(self, monitor, entity, metric, start=..., end=...) -> BaselineHistory | None
    def list_baselines(self, filters=..., limit=100, cursor=None) -> BaselinePage
    def top(self, resource, start, end, n) -> ...
    def events(self, filters) -> ...
    def incidents(self, filters) -> ...
    def incident_detail(self, id) -> ...            # explain_incident substrate
    def monitors(self) -> ...
    def status(self) -> StatusResult                # PM-01 liveness = age of meta.last_tick_ts
class SmallWrites:
    def ack(self, incident_id, by, note) -> None    # PM-03 short txn
```

`ControlledClock` is test-only and available only when the daemon is explicitly
started with `--clock controlled`. On Linux/macOS it preserves the
`$FTMON_CLOCK_SOCK` Unix socket; the harness creates a short temporary socket
directory and removes it during teardown. Windows uses `$FTMON_CLOCK_PORT` and
AF_INET bound strictly to `127.0.0.1`, with the harness reserving an ephemeral
loopback port. Neither transport is opened in normal daemon mode, so PM-05 and
SE-01's production listener boundary remains the loopback web UI alone. Both
transports use the identical line-JSON `{"op":"step","s":5}` /
`{"op":"set","wall":…,"mono":…}` protocol: `sleep_until` blocks on the
endpoint and the daemon replies `{"ok":true,"tick":N}` **after** completing
the tick, so harness steps remain synchronous (TS-05 determinism).

---

## 6. Expression module design (EX-01..07)

- `parse.py`: `ast.parse(text, mode="eval")`; walk with an allowlist visitor (exact node list EX-01, kwargs rejected EX-05); output is a private IR (nested frozen dataclasses) — the evaluator never touches `ast` nodes again. Regexes found in `matches()` are compiled here (EX-07) and pattern length checked.
- Name resolution (EX-02) happens at compile time against `NameEnv`; the IR stores slot kinds (`metric|attr|param|const`) so eval does no dict lookups on strings the author controls.
- `eval.py`: small recursive interpreter over the IR. All binary/unary/compare ops route through `tribool.py` helpers implementing the EX-06 truth table verbatim (one function per table row group; the unit tests mirror the table). Division/modulo by zero, NaN/inf results → `UNKNOWN` + a counter callback. A `deadline_check()` closure is consulted every N=64 IR nodes (EX-03's 10 ms cap).
- `functions.py`: the CA-01 table. Series functions take `(ctx, metric_slot, window_seconds)`; `slope` = numerically stable least squares over (t−t₀); `monot` counts consecutive positive deltas / (n−1); `coverage` = (t_newest − t_oldest)/w clamped to [0, 1] — the window parameter reaches the function for exactly this one case (every other series function only needs the points). `CompiledExpr.windows` is the union of all (metric, window) references — the loader aggregates these per monitor to size ring buffers (CA-04) and to reject > 6 h / >10 000-point windows.
- Message templates (MD-02): validated with `string.Formatter().parse`; allowed field names = same NameEnv; rendering wraps every value — `None` renders as `"n/a"` ignoring any format spec (so `{full_in_h:.1f}` never raises at fire time).

---

## 7. Definition schema and loader (MD-01/03/04/07/08, §8.1)

`schema.py` holds one declarative table (`SCHEMA: dict`) describing every key: type, bounds, required-ness, and per-source-kind applicability. Complete key inventory (normative; JSON-Schema is generated from this table for docs):

| Key | Type / bounds | Applies |
| --- | --- | --- |
| `schema` | int, == 1 (VC-02) | all |
| `monitor.name` | `[a-z][a-z0-9_]{1,31}` | all |
| `monitor.description` | str ≤ 200 | all |
| `monitor.version` | int ≥ 1 | all |
| `monitor.enabled` | bool (default true) | all |
| `monitor.platforms` | subset {linux,windows,darwin} | all |
| `monitor.interval` | duration ≥ "15s" (SA-01) | sampler sources |
| `monitor.source` | name of a registered source, or "events" | all |
| `source_options.watchlist` | array of tables: `{unit=…}` \| `{process=regex}` \| `{listen="tcp:22"}` + optional `during`, `expected=bool` | service, net |
| `source_options.top_n` | int 5..50 (default 15, SA-05) | process |
| `source_options.check` | registered alias | external |
| `source_options.entity` | stable non-empty string ≤ 256 | external |
| `source_options.perfdata[]` | `{label, metric, plugin_uom, unit, kind, scale?}`; ≤32, unique labels/metrics | external |
| `source_options.channels[]` | `{path, query?}`; path required + unique ≤256, query optional ≤2048 (MD-13, DM-19) | events |
| `source_options.store_min_severity` | severity name or int 0-4 (default notice, DM-09) | events |
| `parameters.*` | `{value: num, doc: str}` | all |
| `promotion.expr` | expression (bool) — SA-05(c) heuristic | process |
| `derived[].name/expr` | metric name / expression | sampler sources |
| `glance` | `{metric, unit, aggregate=max|min, thresholds=[{label, parameter}]}`; ≤4 thresholds (MD-12) | sampler sources |
| `trend[]` | declarative value/rate/confidence/projection presentation profile (MD-10) | sampler sources |
| `exempt[]` | expression (bool) over entity ns (CA-07) | sampler sources |
| `rule[].id` | `[a-z0-9-]{1,32}`, unique in monitor | all |
| `rule[].group` | id-syntax; default = rule id (IN-03) | sampler sources |
| `rule[].when` | expression (bool) | all |
| `rule[].severity` | notice\|warning\|error\|critical | all |
| `rule[].confirm_cycles` | int 1..60 (default 1) | sampler rules |
| `rule[].clear_cycles` | int 1..60 (default = confirm) | sampler rules |
| `rule[].message` | template ≤ 200 rendered (NO-01) | all |
| `rule[].action` | bare filename in actions/ (AC-01) | all |
| `rule[].cooldown` | duration (default "10m") | event rules only (IN-08) |
| `rule[].clear_after` | duration (default "30m") | event rules only |
| `rule[].confirm_count` / `confirm_window` | int ≥1 / duration | event rules only |
| `rule[].notify_recovery` | bool (default: false for event rules, true otherwise, IN-04) | all |

Event-rule namespace: `severity, provider, event_id, message, source` + parameters (§7.7.3). Loader pipeline: `tomllib` → schema table check (unknown key = error with dotted path, MD-03) → NameEnv build from `SourceDecl` (PL-05) → compile every expression/template (MD-04 suggestions via `difflib.get_close_matches`) → topo-sort derived (MD-08) → aggregate windows (CA-04) → `MonitorDef` (frozen) + normalized TOML (tomli-w, sorted keys) + SHA-256 hash (PM-04/07).

Registered sources through M9: `process, disk, system, net, unit, self, events,
external`. The `self` source is registered like any other (RB-02). `external`
is the only `DynamicSampler`: `schema.external_decl(mappings)` starts with
`plugin_state, plugin_ok, duration_s` and `plugin_message`, appends mapped
`MetricDecl`s, then builds the NameEnv used by derived/rule/trend validation.
The runtime adapter receives that same frozen mapping tuple, preventing loader
and sampler schema drift (PL-05, MD-11, EC-04/05).

---

## 8. SQLite schema (DDL v1)

Pragmas at open: `journal_mode=WAL, synchronous=NORMAL, foreign_keys=ON, busy_timeout=5000`; DB created with `auto_vacuum=INCREMENTAL` (DM-05). Migrations: numbered SQL files, `PRAGMA user_version` gate, pre-migration backup via backup API (VC-01).

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
-- keys: schema_hash, last_tick_ts, last_tick_mono, rollup5m_cursor, rollup1h_cursor, db_budget_state

CREATE TABLE series(
  id INTEGER PRIMARY KEY, monitor TEXT NOT NULL, entity_id TEXT NOT NULL,
  metric TEXT NOT NULL, durable INTEGER NOT NULL,            -- §9: 1 = system/disk/self/watchlist
  UNIQUE(monitor, entity_id, metric));

CREATE TABLE samples(   series_id INTEGER NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
  PRIMARY KEY(series_id, ts)) WITHOUT ROWID;                 -- DM-01; ~35 B/row effective
CREATE TABLE rollup5m(  series_id INTEGER NOT NULL, bucket INTEGER NOT NULL,
  avg REAL, min REAL, max REAL, last REAL, cnt INTEGER,
  PRIMARY KEY(series_id, bucket)) WITHOUT ROWID;             -- DM-04
CREATE TABLE rollup1h(  LIKE rollup5m );                     -- (spelled out in migration)

CREATE TABLE entities(  monitor TEXT, entity_id TEXT, first_seen INT, last_seen INT,
  gone_ts INT, attrs TEXT CHECK(length(attrs) <= 4096),      -- DM-03, CA-08
  PRIMARY KEY(monitor, entity_id)) WITHOUT ROWID;

CREATE TABLE events(    id INTEGER PRIMARY KEY,              -- id = ingest order (DM-15)
  ts INT, ingest_ts INT, source TEXT, provider TEXT, event_id TEXT,
  severity INT, message TEXT, attrs TEXT);
CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_prov ON events(provider, severity);

CREATE TABLE incidents( id INTEGER PRIMARY KEY, monitor TEXT, grp TEXT, entity_id TEXT,
  state TEXT, severity INT, owning_rule TEXT, opened_ts INT, last_change_ts INT,
  cleared_ts INT, clear_reason TEXT, ack_by TEXT, ack_ts INT,
  notify_count INT, occurrences INT, flapping INT DEFAULT 0);        -- DM-11
CREATE UNIQUE INDEX inc_live ON incidents(monitor, grp, entity_id) WHERE state != 'cleared';
CREATE INDEX inc_state ON incidents(state, last_change_ts);

CREATE TABLE incident_history(incident_id INT, seq INT, ts INT, kind TEXT, detail TEXT,
  PRIMARY KEY(incident_id, seq)) WITHOUT ROWID;              -- DM-12/13 (cap enforced in code)

CREATE TABLE notifications(
  id INTEGER PRIMARY KEY, incident_id INT NOT NULL, kind TEXT NOT NULL,
  severity INT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  monitor TEXT NOT NULL, entity_id TEXT NOT NULL, created_ts INT NOT NULL);
                                                               -- DM-14/NO-01
CREATE TABLE notification_deliveries(
  notification_id INT NOT NULL REFERENCES notifications(id),
  channel TEXT NOT NULL, state TEXT NOT NULL,
  attempt_count INT NOT NULL DEFAULT 0, next_attempt_ts INT,
  delivered_ts INT, last_error TEXT CHECK(length(last_error) <= 512),
  PRIMARY KEY(notification_id, channel)) WITHOUT ROWID;         -- DM-18/NO-04..10
CREATE INDEX delivery_due ON notification_deliveries(next_attempt_ts)
  WHERE state = 'pending';

CREATE TABLE baselines( series_id INTEGER PRIMARY KEY, value REAL, updates INT,
  updated_bucket INT, half_life_s REAL NOT NULL DEFAULT 259200) WITHOUT ROWID;
                                                               -- CA-05
CREATE TABLE cursors(   source TEXT PRIMARY KEY, cursor TEXT, updated_ts INT) WITHOUT ROWID;
CREATE TABLE monitor_loads(monitor TEXT, loaded_ts INT, hash TEXT, normalized TEXT,
  PRIMARY KEY(monitor, loaded_ts)) WITHOUT ROWID;            -- PM-07 (keep last 20/monitor)
```

Migration `0003_notification_deliveries.sql` creates the two new tables, copies
each legacy `outbox` row into `notifications`, and creates a `file` delivery:
legacy delivered rows become `delivered`; undelivered rows become `pending`
with `next_attempt_ts=created_ts`; legacy stale rows become terminal `failed`
with the fixed redacted reason `legacy stale delivery`. It then drops the old
table. Remote deliveries are intentionally not backfilled because DM-18 freezes
the channel set when the notification is created. The normal VC-01 backup makes
this destructive table replacement recoverable.

Migration `0004_baseline_half_life.sql` adds
`half_life_s REAL NOT NULL DEFAULT 259200` to existing baseline rows. Retention
stores the effective value at the seed and treats it as immutable for that row's
lifetime: a changed value replaces the row with the new rollup as update one.
That reset is why a single coefficient can reverse every update represented by
the current row without a duplicate baseline-history table (CA-05).

Write path: `writer.py` accumulates the tick's samples/events/incident rows and
commits **one** transaction at step 4 (PM-03). Notifications and their frozen
initial delivery rows are part of that transaction (NO-04/DM-18); the dispatcher
claims and updates delivery state afterward through its own short transactions.
If `BEGIN IMMEDIATE` fails after `busy_timeout` with "database is locked"
(PM-10), `commit_tick` still clears its pending buffers, the daemon counts
`sqlite_lock_errors`, emits a self-event for the next successful tick, and
skips post-commit actions/outbox/retention for the failed tick — it must not
exit.

---

## 9. Capacity worksheet (DM-16) — and the two SPEC amendments

Planning assumptions: ≤ 400 persisted entities; active persisted series ≈
**325** (the earlier ~270 scenario, recalculated for the built-in self
monitor's 63 declared and derived series rather than its obsolete 12-series
allowance — 58 at v0.53 plus v0.54's five phase counters); 60 s intervals;
WITHOUT ROWID sample row ≈ 35 B, rollup row ≈ 45 B effective (incl.
b-tree overhead); stored events ≈ 2 000/day at ≈ 350 B. The ~10 promoted
term is a **host-wide planning estimate** for one dominant promotion monitor,
not a per-monitor allocation. The separately chosen runtime cap is ten per
monitor, so *N* monitors with promotion expressions can admit up to 10*N*
promotions. That can exceed the worksheet's ~325-series scenario; the binding
host-wide constraints remain the 400 persisted-entity budget and DM-05's used-
page budget. On the reference canary at 2026-08-14, four of five enabled
process monitors carry promotion expressions, so the per-monitor cap permits
40 promotion admissions rather than the planning estimate's ~10. Static
quantities become validation limits where the definition loader can know them;
runtime quantities are bounded and reported where they are admitted.

| Store | Rows | Size |
| --- | --- | --- |
| raw 48 h | 325 × 2 880 ≈ 0.94 M | ≈ 33 MB |
| 5-min 30 d | 325 × 288 × 30 ≈ 2.81 M | ≈ 126 MB |
| 1-h, durable series (≈ 141) × 400 d | 1.35 M | ≈ 61 MB |
| 1-h, process series × **90 d** | ≈ 0.39 M | ≈ 18 MB |
| events 30 d (filtered) | 60 k | ≈ 21 MB |
| incidents + history + misc | — | ≈ 5 MB |
| **Total before pressure degradation** | | **≈ 264 MB → DM-05 degradation is required to land < 200 MB** |

The worksheet is deliberately honest about that pressure: the static
retention maxima do not all fit simultaneously once the current self catalogue
is counted. DM-05's used-page controller shortens lower-priority retention
tiers until the database is back under budget, and `db_degrading` exposes that
compromise. The 5-min window is the trim target: 126 MB at 30 d becomes ~15 d
of 5-min data once the ~64 MB overshoot is shed.
The calculation does not silently retain the historical `self 12` allowance
after adding observability series.

Two findings forced SPEC amendments (recorded as v0.3):

1. **Hourly rollups for all series for 400 d** would cost ≈ 115 MB alone (process-entity churn). Amended DM-04: 400 d hourly retention applies to *durable* series (system, disk, self, watchlist-synthetic); process-sourced series keep 90 d hourly.
2. **Storing all journal events** (50–200 k lines/day on a desktop) would blow the budget within days. Amended DM-09: the event store-filter keeps events with severity ≥ notice **or** matching any loaded event rule; info-level non-matching events are counted (self-metric) but not stored. Configurable `store_min_severity`.

Ring-buffer RAM (CA-04): worst case all-processes window = 300 procs × 2 metrics × 15 samples × 32 B ≈ 0.3 MB; promoted/watchlist long windows: 40 series × 720 points × 32 B ≈ 0.9 MB; comfortably inside the 64 MB cap; cap exists for pathological definitions.

**Active vs. total catalog (v0.43, issue #74).** The ≤400 entity / ~325
series figures above describe *active* catalog — what's concurrently
persisted in a steady tick. They are not a cap on the *total* rows retained
in `entities`/`series`/`baselines` over time: under process churn (the
`hog`/`leak` built-ins plus broad-promotion host monitors), many more
distinct process identities pass through the top-N/promoted set over a
DM-04 retention window (30 d 5-min / 90 d process-hourly) than are active at
any one instant, and each leaves a `gone` catalog row that legitimately
outlives it until its own observations age out. Confirmed on the maintainer
workstation: ~248k `entities` rows (~246k already `gone`) against the ≤400
assumption, ~18 MB of that in `entities` alone, none of it prunable by the
observation-retention logic that existed before MD-09's reap rule (see
`retention.py`'s `_reap_catalog`). Reap makes total catalog **bounded by
DM-04's retention windows**, not convergent to the ~320/≤400 active-state
assumptions — `ftmon doctor` (CL-05) reports both counts separately rather
than treating the active-state assumption as a total-catalog budget, since
doing so would produce routine false pressure on any host with real process
churn.

**v0.27 (issue #103): reap expires the hourly tail rather than waiting for
it.** "Bounded by DM-04's retention windows" above is exactly the problem —
the bound is the *longest* window, so a catalog cannot converge faster than
90 d no matter how briefly its processes lived. Measured on the canary: 7,923
entities dead over a week were pinned by `rollup1h` alone (zero by `samples`,
zero by `rollup5m`), and the oldest hourly bucket was 28.6 d, so the 90 d
window had never fired at all.

`_reap_catalog` therefore deletes `rollup1h` rows of **process-sourced**
series (`durable = 0`) whose entity is continuously `gone` beyond
`R1H_GONE_EXPIRE_S` (7 d, DM-04's process 5-minute window). The restriction
carries the threshold's whole justification: 7 d is the point where no other
DM-04 window still holds data *for a process series*, whereas a durable one
keeps 5-minute data 30 d and hourly 400 d. A first canary run without that
guard deleted 3,624 rows of `disk` history for snap mounts and an unplugged
USB gone 22-26 d — durable entities do go `gone`, which is exactly what the
unrestricted rule failed to consider. It
does **not** gain a second removal rule: the emptiness test that decides when
an entity may be reaped is unchanged, and expiry simply lets an entity reach
it. Reap keeps one definition of removable; a subsequent pass collects the
entity through the existing path.

Three bounds apply per pass, because `REAP_SCAN` caps entities *visited* and
one entity may own arbitrarily many rows:

| bound | why |
| --- | --- |
| `REAP_SCAN` entities visited | existing cursor; unchanged |
| row budget | one huge entity cannot make a pass expensive |
| elapsed-time budget | protects the ≤ 1 s/tick retention contract under a slow disk |

A partially expired entity is not a special state: it still fails the
emptiness test, so it is retried on a later pass and completes across passes.
Resurrection needs no handling either — `gone_ts` returns to NULL and the
candidate query stops matching, which is why the predicate is written against
`gone_ts` rather than a derived "dead" flag.

**v0.28 (issue #103): retained catalog attribution stays on the catalog.**
`store.doctor.catalog_report()` owns one additive `monitor_attribution`
result shared by doctor and `/self`. A single query unions monitor names from
`entities` and `series`, aggregates total/present/gone entities and total
series, orders by series count descending, entity count descending and monitor
name ascending, and fetches at most 65 rows to return 64 plus explicit
truncation metadata. Including either catalog preserves attribution when
doctor is diagnosing an orphan.

The query never reads `samples`, `rollup5m`, `rollup1h` or `baselines`:
attribution is a catalog question, and making the Self page scan observation
tables would turn diagnosis into retention-path load. `gone_ts IS NULL` is
named *present*, not active or persisted. The true current persisted-selection
set exists in pipeline memory and is published only as the global DM-16
gauges; reconstructing a per-monitor split, creation rate or reapability is
outside this read-only change.

### 10.1 Scheduler (SA-01, SA-07)

`scheduler.py` keeps `next_due: dict[monitor, float]` on the **monotonic** clock. Loop: `clock.sleep_until(next_tick)` → detect monotonic gap > 2×tick → emit `clock_gap` self-event and re-anchor all `next_due` (skip, don't catch up) → run tick. Wall-time is read once per tick (`tick_wall_ts`) and stamped on all samples from that tick.

### 10.2 Pipeline per due monitor (SA-06)

```
snapshot = snapshot_cache.get_or_run(source, deadline)   # once per source per tick
entities = project(snapshot, monitor)                    # + synthetic watchlist entities
rings.append(monitor, entities)                          # CA-04
for d in monitor.derived_topo: rings.append_derived(d.eval(ctx))
alive = {e for e if not exempt(e)}                       # CA-07
evals = {(rule, e): rule.when.eval(ctx(e)) for ...}      # TriBool
for (group, e): step_group(...) → effects                # §10.4
persist: non-exempt samples selected by SA-05; purge prior state for exemptions
gone-detection: entities seen before but absent → CA-08 grace timer
```

### 10.3 Promotion (SA-05)

The process source keeps its own all-process short window (15 samples) in `rings` under a non-persisted namespace. After each cycle, `promotion.expr` (from `leak.toml` et al.) is evaluated per process against that window; newly-true → promote (start persisting + full ring), false for 30 min → demote. Transitions → self-events.

Promotion is a persistence decision, not an evaluation gate: every non-exempt
sampled process reaches the rules before `_select_persisted` runs. Admission is
bounded to the chosen concentration guardrail
`PROMOTION_LIMIT_PER_MONITOR = 10`; this is separate from §9's host-wide ~10
planning estimate and permits up to 10*N* admissions across *N* promotion
monitors. Existing true promotions are refreshed first; expired promotions
demote; new matching entity IDs are sorted and admitted into remaining slots.
The sort provides deterministic admission, not severity ranking: the boolean
promotion expression exposes no scalar by which matches could be ranked, so a
more severe match may be refused while an earlier entity ID holds a slot.
Further matches are retained only in bounded rings and counted as distinct
refusals while they remain denied. The first denied set emits one notice event
with provider `ftmon.<monitor>` / event ID `promotion-limit`; returning below
the limit emits one recovery event. Fixed self metrics publish current limited
monitor count and cumulative admission refusals without creating a metric name
per monitor. Rule evaluation and top-N selection are unchanged.

### 10.4 Incident engine (IN-01..08) — pure

`step_group` implements the SPEC §9.1 diagram exactly; `GroupConfig` carries per-rung `severity, confirm, clear, message-template-id, action, notify_recovery` + backoff table `(300, 900, 3600, 21600)` (IN-02). Backoff/renotify decisions derive from `IncidentCore.last_notify_ts/backoff_tier` — the caller rebuilds `IncidentCore` from DB at startup, which is how restarts keep the schedule (IN-02). The same rebuild pass seeds the pipeline's disappearance tracking (`seen[entity_id] = stored last_seen`) for each open incident's discovered entity (IN-09): the ordinary CA-08 grace path then clears entities that vanished during downtime — no second clearing mechanism exists. `step_episode` shares `IncidentCore` and differs only per IN-08 (cooldown gate, `clear_after` timer via `now − last_seen`). Effects are executed by `effects.py`: `NotifyEffect` creates the immutable notification and its eligible delivery rows in the incident transaction, then the dispatcher attempts due rows post-commit; `ActionEffect` → AC-02 subprocess with env, recorded to history.

### 10.5 Baselines & retention slices

`baseline.py` hooks the 5-min rollup job: for each rolled bucket, apply the CA-05 EW update (`α = 1 − 2^(−300/259200)` per 5-min step at the 3 d half-life), increment `updates`. `retention.py` runs ≤ 1 s/tick with cursors in `meta`: rollup 5m → rollup 1h → prune/reap per DM-05/MD-09 order → `incremental_vacuum(200 pages)`. DM-05 measures `(page_count − freelist_count) × page_size`: a deleted page is reusable headroom immediately, even when the main file remains physically larger until bounded incremental vacuum moves/truncates enough tail pages.

FTMON deliberately does not run full `VACUUM` while the daemon is live (v0.44, issue #74). SQLite rebuilds the database under an exclusive write lock; making a background rebuild survivable would spread new lock recovery across the tick writer, retention transaction and notification outbox, plus add another connection/thread, retry state, cross-thread reporting and shutdown behavior. That availability and implementation cost is disproportionate for a local store capped at 200 MB, where incremental vacuum already bounds normal reclaim work and SQLite reuses freelist pages. A future offline compaction command may be considered from measured operator need, but it must refuse while the daemon lock is held and is not part of live retention.

### 10.6 Self source (RB-02)

Metrics: `cpu_pct, rss_bytes, db_bytes, db_allocated_bytes, db_used_bytes,
db_freelist_bytes, db_headroom_bytes, entities_persisted, series_persisted,
cycle_s,
sampling_seconds_total, sampling_{process,disk,system,net,unit,self,external}_seconds_total,
pipeline_seconds_total, pipeline_{ingest,derived,exempt,rules,persist}_seconds_total,
commit_seconds_total,
actions_outbox_seconds_total, retention_seconds_total, prune_seconds_total,
reap_seconds_total, tick_overruns, event_queue_depth, events_dropped,
events_unstored, ring_mem_bytes, source_activity_age_s, eval_unknown_total,
samples_rejected, external_checks_skipped, external_check_failures{category
attr}, external_perfdata_rejected{category attr}`. Fed from a `SelfStats`
struct the daemon updates in place; sampled like any source.

The database figures are five quantities rather than one because DM-05 bounds
**used pages** — free pages are reclaimable and cost nothing against it — and
an alarm on allocation therefore fires while the defined budget is healthy.
`db_headroom_bytes` is signed against DM-05's normative 200 MB target rather
than any alarm level, so retuning a threshold cannot move the reported distance
to the budget. `db_degrading` is a 0/1 gauge of whether the last retention pass
had to degrade, deliberately not a rate: rules window it with `avg()` over the
CA-04 rings, so "degrading on most passes for an hour" is expressible in a
definition instead of a threshold compiled into the daemon (issue #102).

`db_bytes` and `db_allocated_bytes` are deliberately separate. `db_bytes` is
`stat()` of the main file — what the metric measured before #104, so its stored
history stays continuous. `db_allocated_bytes` is `page_count * page_size`,
SQLite's logical size. **These are not the same in WAL mode**, which FTMON
always uses: pages committed since the last checkpoint live in the -wal file,
so the main file lags allocation (measured: ~1 MB on a live database, and
bounded only by the 1000-page auto-checkpoint threshold). Only
`used + freelist == allocated` holds; the physical file satisfies no such
identity and must never be substituted into budget arithmetic. An earlier
attempt to serve both names from one value broke the very history continuity it
was trying to preserve (issue #104 review).

Collecting them costs a design compromise. `SelfSampler` holds no connection
and cannot run PRAGMAs, so the daemon reads `page_count`/`freelist_count`/
`page_size` on its own connection and pushes the results into `SelfStats` —
the same route the notification backlog gauges take. It runs *before* the
sampler loop rather than after, since the sampler reads the struct directly and
a later read would publish the previous tick's database size beside this tick's
everything else. A failed read keeps the previous values instead of publishing
zero: a momentary lock is not evidence that the database shrank.

`entities_persisted` and `series_persisted` count the pipeline's `selected`
set and the series it actually writes, not entities with `gone_ts IS NULL`. Under SA-05 track-all every sampled entity is marked seen,
so a presence-derived count answers "how many processes are running", which is
roughly an order of magnitude larger than the persisted set DM-16's budget
governs. The caller supplies the loaded monitor set, so a removed definition
stops contributing immediately (MD-09) while a monitor whose interval skipped
this tick keeps its last count rather than dropping to zero.

### 10.7 Notification fan-out and retry (DM-18, NO-04..10)

`Notifier.deliver(notification) -> DeliveryResult` returns success or raises a
typed `RetryableDelivery` / `PermanentDelivery`; adapters never update SQLite.
`dispatch.py` runs one worker thread with its own SQLite connection. It claims
one due delivery oldest-first in a short transaction by changing `pending` to
`sending`, performs the adapter call without a transaction, then commits the
outcome and claims the next row. A condition wake-up after incident commit avoids
polling latency; a bounded one-second poll is the lost-wakeup fallback. At
startup every `sending` row returns to `pending`, which is the explicit crash
window behind NO-04's possible duplicate. The mandatory file adapter is ordered
first by channel priority and is represented by a delivery row, keeping audit
behavior on the same durable path.

The retry delay table is `(30, 120, 600, 3600, 21600)` seconds, indexed by the
completed attempt count and capped by `created_ts + 86400`. HTTP classification
is fixed by NO-07; a valid integer or HTTP-date `Retry-After` is clamped to
`[now + scheduled_delay, created_ts + 86400]`. Adapter error text is normalized
to a fixed category plus status code and capped at 512 characters before it
reaches SQLite. Response bodies, exception representations, credential values,
and complete URLs are never persisted.

File-delivery failure is the exception to the remote 24-hour cap: it retries at
the 6-hour ceiling until storage recovers because the audit copy is mandatory.
Desktop readiness is validated before rows are created; a runtime `notify-send`
timeout is retryable and other non-zero exits are permanent. These rules avoid
an absent desktop session creating an endless queue on a server.

The Darwin desktop adapter launches `/usr/bin/osascript` with a
`display notification` expression and a bounded timeout. It needs no FTMON app
bundle or code signature, but Notification Center attributes it to
`com.apple.ScriptEditor2`; adapter documentation and doctor output must name
that identity. A zero exit is delivery success for NO-04 purposes even when
Notification Center or Focus suppresses presentation. The adapter does not
read the private `com.apple.ncprefs.plist` bit field: it is undocumented, does
not cleanly represent Focus/global suppression, and supplies no FTMON-specific
authorization state.

The ntfy adapter POSTs the rendered title/body to
`{base_url}/{urlquoted_topic}` with a fixed, control-free `FTMON <severity>` title,
priority (`info=2, notice=3, warning=4, error|critical=5`), tags
(`ftmon`, kind, severity name), and `Authorization: Bearer …` headers; monitor
data never enters the URL. The generic webhook POSTs `application/json` schema
`ftmon.notify.v1`.
SMTP constructs a plain-text `EmailMessage`, performs TLS before authentication,
and classifies standard SMTP response families. Adapters share one hardened
HTTP opener that rejects HTTPS-to-HTTP redirect and caps response reads at 8 KiB.
The contract follows ntfy's documented
[publish/token API](https://docs.ntfy.sh/publish/); operator documentation links
its [retention/privacy behavior](https://docs.ntfy.sh/privacy/) so choosing the
public service is an informed data-egress decision rather than a silent default.

The worker survives store faults the way the tick loop survives them (PM-12).
`connect`/`migrate`/`reset_inflight` and the drain loop share one recovery
boundary, so no path can exit the thread with its connection still open. A
message-matched lock/busy or invalidated-connection fault closes the
connection, waits an exponential backoff (0.5 s doubling to 5 s) *on the same
condition the wakeups use* so `stop()` and `reconfigure()` interrupt it, then
reconnects and repeats `reset_inflight`. Everything else — corruption, a failed
migration, an I/O or permission error, an unexpected exception — is fatal: the
thread publishes `dead` and exits rather than looping forever against a fault
retrying cannot fix. The lock predicate is imported from the PM-10 tick path
rather than restated, because two independent spellings of "is this SQLite
telling us it is busy" is how PM-10 and PM-12 drift apart later.

Liveness is durable because a Python thread's death is invisible to both the
daemon loop and any later `wake()`. The worker writes `notify_dispatch_state`
(`DISPATCH_STATES` in `store/outbox.py`: `running`/`recovering`/`stopped`/
`dead`), `notify_dispatch_heartbeat_ts`,
and the last error category/timestamp to `meta` on its own connection. The
There is deliberately no `starting` state: the row does not exist until the
worker has connected and published, and `doctor` reads that absence as
`unknown` (v0.30, issue #121 — DESIGN previously listed a `starting` state the
implementation never wrote). `unknown` and `stopped` are treated alike by
`dispatch_health`, so a state that only ever appeared between thread start and
first write would have no operational consumer.

The heartbeat is throttled to 30 s and forced on state changes and non-empty
flushes: an unconditional per-poll write would put a 1 Hz writer against
`commit_tick`'s `BEGIN IMMEDIATE`, manufacturing the very PM-10 contention this
section exists to survive. The daemon records `notify_dispatch_mode`
(`background`/`synchronous`) so doctor can tell a controlled-clock run with no
worker from a worker that died, and both heartbeat and backlog ages are
measured against `last_tick_ts` — the same injected-clock domain that wrote
them — never against doctor's own wall clock.

`ftmon doctor` resolves secret references and validates non-secret structure but
reports only `ready`, `disabled`, or a stable error code. Because that says
nothing about delivery, doctor also prints dispatcher state and a four-way
backlog split — `pending`, `due_claimable`, `quiet_held`, `failed` — plus the
oldest claimable-due age, and fails when a live daemon's worker is dead or that
age exceeds 60 s. Quiet-held rows are excluded from both the claimable count
and the age, so an overnight quiet window cannot read as a stuck outbox; the
predicates are gated on a live daemon, so the documented "stop the daemon and
inspect" workflow does not fail on debt no one is draining yet. The self source
carries the aggregates, `notify_store_errors`, `notify_worker_alive`, and a
bounded `notify_failed` total — bounded rather than per-channel because five
extra persisted series bill against the same DM-16 catalog worksheet issue #74
was spent defending; the per-channel breakdown is a read-side query on
`notification_deliveries` for doctor and `/self`, which needs no series at all.
Together these make a broken notification path observable without recursively
notifying about notifier failure.

The existing 30-second rescan also compares the `config.toml` file stamp. A
changed valid file constructs a complete adapter snapshot, reconfigures the one
worker at an attempt boundary, then changes the writer's policy for future
notifications. Existing delivery rows are never added retroactively. A malformed
or removed file retains the last known-good snapshot; a valid but individually
invalid channel fails closed while the other channels reload (NO-10).

`SIGHUP` triggers this same rescan out of cycle (PM-11): the handler sets a
flag on `DaemonCore` and the top of the next tick consumes it — the handler
itself never touches the filesystem or database, so it cannot race the tick
loop or block in a signal context. The packaged daemon units map it to
`ExecReload=`. A Darwin LaunchAgent uses absolute `ProgramArguments`, explicit
FTMON path environment, `RunAtLoad`, and `KeepAlive` in the user
`gui/<uid>` domain. launchd passes SIGHUP to the managed process unchanged, so
the service wrapper signals the current PID and retains the existing PM-11
handler. `launchctl kickstart -k` is reserved for an explicit restart because
it replaces the PID.

### 10.8 External check execution and projection (EC-*, MD-11, SE-07)

The cache key for ordinary sources remains the source name. For `external`, it
is `(source, check_alias)`: all due monitors referencing one alias receive the
same immutable `RawCheckResult`, while each monitor projects only its declared
performance mappings. This preserves SA-06's run-once guarantee without making
one definition's schema authoritative for another.

```python
@dataclass(frozen=True)
class CheckSpec:
    alias: str
    argv: tuple[str, ...]
    protocol: Literal["nagios", "ftmon-json"]
    timeout_s: float

@dataclass(frozen=True)
class RawCheckResult:
    state: int                 # 0 OK, 1 warning, 2 critical, 3 unknown
    message: str               # control-free, <= 2 KiB
    duration_s: float
    values: Mapping[str, tuple[float, str]]  # raw label -> (value, UOM)
    failure: str | None        # fixed category, never raw stderr/exception

@dataclass(frozen=True)
class PerfMapping:
    label: str
    metric: str
    plugin_uom: str
    unit: str
    kind: Literal["gauge", "counter"]
    scale: float
```

`CheckRunner.run(spec, deadline_mono)` uses `subprocess.Popen` rather than
`subprocess.run` so timeout can send TERM then KILL to the new session/process
group. It supplies `stdin=DEVNULL`, captured pipes, `start_new_session=True`,
`close_fds=True`, `cwd=state_dir`, and exactly `PATH=os.defpath` plus fixed
non-secret identity fields (`FTMON_CHECK_ALIAS`, `FTMON_CHECK_TIMEOUT`). On
Windows, the same paths/process seam additionally copies only `SystemRoot`,
`SystemDrive`, `windir`, `TEMP`, `TMP`, and `PATHEXT` from the service
environment (`SystemRoot` alone has a documented `C:\Windows` fallback); this
is minimum runtime support, not general environment inheritance. Windows
termination gives `taskkill /T /F` a bounded deadline and falls back to bounded
direct-child kill/waits. A pair of bounded readers drains stdout/stderr to prevent pipe deadlock while retaining
at most 64 KiB/8 KiB; adapter input over the protocol cap fails closed. Stderr
is discarded after categorization. No DB transaction spans launch or wait.
Immediately before `Popen`, the runner repeats executable `lstat`, resolved-path,
owner and mode checks; a changed target returns categorized unknown instead of
relying on the registry loader's earlier observation.

`nagios.parse()` consumes only the first UTF-8 stdout line. It splits once on
`|`, normalizes exit status, strips ASCII controls from the summary, and parses
space-separated perfdata with a small scanner supporting Nagios single-quoted
labels and backslash-free values. It never uses shell tokenization. Threshold,
minimum and maximum fields are syntax-checked then discarded: exit status
already represents the plugin threshold decision, while FTMON rules remain
explicit. Duplicate raw labels are marked ambiguous and unavailable to every
mapping of that label. `jsoncheck.parse()` strips surrounding ASCII whitespace,
then applies EC-10 with `json.loads` plus
exact type/key/depth/count checks; Python booleans are rejected before numeric
coercion.

Projection always emits `plugin_state`, `plugin_ok`, and `duration_s`. For each
mapping it looks up one raw value, requires an exact UOM match, multiplies by
finite `scale`, rejects non-finite output, and emits the mapped metric. Missing
or rejected labels are omitted so expression lookup returns `None`. The
human-readable message is an attr, not a metric. Existing pipeline, storage,
query, Metrics and Trend code needs no external-check special case after this
projection boundary (EC-04/05).

The scheduler maintains a rotating alias cursor. It runs due aliases
sequentially until the shared source deadline, caches successes and unknown
results for the tick, and counts unstarted aliases as `external_checks_skipped`.
The next tick begins after the last considered alias, preventing a slow first
check from starving later checks. Timeout/launch/protocol failures increment
categorized `external_check_failures`; Nagios state 3 does not. Registry reload
shares the config rescan boundary used by notifications but swaps an all-valid
`CheckRegistry` independently, so one malformed change cannot silently alter
existing execution authority (EC-06/08).

---

### 10.9 Extra-monitor recipe catalogue (XR-*, TS-16)

Each non-underscore directory below `extra-monitors/` is a self-contained
compatibility claim. Markdown carries operator reasoning and machine-readable
`recipe.toml` carries only bounded catalogue metadata and fixture expectations.
Keeping both avoids two failure modes: prose-only examples silently rot, while
configuration-only examples omit the security, licensing and threshold reasons
an operator needs to trust them.

`tests/extra_monitors/test_recipes.py` discovers recipes rather than maintaining
a second index. It validates exact manifest keys, required article headings,
HTTPS upstream and licence metadata, registry/definition alias agreement,
external-definition schema, privilege shape and deterministic adapter output.
It never loads the registry through `checks.registry` because example
executables are intentionally not installed in CI; executable readiness remains
an operator responsibility verified by `doctor` on the target host.

Fixtures are captured protocol output, not golden FTMON database snapshots.
They prove the volatile third-party boundary—exit state, labels and UOM—while
the central TS-15 journey already proves projection through history, incidents
and Trends. Network, hardware and installed-plugin checks stay opt-in so the
default suite remains deterministic and does not turn upstream availability or
root access into a merge gate.

Repository-maintained scripts are the exception to the no-vendoring rule. They
live under their recipe's `scripts/`, carry an explicit licence header, depend
only on documented platform tools or the Python standard library, and have
direct behavioral tests in addition to protocol fixtures (XR-05).

### 10.10 Static Exchange publisher (XR-06..10, TS-19)

`tools/build_exchange.py --output dist/exchange` reads only regular files from
non-underscore recipe directories, validates the same bounded metadata used by
TS-16, then writes into a newly created destination. It refuses a destination
inside `extra-monitors/`, symlinks anywhere in a recipe, duplicate IDs or
generated paths, unsafe URL schemes and unsupported Markdown. The builder does
not import recipe modules, invoke commands, inspect executables or access the
network. This keeps a catalogue contribution a data-review problem rather than
a CI code-execution boundary.

The renderer uses Python's HTML escaping plus a deliberately small Markdown
subset (headings, paragraphs, fenced code, lists, inline code and HTTPS links).
Raw HTML is text, not markup. Templates are repository-owned constants rather
than contributor-selectable files. Output is ordered by recipe ID and JSON uses
sorted keys and fixed separators, so two builds of one tree are byte-identical.

The artifact contains `index.html`, `recipes/<id>/index.html`,
`search-index.v1.json`, repository-owned `assets/exchange.css` and
`assets/exchange.js`, plus `404.html`, `.nojekyll` and `CNAME`. The index renders
all cards and filter links in HTML; JavaScript only narrows that existing list
from the versioned search document. Detail pages show compatibility and trust
metadata before the safely rendered article, and link to reviewed source on
GitHub rather than copying or offering a third-party executable.

`.github/workflows/exchange.yml` builds and tests on pull requests and pushes.
The deploy job additionally requires `github.ref == refs/heads/main` and a push
event, depends on the build artifact, targets the protected `github-pages`
environment, and alone receives `pages: write` plus `id-token: write`. Workflow
actions are pinned to immutable revisions because a documentation publisher is
still a software supply-chain boundary. Pages terminates TLS for the verified
`exchange.ftmon.org` custom domain; DNS and rollback remain operator steps.

### 10.11 Shared contribution skills (AS-*, TS-20)

`.ai/skills/` is a neutral repository namespace rather than a discovery path
claimed to work automatically in every agent. Each skill follows the common
filesystem shape with a concise `SKILL.md`; optional `agents/openai.yaml` is UI
metadata and never changes workflow semantics. No schema, template or plugin
reference is copied into bundled resources: the skill reads live repository
files so a SPEC change cannot leave a hidden second contract behind.

`ftmon-add-extra-monitor` is the only initial shared skill because that workflow
is frequent, externally contributed and crosses the most trust boundaries. It
branches between an observed Nagios first-line/exit-code adapter and strict
FTMON JSON, then rejoins for mappings, incidents, Trends, recipe evidence,
Exchange publication and tests. It does not scaffold files itself; using the
reviewed `_template` through normal agent edits keeps changes visible in the
diff and avoids giving a helper script implicit write authority.

`tests/ai_skills/test_shared_skills.py` treats skills as inert UTF-8 text. It
checks portable frontmatter and bounded structure, resolves backtick repository
paths, verifies commands name existing tools/test directories, and asserts the
critical concepts that make the workflow safe. This deliberately avoids a
vendor SDK: native discovery is smoke-tested by users of that product, while CI
protects the shared semantic core.

Installation remains explicit. Codex users link/copy the skill directory into
`${CODEX_HOME:-$HOME/.codex}/skills/`; Claude Code users link/copy it into
`~/.claude/skills/` or a checkout-local `.claude/skills/`. These are generated
adapters in ignored/personal locations, never separately committed skills.

---

## 11. Event pipeline (SA-03/08, DM-07..10, DM-15/20)

`journald.py`: spawns `journalctl -f -o json --output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT,__CURSOR [--after-cursor=C]`. Reader thread appends raw lines to deque. `drain()` (main thread): parse JSON (malformed → count, skip), normalize → `EventRecord` (severity map: PRIORITY 0–2→critical, 3→error, 4→warning, 5→notice, 6–7→info; provider = `_SYSTEMD_UNIT` else `SYSLOG_IDENTIFIER`), return last `__CURSOR`. Cursor is persisted in the tick's write txn (DM-15). Storm counter per (source, provider) sliding minute (DM-10); store-filter per amended DM-09; matching against loaded event rules uses the same compiled `when` expressions with the event-field NameEnv. Reader death → `alive()` false → scheduler restarts with backoff (SA-03).

`win_evtlog.py` keeps one subscription and bookmark per configured channel.
On first use of a channel absent from the durable composite cursor, a reverse
filtered query snapshots its tail and seeds that bookmark before subscription;
an empty result seeds an internal oldest-record marker and subscribes from the
oldest record. This preserves first-run “now” behavior for populated logs while
making the first event in an empty log replayable after a sibling-only partial
drain. Callback bookmarks remain queue evidence only; `drain()` advances each
channel independently as represented entries are removed (DM-15/DM-19).

All three platform adapters call the same adjacent-repeat reducer before queue
admission. It compares the complete canonical origin/message signature and
mutates only the current tail run, replacing its opaque cursor/bookmark or
macOS identity with the newest one. Restricting the reducer to contiguous runs
is a durability decision: a per-key LRU could merge across an intervening
record, then commit a later opaque checkpoint before that intervening record
was drained. Aggregate attrs preserve count and first/last source timestamps;
the episode engine consumes the count directly rather than expanding a large
storm back into memory. Adapter `received` and `repeated` counters feed a
60-second rolling raw arrival-rate gauge in the event engine (DM-20).

`oslog.py` first replays `/usr/bin/log show --style ndjson` from
several seconds before its persisted wall-time watermark, then starts
`/usr/bin/log stream --style ndjson` with the same fixed operational predicate.
That source-side allowlist accepts fault-level events from third-party
executable roots and explicit kernel storage-integrity text;
ambient debug ingestion is forbidden because downstream rules cannot protect
the reader queue. Both outputs are
line-framed but not pure event NDJSON: the reader ignores human filter text,
blank lines, and terminal `{"count": ..., "finished": 1}` objects, accepting
only `eventType == "logEvent"`. Records are sparse; normalization uses
`timestamp`, `eventMessage`, `subsystem`, `category`, `processImagePath`,
`processID`, and `messageType` when present.

The replay overlap is deduplicated against bounded pending and durable identity
windows. A coalesced run shares the one global pending window rather than
allocating an identity list per queue entry; draining transfers only identities
from represented runs into the durable checkpoint, while overflow-dropped runs
cannot advance it. Both windows are capped at `IDENTITY_MAX`.
The preferred identity is `(bootUUID, machTimestamp, traceID, processID,
senderProgramCounter)` plus a normalized-payload hash fallback. The set covers
at least the replay/handoff overlap and is committed with the watermark only
after events are accepted. This is deliberately at-least-once: Monterey
accepts only second-resolution `--start` values, the boundary is inclusive,
and the same event's stream and archived timestamps differed by milliseconds
on real hardware. If the requested boundary predates retained unified-log
data, the source records a retention-gap self-event before tailing current
events.

The macOS profile does not turn unified-log severity alone into actionability:
routine Apple components emit `error` and even `fault` during normal operation,
and an `osascript` notification itself can produce TCC/RunningBoard errors.
Events therefore ship enabled only because admission is restricted before the
queue. The adapter assigns stable event classes (`third-party-fault` and
`storage-integrity`) and the profile rules match those classes;
its store threshold is canonical `critical`. Disk rules exclude read-only and
`nobrowse` mounts (including mounted application images), omit APFS inode
thresholds, and retain capacity rules only for writable visible volumes.
Network rules are watchlist-only.

---

## 12. Query layer (DM-06, UI-05)

Tier choice: `end > now−48h and span ≤ 12h` → raw; `span ≤ 30d` → 5m; else 1h — then if points > max_points, server-side LTTB downsample to max_points (used by web charts and MCP alike). All timestamps out are UTC ints + one `tz: "<IANA>"` field per response (MC-02).

`Query.series` remains the shared web/chart path and may return empty-point
shells for catalog rows with no observations in-range. MCP `query_metrics`
uses a separate observed-first path (`list_observed_series_entities` via
`EXISTS` on the DM-06-selected table, capped `COUNT` preflight, then
`series_points`) under one WAL `read_snapshot()` transaction so quiet windows
are empty series with reasons, entity order is deterministic (`entity_id ASC`),
discarded entities are never point-materialized, and an intervening daemon
write cannot inflate `points_returned` past the hard cap (MC-01 / issue #61).

Baseline reads join `baselines` to `series`. `current_baseline` returns the
stored level even below the 240-update rule gate together with capped coverage,
readiness, update bucket and effective half-life. `baseline_history` starts at
that current row and walks retained `rollup5m.avg` rows newest-first, applying
`b_previous = (b_current − α·rollup_avg) / (1−α)` at most `updates−1` times.
The seed is never reversed. Returned points retain their five-minute bucket
timestamps; the first requested bucket is `ceil(start/300)·300`, and history is
truncated only when that bucket precedes the earliest reconstructable point and
the seed was not reached. Missing buckets remain gaps.

`list_baselines` orders all stored rows by `(monitor, entity_id, metric)` and
uses an opaque keyset cursor containing both the last key and canonical exact
filters. The default page is 100 rows and the hard maximum is 500; malformed,
filter-mismatched or out-of-range requests fail rather than clamp. This shared
page contract serves MCP and the Baselines web index (MC-07/UI-02).

---

## 13. MCP server (`mcp_server.py`, MC-01..07)

FastMCP over stdio; every tool = thin wrapper on `Query`/`SmallWrites`/`definitions`. Parameter schemas (FROZEN; `range` = duration string or `[iso, iso]`):

| Tool | Params (required bold) | Returns |
| --- | --- | --- |
| get_status | — | daemon alive/stale (shared UI-04 predicate)/last_tick_age, monitors[] (loaded entries, plus `state="config_error"` for invalid files and unavailable check aliases), drafts[], open_incidents, self_metrics, glances[] {monitor, entity_id, metric, value, unit, aggregate(max\|min\|last), thresholds[{label, value}]} with glances_returned/glances_matched/glances_truncated/limits{max_glances:64} always present (additive return fields; params FROZEN) |
| query_metrics | **monitor, metric, range**; entity, agg(avg\|min\|max\|last), filter_expr | series[] {entity, points[[ts,v]] \| agg}, resolution, tz, truncated, entities_returned, entities_matched, points_returned, limits{max_entities:50, max_points_per_entity:2000, max_total_points:10000}; when series empty also empty_reason + available_metrics (additive return fields; params FROZEN) |
| top_consumers | **resource(cpu\|rss\|io), range**; n=10 | ranked[] {entity, attrs, agg_value} |
| get_process_history | **name_or_pid, range** | entities[] {entity_id, attrs, first/last/gone, series{…}} |
| list_events | **range**; min_severity, provider, match_expr, limit=200 | events[] |
| list_incidents | — ; state, range, monitor | incidents[] summary |
| explain_incident | **id** | rule text+params, series ±window, events ±10 m, history[] |
| list_monitors / get_monitor | — / **name** | defs + state + validation + load history |
| monitor_paths | — | {config_dir, monitors_dir, drafts_dir, actions_dir, check_registry, data_dir, db_file, state_dir} — mirrors `ftmon paths --json` (MC-06/CL-06) |
| diagnose_monitor | **name** | {found(enabled\|disabled\|draft\|missing), path, valid, errors[], last_load{hash, age_s}, check{alias, registered, executable_trusted}, last_result{entity_id, plugin_state, plugin_ok, plugin_message, duration_s, sample_age_s}\|null} — booleans/categories and stored EC-05 fields only, never argv (SE-07); `last_result` null when non-external / no DB / configured entity never sampled |
| list_baselines | monitor, entity, metric, ready, limit=100, cursor | {tz, baselines[]{monitor,entity,metric,level,updates,required_updates,coverage,ready,updated_at,half_life_s}, next_cursor}; all stored rows, exact filters, opaque filter-bound keyset cursor (MC-07) |
| validate_monitor | **toml_text** | {ok} \| {errors[]: {path, code, message, hint}} |
| define_monitor | **toml_text** | {draft_path, approval_hint, next_steps[] {via(cli\|web), action}} \| errors as above |
| ack_incident | **id**; note | {ok, incident} |

Errors: `{code, message, hint}` (MC-04) with codes `invalid_params, validation_failed, not_found, name_exists, daemon_stale`. Resources (MC-05), force-included from their canonical sources into `ftmon/docs/` in the wheel and loaded with `importlib.resources`: `ftmon://docs/definitions` → `docs/definitions.md` (authoring traps, CI-validated recipes, attribute-only `filter_expr`); `ftmon://docs/check-authoring` → `docs/check-authoring.md` (ftmon-json exit-0 trap first); `ftmon://docs/external-checks` → `docs/external-checks.md`. Tool descriptions steer writers to those resources (MC-05); empty `query_metrics` responses keep the v0.40 `empty_reason` / `available_metrics` fields without an additive success-path `hint`. A checkout-relative read is only the editable-development fallback.

---

## 14. Web UI (`web/`, UI-01..09)

Starlette app; Jinja2 (autoescape); a body `data-refresh-ms` contract plus the
small vendored script performs full-page polling: dashboard, incident and
Events views every 5 s, Monitors and Self every 15 s (UI-04). Metrics, Trends
and Baselines do not auto-refresh so chart state and long-form inspection stay
stable. uPlot charts are fed by `/api/series` (JSON from `Query`, ≤ 2 000 pts).
Both web middlewares call one response-header helper. Operational middleware
also enforces the exact Host allowlist and matching POST Origin. The shared
headers are the SE-02 CSP (`default-src 'self'`, `frame-ancestors 'none'`,
`form-action 'self'`, `base-uri 'none'`), `nosniff`, `DENY` framing, no-referrer,
CORP same-origin and COOP same-origin; neither middleware emits CORS (UI-08).

Routes: `GET /` dashboard · `GET/POST /incidents[/{id}][/ack]` · `GET /metrics?monitor=…&entity=…&metric=…&range=…&statistic=…[&group=…]` explorer (state in query string, UI-02) · `GET /baselines` read-only index · `GET /events` · `GET /monitors`, `POST /monitors/{name}/(enable|disable|approve|delete-draft)` · `GET /self` · `GET /api/series?monitor=…&entity=…&metric=…&range=…&statistic=…[&group=…]`. Templates: `base.html` + one per page; severity rendered as `<span class="sev sev-error">▲ error</span>` (icon + text, UI-09); charts carry a server-rendered text alternative. The locally packaged FTMON mark supplies the header image, PNG/ICO favicons, and touch icon without weakening UI-01's offline guarantee. Its header image is decorative beside a real-text wordmark so branding cannot obscure the home link's accessible name or become unreadable when images fail.

Incident detail composes evidence links through a closed built-in `self` map:
`cpu-budget` → Metrics `cpu_10m`/`avg`; `rss-growth` → Trend `rss-growth`;
`rss-budget` → Metrics `rss_bytes`/`max` and Trend `rss-growth`; `db-budget` →
Trend `db-capacity`. Other monitors retain the declared `incident_group`
profile match. The hard-coded self map is deliberate: these four groups are
normative product semantics, while adding definition syntax would expose a new
authoring contract solely to configure built-in navigation. Every generated
URL carries `entity`, `range=24h`, and `group`. Metrics and Trends keep `group`
in their forms and use it as the marker filter, visibly reporting the filter
and a no-matches state and offering a clear link. Changing monitor/profile
drops the old group because group names are monitor-scoped; direct URLs remain
valid for retained historical incidents. Trend targets are checked against the
live definition catalog because a Trend is a definition-owned presentation
contract. Metrics targets deliberately are not: an exact persisted-series
bookmark retains UI-13's honest expired/no-observations state (UI-12/UI-13).

Metrics payloads always include `baseline`: null when the selected persisted
series has no CA-05 row, otherwise the current record plus native five-minute
`points[[ts,value]]`, explicit exact-300-second `runs`, and range-relative
`history_truncated`. The panel's `y_domain` is calculated from all finite
metric, envelope and baseline values, with 0..100 retained as the minimum
percent domain and deterministic padding for other/constant ranges. Browser
rendering strokes each supplied run dashed and paints its native points; it
never step-holds onto raw timestamps, spans a larger gap, interpolates hourly
history, or draws the current level across an unobserved range. A visible
Baseline key and the accessible summary carry learning/readiness, level,
coverage and truncation even when no historical run falls in range
(UI-13/TS-11).

`GET /baselines` applies the same exact filters, 100/500 row bounds, opaque
filter-bound keyset cursor and ordering as MC-07. Invalid limits and cursors are
HTTP 400, while each row links to the matching shareable Metrics selection.
The page has no POST route; CA-06 reset remains CLI-only (UI-02/03).

Self renders `ftmon.__version__` as the explicitly labelled web-process version.
It does not infer a daemon version from the shared database, so an upgrade that
has not restarted both services cannot present false process-version equality
(UI-02).

### 14.1 Public synthetic demo (UI-15/16, SE-06)

Demo mode is a separate application factory, `create_demo_app`, rather than a
boolean checked inside mutating handlers. It imports `Query` but not
`SmallWrites`, definitions writers, actions, daemon, or MCP. Its route list is
an allowlist of the normal GET/HEAD page, partial, static, and series handlers;
Starlette therefore returns 404/405 for every mutation path. A response banner
and `<meta name="robots" content="noindex,nofollow">` distinguish synthetic
content and keep parameterized explorer URLs out of search indexes.

`ftmon demo build --output PATH` replays the packaged, versioned
`scenarios/demo-v1.jsonl` with a fixed clock and seed into a new temporary DB,
runs retention/rollups, verifies UI-16 coverage, fsyncs, then atomically renames
the completed file. The demo web process opens it with SQLite URI
`mode=ro&immutable=1`; it refuses the normal XDG database path, a non-regular
file, group/world-writable input, or a database missing the `demo_dataset=1`
meta marker and expected scenario version. No visitor state is stored. The seed
includes one learning and one ready baseline with matching retained five-minute
rows so Metrics and the Baselines index exercise both visibility states without
operational data (UI-16).

The CLI is explicit:

```sh
ftmon web --demo --demo-db /var/lib/ftmon-demo/demo.db \
  --demo-host demo.ftmon.org --port 8420
```

It still binds `127.0.0.1`. Demo middleware accepts exactly
`Host: demo.ftmon.org` (optional `:443` after normalization), ignores forwarded
Host/Origin authority, caps request targets at 4 KiB, and emits the same shared
security headers as the operational app. Startup fails if `--demo-host` is absent, is an
IP/localhost name, or contains a wildcard.

The reference deployment uses a dedicated `ftmon-demo` account, a systemd
oneshot database builder before the read-only web service, and Caddy bound to
80/443 for automatic HTTPS and reverse proxying to loopback. The hosting layer
must additionally cap request rates/concurrency; the backend remains bounded by
the 2,000-point query limit and immutable DB. A daily timer rebuilds then
restarts the service, although correctness never depends on visitor-state reset.
The demo has no operational daemon, notification credentials, writable actions,
MCP server, or backup job.

Caddy is chosen for the reference deployment because a hostname enables
[automatic public HTTPS](https://caddyserver.com/docs/quick-starts/reverse-proxy)
without a separate certificate-renewal mechanism; it supplies transport, not
application trust, which remains enforced by the demo Host and read-only rules.

---

## 15. CLI (`cli.py`, CL-01..05)

argparse tree; every subcommand is a function taking `(Paths, Query|…, argparse.Namespace)` so tests call them directly. Mapping: `daemon→daemon.run`, `mcp→mcp_server.run`, `web→web.app.run` (`--demo` builds `web.demo_app.create_demo_app`), `demo build→demo.build`, `init --profile→cli.cmd_init` (installs builtins + explicit config scaffold), `check→cli.cmd_check` (CL-02), `status/top/incidents/incident/events/query/monitors→store.query` renderers (each with `--json`, CL-03; `status` exit codes per CL-04), `ack/monitor approve|enable|disable→SmallWrites/definitions`, `baseline reset→store`, `doctor→store.doctor` (CL-05: quick_check/--deep, WAL checkpoint, sizes, cursor and delivery ages, channel readiness, orphans, `--backup` via `sqlite3.Connection.backup`).

### 15.1 Generic historical trends (M7.1, MD-10/CA-10/UI-12)

`MonitorDef.trends` is a tuple of frozen `TrendProfile` values validated by the loader. A profile is presentation metadata over persisted series, never an executable expression and never a reason to collect more data. Frozen fields: `id, kind, title, value_metric, value_unit, rate_metric, rate_unit, confidence_metric, confidence_threshold_param, remaining_metric, value_threshold_params, rate_threshold_params, incident_group`.

The generic view uses up to four synchronized panels: required value and signed-rate panels, optional confidence on a fixed 0..1 scale, and optional qualified time remaining. `null` means the concept is not meaningful for the profile; an existing panel with empty points means data has not arrived. Separate panels preserve distinct units and failure modes while synchronized cursors retain temporal correlation.

`Query.trend(monitor, entity, profile, …)` returns explicit units, resolution, coverage, declared thresholds and group-filtered incident markers. Its optional incident-group override takes precedence over the profile default so a shared panel can investigate another group without mixing markers. Metrics and Trends select incidents whose lifetime overlaps the requested range (`opened_ts <= end` and not cleared before `start`); acknowledgment is quieting, not recovery, and therefore does not end that lifetime. Projection uses persisted rate + remaining + optional confidence and never differentiates display points or fills absent buckets. `Query.disk_trend` remains a v0.x compatibility adapter.

Routes: `GET /trends[/{monitor}/{profile}]?entity=…&range=…[&group=…]` and `GET /api/trend?monitor=…&profile=…&entity=…&range=…[&group=…]`. `/disks` redirects to `/trends/disk/space_growth`. Dashboard, monitor and incident links all target this same explorer rather than creating alternate render/query paths.

The Trends selector queries active entity rows seen within the greater of two
monitor intervals or CA-08's default five-minute grace. The freshness bound
also excludes legacy null-`gone_ts` rows left by an earlier daemon lifetime.
When a URL explicitly names an older entity, the view adds that one identity
back to the selector and queries its retained history. This bounds the common
discovery path without breaking forensic links; Metrics deliberately remains
the complete persisted series catalogue.

Reference profiles prove both shapes: disk `space-growth` supplies all four panels; leak `rss-growth` supplies value/rate/confidence and explicit `projection: null`. A process has no single honest capacity ceiling because host memory, swap, cgroups and the OOM killer differ, so FTMON refuses to invent one.

### 15.2 Metrics versus Trends (M7.2, UI-13)

Metrics and Trends share one uPlot adapter, series-envelope JSON shape, cursor/time-axis behavior, incident-marker plugin, and server-rendered text-summary rules. Sharing is a correctness decision, not merely visual consistency: two renderers previously disagreed about time axes, gaps, long-range rollups, and interaction, making the diagnostic view a poor way to verify a Trend.

Their semantics remain deliberately separate. `/metrics` selects one persisted `(monitor, entity, metric)` and a rollup statistic; its selector catalogue uses `EXISTS` against the same raw/5-minute/hourly tier selected for the requested range, so expired metadata cannot produce default empty charts. An exact requested series bypasses catalogue filtering for stable bookmarks but renders no chart when that tier has no points. Metrics reports observations and never infers that a name containing `slope`, `pct`, or `rate` has special meaning. `/trends` joins only definition-declared panels and may qualify confidence or projection. A matching-profile link is navigation, not automatic interpretation inside Metrics.

`GET /api/series?monitor=…&entity=…&metric=…&range=…&statistic=…[&group=…]` returns `{monitor, entity, metric, unit, statistic, resolution, points, lower, upper, incidents, incident_group, summary, matching_trends}`. The optional group filters incident markers and is echoed as `incident_group`; it does not change the series. Raw metric units come from `SourceDecl`; derived units come from explicit trend-profile use when available, otherwise the neutral label `value`. Server-side Query remains authoritative for tier selection, envelopes, incident filtering, and the 2 000-point cap.

### 15.3 Dashboard health tiles (M7.3, UI-14/UI-17/UI-18)

The dashboard composition root builds a `MonitorTile` view model rather than embedding policy in Jinja: `{name, description, state, icon, label, incident_count, max_severity, trend_profiles, glance?}`. State precedence is evaluated once in Python as `config_error > stale_or_unknown > disabled > error_or_critical > notice_or_warning > clear`; templates only render it. Centralizing precedence prevents a CSS class or loop ordering change from silently turning a broken monitor green.

“Live incident” includes both `open` and `acked`: ack suppresses renotification but is not recovery (IN-02). Staleness overrides enabled/incident display because old database state cannot prove current health. A successfully loaded monitor with no committed load/sample evidence is also unknown; `monitor_loads`, series, or an event cursor provides evidence that it has participated in a daemon cycle. Invalid definition files become config-error tiles even though no `MonitorDef` exists.

CSS state classes restore the legacy green/yellow/red scan pattern via left-edge
accents and soft washes (healthy tiles stay quiet so problems read first), and
every tile also carries a stable glyph and text label. The dashboard sorts tiles
attention-first, splits clear monitors into a quieter section, and keeps an
intentionally disabled monitor without a retained incident in a separate
inactive section. A disabled monitor with a live incident remains prominent.
That ordering is presentation only and never recomputes UI-14 state. Summary
severity comes from the same live incidents as the tiles, including synthetic
demo incidents. No state flashes: motion
is unnecessary for urgency, hostile to reduced-motion users, and makes a
workstation dashboard distracting. Incident links use `/incidents?monitor=…`,
the same Query filter as CLI/MCP.

`MonitorDef.glance` is frozen presentation metadata, not executable policy and
not a reason to collect another metric (MD-12). A focused read-only Query call
finds each active entity's latest raw sample for the declared metric, rejects
samples older than twice the monitor interval, evaluates the definition's
CA-07 exemptions using persisted raw windows, attributes, parameters and
baselines, then selects `max|min` with newest-timestamp then entity-ID
tie-breaking. Rollups and entities with
`gone_ts` are excluded because a retained historical value cannot describe the
host now. Python resolves the declared threshold parameters and composes the
optional tile value; Jinja never aggregates, checks freshness or guesses a
unit. Unknown, disabled, config-error and globally stale tiles omit glance, so
UI-14 remains the sole health-state contract (UI-17). This adds no database
migration, daemon write, tick-path computation or incident-state transition.

That whole read side lives in `glance.py`, not in `web/app.py`: the staleness
predicate, the UI-14 precedence function, the check-alias loader, the CA-07
stored-context evaluator, the `max|min` reduction, the UI-18 fallback and the
bounded batch. MCP `get_status` composes the same `GlanceReading`s and the web
layer only wraps them into `TileGlance` (formatted strings, SVG meter
geometry), so a formatting change cannot alter selection and MCP never imports
a Starlette application (MC-01, issue #64). `GlanceReading` is raw: consumers
that want text call the web formatter, and the MCP payload deliberately omits
display strings so a model cannot parse `"94%"` where `94.0` and `percent` are
available. The batch sorts by monitor name and caps at 64 records with
always-present truncation metadata; the dashboard renders every tile it has and
is unaffected by the cap.

The Events source has no sampled entity of its own, so its fixed operational
glance reads the fresh `self/ftmon/event_rate_per_min` series and labels it
`ingest … events/min`. It has no threshold meter and cannot change tile state;
the same freshness and trustworthy-state gates apply (UI-18).

Reference definitions deliberately choose like-for-like signals: disk maximum
used percent; load five-minute CPU PSI; hog maximum five-minute CPU; leak
maximum RSS growth rate; temperature hottest Celsius value; and iowait its
fifteen-minute average. Threshold labels appear only when their parameter uses
the same metric and window. Status-only recipes such as `sensors` omit glance
instead of inventing a numeric interpretation.

#### M7 disk reference profile

The disk detail view uses three synchronized uPlot panels rather than one overloaded dual-axis chart:

1. capacity (`used_pct`) with notice/warning/error thresholds and the selected rollup's min/max envelope;
2. signed `fill_rate_bph` with a zero line, plus `filling` on its own fixed 0..1 confidence scale;
3. qualified hours-to-full, rendered as gaps whenever projection prerequisites fail, with incident transition markers shared across all panels.

Separation is deliberate: percentage, bytes/time, confidence, and time remaining have different units and failure modes. Putting them on one axis makes cleanup look like growth and enormous sentinel forecasts dominate autoscaling. Synchronized cursors preserve correlation without conflating scales.

`disk.toml` adds the persisted derived metric `fill_rate_bph = slope(used_bytes, "70m") * 3600`. Persisting the signed rate means long-range charts use the value evaluated against the original ring window, not a derivative of visually downsampled points (DM-17). `full_in_h` remains available for compatibility, but the trend API converts unqualified values to `null`; no database migration is required because derived metrics already use the generic series tables. Pre-amendment history simply has no rate series and is reported as unavailable rather than reconstructed inaccurately from coarse rollups.

`Query.series` gains keyword-only `statistic="avg"` and `include_envelope=False`. Raw points use their value for every statistic; rollups select the named stored column, and envelopes return stored `min/max`. Downsampling occurs after column selection. `Query.disk_trend` aligns series by timestamp, returns explicit units/resolution/coverage and incident markers, and qualifies each projection from persisted rate + confidence. It never linearly fills absent buckets.

Routes: `GET /disks` · `GET /disks/{entity:path}` · `GET /api/disk-trend?entity=…&range=…`. Range state lives entirely in the URL. The JSON contract is `{entity, range, resolution, panels, thresholds, incidents, summary}`; each panel supplies columnar uPlot data and units. Server-rendered summary text is authoritative for UI-11 and remains useful when JavaScript is unavailable.

uPlot remains D4's renderer: its small vendorable footprint, temporal scales, cursor synchronization, and high/low bands match this bounded local use. ECharts was rejected as a much broader dependency; Chart.js duplicates server-side decimation and is less specialized for synchronized dense time series. LTTB stays the general shape-preserving cap, while extrema remain separately visible through the rollup envelope rather than relying on LTTB to retain every spike.

---

## 16. Test infrastructure design (TS-01..15)

- **Traceability**: `tools/gen_reqindex.py` regexes SPEC.md for `**XX-nn**` → `tests/reqindex.json` (committed); IDs listed in `NON_TESTABLE = {NG-*, DO-*, …}` are exempt. `tests/test_traceability.py` scans all test docstrings for `[XX-nn]` markers and fails on uncovered IDs (TS-01).
- **Scenario format** (TS-04), JSONL, one file per case in `tests/scenarios/`:

```jsonl
{"at": 0,   "source": "process", "entities": [{"id": "leaky:101:1000", "attrs": {"name": "leaky", "cmdline": "./leaky"}, "metrics": {"rss_bytes": 1.0e8, "cpu_pct": 1.0}}]}
{"at": 60,  "source": "process", "entities": [{"id": "leaky:101:1000", "metrics": {"rss_bytes": 1.2e8}}]}   # attrs sticky, metrics merge
{"at": 90,  "event": {"source": "journald", "provider": "kernel", "severity": 3, "message": "Out of memory: ..."}}
{"at": 0,   "generate": {"source": "process", "count": 300, "churn_per_min": 5, "rss": [1e7, 5e8], "cpu": [0, 4]}}   # proc-churn-300
```

  `sources/fixtures.py` implements `Sampler`/`EventSource` over these files: a fixture snapshot is the merge of all records ≤ now for that source; `generate` uses a seeded RNG (seed in the file) for reproducibility.
- **Tier-1 harness**: pytest fixture `daemon_proc(scenario, config)` → temp `$FTMON_*` dirs, spawns `ftmon daemon --clock controlled --fixtures <file>`, returns a `Ctl` object (`step(seconds)` drives the clock socket in 5 s ticks and waits for acks; `db()`, `notifications()`, `cli(*args)` helpers). Kill-9 test: `ctl.kill9(); ctl.restart(); assert dup_count ≤ 1` (NO-04/TS-05).
- **Tier-2**: `@pytest.mark.realsystem`, driven by `tests/e2e_real/`; asserts via `notifications.jsonl` and `ftmon --json` outputs only (TS-08).
- **External checks** (TS-15): parser/runner unit tests create temporary local
  executables with fixed bytes, exit status, child-process and clock behavior;
  no installed Nagios package or network is used. Tier-1 fixtures may provide a
  `check` record containing raw protocol output and elapsed duration, but the
  principal journey launches the real bounded runner under ControlledClock so
  argv isolation, one-run alias sharing, mapped persistence and Trend exposure
  are tested together. A packaging smoke may invoke an administrator-supplied
  plugin only under `realsystem` and is never required for compatibility.
- **Lint tests**: grep-tests for direct `time.time|datetime.now|time.monotonic` outside `clock.py` (TS-03) and for forbidden imports in `expr/` (EX-04) / layering (§1).
- **Doc-version coherence** (TS-19): `test_traceability.py` parses the SPEC
  `Status:` header, the newest §21 entry, and this document's companion
  reference and fails on any mismatch — regex over the committed files, no
  new tooling.
- **Soak evidence** (TS-17): no new instrumentation — the daemon already
  records everything the gate needs (RB-02 self metrics, incidents,
  `notification_deliveries`, DB size). A small `tools/soak_report.py` reads a
  live or copied-via-backup-API DB and emits one markdown report: budget
  percentiles from `self` history, retention-cycle DB-size curve, outbox
  drain lag, restart count from daemon-start self events, unexplained
  `self` incidents, and the closing `ftmon doctor` output. The report file is
  the release-notes artifact; asserting from stored history (not external
  top/ps sampling) is deliberate — it exercises the same query path users trust.

---

## 17. Security implementation notes (SE-*)

Jinja autoescape + CSP (SE-02); notification bodies strip control chars; CLI output escapes via `repr`-safe rendering for untrusted strings. `attrs` JSON stored with `ensure_ascii=False` but rendered escaped. Action runner: `subprocess.run(env=minimal, timeout=30, close_fds=True, cwd=state_dir)`, never a shell (AC-02). File modes via `os.open(..., 0o600)` in `atomic_write`; dirs `0o700` at init (SE-04/PM-06).

Remote adapter constructors receive resolved secrets through a `SecretValue`
wrapper whose `repr`/`str` is always `<redacted>`; only the adapter's private
header/auth call can reveal bytes. `SecretRef.resolve()` rejects symlinks,
unsafe ownership/mode, NUL/newline in header credentials, and missing/empty
values. HTTP diagnostics retain only scheme + hostname + status category, never
path/query/user-info. Webhook JSON and email bodies use the already bounded
NO-01 rendered fields, not raw events or process attributes (SE-05).

Demo and operational web factories share rendering but not authority-bearing
dependencies or route construction. This structural split is why a future
template or navigation change cannot accidentally expose a POST in public mode;
TS-14 inspects the actual route methods and app state, not just returned HTML.

External checks never reuse the action runner: actions are incident effects and
external checks are evidence producers with different scheduling, output and
failure semantics. They share only low-level rationale—no shell, hard timeout,
minimal environment, output caps. Registry ownership is outside monitor drafts;
`define_monitor` may emit `source = "external"` only with an existing alias and
cannot inspect argv. Plugin output is untrusted display/template input. Nagios
plugins execute as separate processes and are not linked, imported or
distributed by FTMON; the MIT package contains only the adapter, while every
plugin remains under its own license (EC-01/02/07/09, SE-07).

---

## 18. Design decisions log

| # | Decision | Why (alternatives) |
| --- | --- | --- |
| D1 | Single write txn per tick | PM-03 simplicity; WAL readers unaffected (vs per-write commits: fsync storm) |
| D2 | `WITHOUT ROWID` + interned `series` table | ~2× row-size saving; makes §9 close (vs naive text columns: >500 MB) |
| D3 | Confirm counters in-memory only | restart cost = one confirmation delay; avoids chatty persistent counter writes |
| D4 | starlette+jinja2+htmx+uPlot | UI-06 no-SPA mandate; all vendorable; smallest competent stack |
| D5 | argparse over click | zero-dep, weak-model-friendly, stable help text (DO-03) |
| D6 | Narrow I/O threads; synchronous fakes | event readers and M8 delivery worker cannot block sampling; fixtures bypass both boundaries for determinism |
| D7 | LTTB downsampling in query layer | one implementation serves UI-05 and MCP |
| D8 | Store-filter for events (SPEC v0.3) | capacity worksheet §9; full journal storage impossible in 200 MB |
| D9 | Hourly-rollup durable/ephemeral split (SPEC v0.3) | §9; process churn dominates otherwise |
| D10 | Persist signed fill rate; qualify projections at presentation | derivatives of downsampled history and clamped sentinel forecasts are misleading (DM-17/CA-09) |
| D11 | Three synchronized disk panels | preserves distinct units/scales while keeping temporal correlation (UI-10) |
| D12 | Declarative trend profiles, not name inference | names cannot establish units, limits, confidence meaning, or honest projection semantics (MD-10) |
| D13 | One explorer plus contextual links | supports discovery and incident investigation without duplicate query/render paths (UI-12) |
| D14 | Shared uPlot adapter, distinct page semantics | one historical rendering truth while preventing arbitrary metrics from acquiring invented trend meaning (UI-13) |
| D15 | Tile state composed in Python with fixed precedence | prevents templates/colors from becoming hidden health policy; preserves ack and stale semantics (UI-14) |
| D16 | New local mark plus real-text wordmark | preserves a hint of the legacy lavender identity without carrying forward a low-resolution asset; packaged variants keep the UI offline, while text retains accessibility and graceful failure |
| D17 | Explicit init profiles, not runtime personality | server-friendly defaults are inspectable configuration and cannot create hidden behavior after installation |
| D18 | Immutable notification plus per-channel deliveries | one channel's success cannot conceal another's failure; freezes fan-out at the incident transaction boundary |
| D19 | Small first-party channel set | ntfy, JSON webhook, and SMTP cover the single-server use case without inheriting a large plugin/dependency and credential surface |
| D20 | Separate read-only demo app and marked synthetic DB | route omission and immutable storage are stronger public-safety boundaries than hiding controls in templates |
| D21 | Registered subprocess checks, not an in-process plugin API | reuses user scripts and the Nagios ecosystem while crashes, dependencies and licenses remain outside the daemon |
| D22 | Explicit perfdata mappings before persistence | prevents output-driven schema growth/high cardinality and makes units, counter semantics, expressions and Trends honest |
| D23 | Registry authority separate from monitor definitions | AI/user drafts can compose rules around an approved check but cannot introduce executable paths, argv or credentials |
| D24 | v1.0 gated on recorded soak + empty pending list (TS-17/18) | the codebase was built in days; deterministic CI proves logic, not longevity — a monitor's core claims (bounded RSS/DB, durable retry, quiet operation) are only credible after real wall-clock time, and untested SE-* requirements are the riskiest kind of pending |
| D25 | Doc-drift audit is a manual recorded pass, not tooling (DO-09) | prose docs can't be regex-traced like requirement IDs; a per-milestone checklist executed and recorded costs less than building doc-testing machinery for four documents |
| D26 | Recipe authority and Exchange publisher stay in one repository | monitor-schema changes and compatible recipes merge atomically; a second repository would create version skew before independent governance is needed |
| D27 | Exchange is generated static documentation, not a submission application | pull requests provide identity, review and history without accounts, uploads, moderation storage or an executable marketplace attack surface |
| D28 | Safe subset renderer with deterministic output | contributor prose cannot inject active content and byte-identical artifacts make review, caching and rollback auditable |
| D29 | Tool-neutral canonical skills with vendor installation adapters | one reviewed workflow serves multiple agents without promising universal auto-discovery or maintaining divergent copies |
| D30 | Shared skills read live repository authority | avoids freezing schema details in prompt assets; SPEC, templates and tests remain the only behavioral contract |
| D31 | Container monitoring stays behind the external-check boundary in 2.0 | a rootful engine socket is effectively administrative authority and conflicts with SE-01; a recipe can use a pre-existing same-user rootless socket without adding a daemon dependency, while canary evidence must justify any post-2.0 per-container sampler/event design |
| D32 | Explicit per-monitor glance metadata | multi-entity monitors and arbitrary rules cannot honestly reveal one primary value, unit, aggregate or threshold label by inference |

---

## 19. Milestone → work-package skeleton

Detailed WPs (with frozen file lists + pre-written tests) follow in TESTPLAN.md; the cut is:

- **M1**: WP1 paths/clock/model · WP2 expr (parse/eval/functions/tribool) · WP3 schema/loader · WP4 store (db/migrations/writer/query) · WP5 sources process/disk/system + fixtures · WP6 scheduler+pipeline (no incidents) · WP7 CLI check/status/query · WP8 traceability tooling.
- **M2**: WP9 incident engine · WP10 outbox+notifiers · WP11 retention/rollups/baselines · WP12 builtins leak/hog/disk/load/self + tier-1 harness + scenario library.
- **M3**: WP13 journald+event pipeline · WP14 events/service/net builtins + unit/net sources.
- **M4**: WP15 MCP server. **M5**: WP16 web UI. **M6**: WP17 actions+doctor+tier-2+docs.
- **M7**: WP18 historical query envelopes + signed disk rate · WP19 uPlot disk views/API + Tier-1 visualization contract tests.
- **M7.1**: WP20 trend-profile schema/loader + generic query · WP21 Trends explorer, leak reference profile, contextual links + Tier-1 contract tests.
- **M7.2**: WP22 generic series API + shared chart adapter · WP23 Metrics uPlot view, incident markers, summaries, Trend links + tests.
- **M7.3**: WP24 dashboard tile view model + monitor-filtered incidents · WP25 accessible state CSS/template + HTTP tests.
- **M8**: WP26 config profiles + secret references + outbox migration · WP27
  dispatcher and ntfy/webhook/SMTP adapters + deterministic failure tests · WP28
  hardened server unit, doctor status, server/channel documentation + real-system
  smoke tests.
- **M8.1**: WP29 versioned demo scenario + immutable builder · WP30 separate
  GET-only app factory/CLI and security tests · WP31 Caddy/systemd/timer deployment
  documentation and `demo.ftmon.org` release checklist.
- **M9**: WP32 check registry + path/security/reload validation · WP33 bounded
  runner + Nagios/FTMON-JSON parsers · WP34 dynamic perfdata declaration,
  external sampler and scheduler fairness · WP35 controlled-clock
  state/perfdata-to-Trend journey, doctor/CLI/MCP/web contracts and external
  check authoring/reuse documentation.
- **M9.1**: WP36 recipe schema/template + discovery validator · WP37 HTTP/TLS
  and constrained SMART/NVMe Nagios recipes · WP38 maintained native JSON
  script, direct tests, catalogue/user-documentation links.
- **M9.2**: WP42 publication metadata + safe deterministic generator · WP43
  catalogue/detail/search/security tests · WP44 Pages workflow, custom-domain
  runbook, local preview and rollback documentation.
- **M9.3**: WP45 portable skill contract + `ftmon-add-extra-monitor` · WP46
  structural/semantic skill tests · WP47 Codex/Claude installation and trust
  documentation.
- **M10**: WP39 `tools/soak_report.py` + soak procedure/evidence template
  (TS-17) · WP40 pending-traceability burn-down, SE-* first, then UI-*/PL-*,
  then the remainder; each ID gains a test or a documented spec amendment
  (TS-18) · WP41 doc-drift + external-claim audit checklist, repo hygiene
  (review artifacts out of `docs/`, root limited to living documents),
  dependency-deprecation sweep (DO-09).

Each WP names its FROZEN interfaces from §4–5; an implementing model receives: SPEC excerpt, this document's relevant sections, the WP's test files, and the interface stubs — nothing else is in scope for it.

# FTMON v2 — Design

Status: **DRAFT v0.1**. Companion to `SPEC.md` v0.3 — every design element cites the requirement(s) it satisfies. Where this document says FROZEN, implementers MUST NOT alter names, signatures, or semantics; changes go through this document first.

Design-phase artifacts:

- this document;
- `design/builtins/*.toml` — the eight built-in monitor definitions (MD-07), normative;
- two SPEC amendments recorded in SPEC §21 (v0.3): hourly-rollup retention split and event store-filter, both forced by the capacity worksheet (§9 here).

`TESTPLAN.md` and per-milestone work packages are the next phase and build on §16.

---

## 1. Repository and package layout

```
PROJECTS/ftmon/                  # monorepo root (git)
├── SPEC.md  DESIGN.md  TESTPLAN.md(next)  LICENSE(MIT)
├── pyproject.toml  uv.lock      # single Python project at repo root
├── design/
│   └── builtins/*.toml          # normative built-in defs; copied into package data by WP
├── src/ftmon/                   # the package (SPEC §3)
│   ├── paths.py                 # FS-01: all filesystem paths (platformdirs)
│   ├── clock.py                 # TS-03: Clock protocol + SystemClock + ControlledClock
│   ├── model.py                 # §4 core dataclasses (FROZEN)
│   ├── expr/                    # EX-04: stdlib-only, imports nothing from ftmon.*
│   │   ├── parse.py  eval.py  functions.py  tribool.py
│   ├── definitions/
│   │   ├── schema.py            # MD-01 validator (single source of truth)
│   │   ├── loader.py            # TOML → MonitorDef, normalization, topo-sort (MD-08)
│   │   └── builtins/*.toml      # package data, installed by `ftmon init` (FS-02)
│   ├── sources/
│   │   ├── base.py              # Sampler/EventSource protocols + SourceDecl (PL-05)
│   │   ├── process.py disk.py system.py net.py unit.py selfsrc.py
│   │   ├── journald.py          # linux EventSource
│   │   └── fixtures.py          # TS-04 scenario-driven fakes (ship in prod pkg: PL-04)
│   ├── engine/
│   │   ├── scheduler.py         # SA-01 tick loop
│   │   ├── pipeline.py          # SA-06 source→snapshot→project→derive→rules
│   │   ├── rings.py             # CA-04 ring buffers
│   │   ├── baseline.py          # CA-05
│   │   ├── incidents.py         # IN-06 pure state machine (FROZEN)
│   │   └── effects.py           # effect executor: outbox, actions (AC-*), notify dispatch
│   ├── store/
│   │   ├── db.py                # connection factory, pragmas, migrations runner
│   │   ├── migrations/0001_init.sql …
│   │   ├── writer.py            # daemon-side batched writes
│   │   ├── query.py             # DM-06 tier-transparent reads (shared by CLI/MCP/web)
│   │   ├── retention.py         # DM-04/05 rollups, prune, vacuum
│   │   └── outbox.py            # NO-04
│   ├── notify/base.py desktop.py file.py
│   ├── daemon.py                # composition root; owns the only bulk-write connection
│   ├── mcp_server.py            # §13
│   ├── web/                     # §14: app.py, routes.py, templates/, static/vendor/
│   ├── selfmon.py               # RB-02 self metrics collection
│   └── cli.py                   # §15 argparse tree, every subcommand
├── tests/                       # §16; mirrors src layout + e2e/ + scenarios/
├── tools/gen_reqindex.py        # TS-01 traceability index generator
├── docs/definitions.md install.md
└── ftmon-legacy/                # untouched reference (GPLv2)
```

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

The daemon is synchronous and single-threaded except: each `EventSource` owns one reader `subprocess` + one stdlib `threading.Thread` that only moves lines from the pipe into a `collections.deque(maxlen=10_000)` (SA-08). No other threads. All parsing/normalization happens on the main thread at drain time, keeping determinism (fixtures bypass the thread entirely).

Confirm/clear counters (IN-01) are in-memory only; a daemon restart loses in-progress confirmation and re-accumulates (documented, acceptable — incidents and backoff state survive via DB per IN-02/DM-14).

---

## 3. Filesystem & configuration (FS-01, PM-06)

`paths.py` exposes a frozen `Paths` dataclass built once from `platformdirs` + `$FTMON_*` env overrides (tests use temp dirs via env). `config.toml` keys (complete v1 set): `[daemon] tick_seconds=5, gone_grace="5m"`, `[privacy] collect_cmdline=true`, `[quiet_hours] enabled=false, start="22:30", end="07:30"`, `[web] port=8420`, `[retention]` overrides, `[notify] desktop=true`.

Atomic write helper `paths.atomic_write(path, bytes)` (tmp + fsync + rename, 0600) is the only function that writes into the config tree (PM-06a/b); loader rejects symlinks (PM-06c).

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
@dataclass(frozen=True) class Snapshot:            # SA-06: one ts for all entities
    source: str; ts: float; entities: tuple[EntitySample, ...]

@dataclass(frozen=True) class EventRecord:          # DM-07/08
    ts: float; ingest_ts: float; source: str; provider: str
    event_id: str | None; severity: int; message: str; attrs: Mapping[str, str]

@dataclass(frozen=True) class Notification:        # NO-01
    incident_id: int; kind: Literal["open","escalate","renotify","recover","digest"]
    severity: int; title: str; body: str; created_ts: float

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
def step_episode(cfg: EpisodeConfig, st: GroupState, matches: Sequence[EventRecord],
                 now: float) -> tuple[GroupState, tuple[Effect, ...]]           # IN-08

# storage facade (all non-daemon processes use only Query + SmallWrites)
class Query:      # DM-06; shared by CLI/MCP/web
    def series(self, monitor, metric, entity=None, start=..., end=..., max_points=2000) -> SeriesResult
    def top(self, resource, start, end, n) -> ...
    def events(self, filters) -> ...
    def incidents(self, filters) -> ...
    def incident_detail(self, id) -> ...            # explain_incident substrate
    def monitors(self) -> ...
    def status(self) -> StatusResult                # PM-01 liveness = age of meta.last_tick_ts
class SmallWrites:
    def ack(self, incident_id, by, note) -> None    # PM-03 short txn
```

`ControlledClock` (tests): listens on `$FTMON_CLOCK_SOCK` (unix socket, line-JSON `{"op":"step","s":5}` / `{"op":"set","wall":…,"mono":…}`); `sleep_until` blocks on the socket; the daemon replies `{"ok":true,"tick":N}` **after** completing the tick, so harness steps are synchronous (TS-05 determinism).

---

## 6. Expression module design (EX-01..07)

- `parse.py`: `ast.parse(text, mode="eval")`; walk with an allowlist visitor (exact node list EX-01, kwargs rejected EX-05); output is a private IR (nested frozen dataclasses) — the evaluator never touches `ast` nodes again. Regexes found in `matches()` are compiled here (EX-07) and pattern length checked.
- Name resolution (EX-02) happens at compile time against `NameEnv`; the IR stores slot kinds (`metric|attr|param|const`) so eval does no dict lookups on strings the author controls.
- `eval.py`: small recursive interpreter over the IR. All binary/unary/compare ops route through `tribool.py` helpers implementing the EX-06 truth table verbatim (one function per table row group; the unit tests mirror the table). Division/modulo by zero, NaN/inf results → `UNKNOWN` + a counter callback. A `deadline_check()` closure is consulted every N=64 IR nodes (EX-03's 10 ms cap).
- `functions.py`: the CA-01 table. Series functions take `(ctx, metric_slot, window_seconds)`; `slope` = numerically stable least squares over (t−t₀); `monot` counts consecutive positive deltas / (n−1). `CompiledExpr.windows` is the union of all (metric, window) references — the loader aggregates these per monitor to size ring buffers (CA-04) and to reject > 6 h / >10 000-point windows.
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
| `parameters.*` | `{value: num, doc: str}` | all |
| `promotion.expr` | expression (bool) — SA-05(c) heuristic | process |
| `derived[].name/expr` | metric name / expression | sampler sources |
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

Registered sources v1: `process, disk, system, net, unit, self, events`. The `self` source is registered like any other (RB-02) — its `SourceDecl` lists the §10.6 metrics.

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

CREATE TABLE outbox(    id INTEGER PRIMARY KEY, incident_id INT, kind TEXT, body TEXT,
  created_ts INT, delivered_ts INT, stale INT DEFAULT 0);    -- DM-14/NO-04
CREATE INDEX outbox_undelivered ON outbox(created_ts) WHERE delivered_ts IS NULL;

CREATE TABLE baselines( series_id INTEGER PRIMARY KEY, value REAL, updates INT,
  updated_bucket INT) WITHOUT ROWID;                         -- CA-05
CREATE TABLE cursors(   source TEXT PRIMARY KEY, cursor TEXT, updated_ts INT) WITHOUT ROWID;
CREATE TABLE monitor_loads(monitor TEXT, loaded_ts INT, hash TEXT, normalized TEXT,
  PRIMARY KEY(monitor, loaded_ts)) WITHOUT ROWID;            -- PM-07 (keep last 20/monitor)
```

Write path: `writer.py` accumulates the tick's samples/events/incident rows and commits **one** transaction at step 4 of the tick (PM-03). Outbox rows for incident transitions are part of that same transaction (NO-04); delivery and `delivered_ts` update happen after commit.

---

## 9. Capacity worksheet (DM-16) — and the two SPEC amendments

Assumptions (become validation limits): ≤ 400 persisted entities; active persisted series ≈ **270** (top-15 procs × 6 metrics + ~10 promoted × 6 + ~10 watchlist × 6 + disk 6 mounts × 5 + system 12 + net 8 + self 12); 60 s intervals; WITHOUT ROWID sample row ≈ 35 B, rollup row ≈ 45 B effective (incl. b-tree overhead); stored events ≈ 2 000/day at ≈ 350 B.

| Store | Rows | Size |
| --- | --- | --- |
| raw 48 h | 270 × 2 880 ≈ 0.78 M | ≈ 27 MB |
| 5-min 30 d | 270 × 288 × 30 ≈ 2.33 M | ≈ 105 MB |
| 1-h, durable series (≈ 90) × 400 d | 0.86 M | ≈ 39 MB |
| 1-h, process series × **90 d** | ≈ 0.39 M | ≈ 18 MB |
| events 30 d (filtered) | 60 k | ≈ 21 MB |
| incidents + history + misc | — | ≈ 5 MB |
| **Total steady state** | | **≈ 215 MB → DM-05 trims 5-min tail to land < 200 MB** (effective ~27 d of 5-min data; honest and self-correcting) |

Two findings forced SPEC amendments (recorded as v0.3):

1. **Hourly rollups for all series for 400 d** would cost ≈ 115 MB alone (process-entity churn). Amended DM-04: 400 d hourly retention applies to *durable* series (system, disk, self, watchlist-synthetic); process-sourced series keep 90 d hourly.
2. **Storing all journal events** (50–200 k lines/day on a desktop) would blow the budget within days. Amended DM-09: the event store-filter keeps events with severity ≥ notice **or** matching any loaded event rule; info-level non-matching events are counted (self-metric) but not stored. Configurable `store_min_severity`.

Ring-buffer RAM (CA-04): worst case all-processes window = 300 procs × 2 metrics × 15 samples × 32 B ≈ 0.3 MB; promoted/watchlist long windows: 40 series × 720 points × 32 B ≈ 0.9 MB; comfortably inside the 64 MB cap; cap exists for pathological definitions.

---

## 10. Engine design

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
persist: samples for persisted series only (SA-05 promote/top-N/watchlist)
gone-detection: entities seen before but absent → CA-08 grace timer
```

### 10.3 Promotion (SA-05)

The process source keeps its own all-process short window (15 samples) in `rings` under a non-persisted namespace. After each cycle, `promotion.expr` (from `leak.toml` et al.) is evaluated per process against that window; newly-true → promote (start persisting + full ring), false for 30 min → demote. Transitions → self-events.

### 10.4 Incident engine (IN-01..08) — pure

`step_group` implements the SPEC §9.1 diagram exactly; `GroupConfig` carries per-rung `severity, confirm, clear, message-template-id, action, notify_recovery` + backoff table `(300, 900, 3600, 21600)` (IN-02). Backoff/renotify decisions derive from `IncidentCore.last_notify_ts/backoff_tier` — the caller rebuilds `IncidentCore` from DB at startup, which is how restarts keep the schedule (IN-02). `step_episode` shares `IncidentCore` and differs only per IN-08 (cooldown gate, `clear_after` timer via `now − last_seen`). Effects are executed by `effects.py`: `NotifyEffect` → outbox insert (in-txn) then post-commit delivery; `ActionEffect` → AC-02 subprocess with env, recorded to history.

### 10.5 Baselines & retention slices

`baseline.py` hooks the 5-min rollup job: for each rolled bucket, apply the CA-05 EW update (`α = 1 − 2^(−300/259200)` per 5-min step at the 3 d half-life), increment `updates`. `retention.py` runs ≤ 1 s/tick with cursors in `meta`: rollup 5m → rollup 1h → prune per DM-05 order → `incremental_vacuum(200 pages)`. Weekly full VACUUM only when daemon idle and DB fragmented > 20 %.

### 10.6 Self source (RB-02)

Metrics: `cpu_pct, rss_bytes, db_bytes, cycle_s, sampler_s{per-source attr}, tick_overruns, event_queue_depth, events_dropped, events_unstored, ring_mem_bytes, source_activity_age_s, eval_unknown_total, samples_rejected`. Fed from a `SelfStats` struct the daemon updates in place; sampled like any source.

---

## 11. Event pipeline (SA-03/08, DM-07..10, DM-15)

`journald.py`: spawns `journalctl -f -o json --output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT,__CURSOR [--after-cursor=C]`. Reader thread appends raw lines to deque. `drain()` (main thread): parse JSON (malformed → count, skip), normalize → `EventRecord` (severity map: PRIORITY 0–2→critical, 3→error, 4→warning, 5→notice, 6–7→info; provider = `_SYSTEMD_UNIT` else `SYSLOG_IDENTIFIER`), return last `__CURSOR`. Cursor is persisted in the tick's write txn (DM-15). Storm counter per (source, provider) sliding minute (DM-10); store-filter per amended DM-09; matching against loaded event rules uses the same compiled `when` expressions with the event-field NameEnv. Reader death → `alive()` false → scheduler restarts with backoff (SA-03).

---

## 12. Query layer (DM-06, UI-05)

Tier choice: `end > now−48h and span ≤ 12h` → raw; `span ≤ 30d` → 5m; else 1h — then if points > max_points, server-side LTTB downsample to max_points (used by web charts and MCP alike). All timestamps out are UTC ints + one `tz: "<IANA>"` field per response (MC-02).

---

## 13. MCP server (`mcp_server.py`, MC-01..05)

FastMCP over stdio; every tool = thin wrapper on `Query`/`SmallWrites`/`definitions`. Parameter schemas (FROZEN; `range` = duration string or `[iso, iso]`):

| Tool | Params (required bold) | Returns |
| --- | --- | --- |
| get_status | — | daemon alive/last_tick_age, monitors[], open_incidents, self_metrics |
| query_metrics | **monitor, metric, range**; entity, agg(avg\|min\|max\|last), filter_expr | series[] {entity, points[[ts,v]]}, resolution, tz |
| top_consumers | **resource(cpu\|rss\|io), range**; n=10 | ranked[] {entity, attrs, agg_value} |
| get_process_history | **name_or_pid, range** | entities[] {entity_id, attrs, first/last/gone, series{…}} |
| list_events | **range**; min_severity, provider, match_expr, limit=200 | events[] |
| list_incidents | — ; state, range, monitor | incidents[] summary |
| explain_incident | **id** | rule text+params, series ±window, events ±10 m, history[] |
| list_monitors / get_monitor | — / **name** | defs + state + validation + load history |
| validate_monitor | **toml_text** | {ok} \| {errors[]: {path, code, message, hint}} |
| define_monitor | **toml_text** | {draft_path, approval_hint} \| errors as above |
| ack_incident | **id**; note | {ok, incident} |

Errors: `{code, message, hint}` (MC-04) with codes `invalid_params, validation_failed, not_found, name_exists, daemon_stale`. Resource (MC-05): `ftmon://docs/definitions` → `docs/definitions.md`.

---

## 14. Web UI (`web/`, UI-01..09)

Starlette app; Jinja2 (autoescape); htmx for partial refresh (dashboard/incidents poll `/partials/*` every 5 s, UI-04); uPlot for charts fed by `/api/series` (JSON from `Query`, ≤ 2 000 pts). Middleware enforces UI-08: Host allowlist else 400; POST requires matching Origin; headers `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.

Routes: `GET /` dashboard · `GET/POST /incidents[/{id}][/ack]` · `GET /metrics` explorer (state in query string, UI-02) · `GET /events` · `GET /monitors[/{name}]`, `POST /monitors/{name}/(enable|disable)`, `POST /drafts/{name}/(approve|delete)` · `GET /self` · `GET /partials/(tiles|incidents|health)` · `GET /api/series`. Templates: `base.html` + one per page + partials; severity rendered as `<span class="sev sev-error">▲ error</span>` (icon + text, UI-09); charts get a `<figcaption>` text alternative (current value + trend sentence from `slope`).

---

## 15. CLI (`cli.py`, CL-01..05)

argparse tree; every subcommand is a function taking `(Paths, Query|…, argparse.Namespace)` so tests call them directly. Mapping: `daemon→daemon.run`, `mcp→mcp_server.run`, `web→web.run`, `init→definitions.install_builtins`, `check→definitions.check_cli` (CL-02), `status/top/incidents/incident/events/query/monitors→store.query` renderers (each with `--json`, CL-03; `status` exit codes per CL-04), `ack/monitor approve|enable|disable→SmallWrites/definitions`, `baseline reset→store`, `doctor→store.doctor` (CL-05: quick_check/--deep, WAL checkpoint, sizes, cursor ages, orphans, `--backup` via `sqlite3.Connection.backup`).

---

## 16. Test infrastructure design (TS-01..08)

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
- **Lint tests**: grep-tests for direct `time.time|datetime.now|time.monotonic` outside `clock.py` (TS-03) and for forbidden imports in `expr/` (EX-04) / layering (§1).

---

## 17. Security implementation notes (SE-*)

Jinja autoescape + CSP (SE-02); notification bodies strip control chars; CLI output escapes via `repr`-safe rendering for untrusted strings. `attrs` JSON stored with `ensure_ascii=False` but rendered escaped. Action runner: `subprocess.run(env=minimal, timeout=30, close_fds=True, cwd=state_dir)`, never a shell (AC-02). File modes via `os.open(..., 0o600)` in `atomic_write`; dirs `0o700` at init (SE-04/PM-06).

---

## 18. Design decisions log

| # | Decision | Why (alternatives) |
| --- | --- | --- |
| D1 | Single write txn per tick | PM-03 simplicity; WAL readers unaffected (vs per-write commits: fsync storm) |
| D2 | `WITHOUT ROWID` + interned `series` table | ~2× row-size saving; makes §9 close (vs naive text columns: >500 MB) |
| D3 | Confirm counters in-memory only | restart cost = one confirmation delay; avoids chatty persistent counter writes |
| D4 | starlette+jinja2+htmx+uPlot | UI-06 no-SPA mandate; all vendorable; smallest competent stack |
| D5 | argparse over click | zero-dep, weak-model-friendly, stable help text (DO-03) |
| D6 | Reader thread + deque only | keeps daemon single-threaded logically; fixtures bypass thread → determinism |
| D7 | LTTB downsampling in query layer | one implementation serves UI-05 and MCP |
| D8 | Store-filter for events (SPEC v0.3) | capacity worksheet §9; full journal storage impossible in 200 MB |
| D9 | Hourly-rollup durable/ephemeral split (SPEC v0.3) | §9; process churn dominates otherwise |

---

## 19. Milestone → work-package skeleton

Detailed WPs (with frozen file lists + pre-written tests) follow in TESTPLAN.md; the cut is:

- **M1**: WP1 paths/clock/model · WP2 expr (parse/eval/functions/tribool) · WP3 schema/loader · WP4 store (db/migrations/writer/query) · WP5 sources process/disk/system + fixtures · WP6 scheduler+pipeline (no incidents) · WP7 CLI check/status/query · WP8 traceability tooling.
- **M2**: WP9 incident engine · WP10 outbox+notifiers · WP11 retention/rollups/baselines · WP12 builtins leak/hog/disk/load/self + tier-1 harness + scenario library.
- **M3**: WP13 journald+event pipeline · WP14 events/service/net builtins + unit/net sources.
- **M4**: WP15 MCP server. **M5**: WP16 web UI. **M6**: WP17 actions+doctor+tier-2+docs.

Each WP names its FROZEN interfaces from §4–5; an implementing model receives: SPEC excerpt, this document's relevant sections, the WP's test files, and the interface stubs — nothing else is in scope for it.

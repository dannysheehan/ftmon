# FTMON v2 — Code Simplicity & Dead-Code Audit

Date: 2026-08-10 (rev. 2 — corrected per maintainer review)
Scope: `src/ftmon/` (≈18,700 LOC) compared against `SPEC.md` and `DESIGN.md`.
Method: full lint (`ruff check` — clean), targeted grep + read-through of every
module family, parallel deep-dive agents on the large top-level modules.

## Executive summary

FTMON v2 is unusually disciplined and internally consistent. There is almost no
duplication of *security-critical* logic — the hardened HTTP opener
(`notify/http.py`) and the trust predicates (`checks/trust.py`) are correctly
single-sourced, and the layering rule is enforced by a lint test. The system is
lean relative to its scope. Ruff passes; there are no unused imports or
wholesale-unused modules; every runtime dependency is used.

The findings below are marked with a verdict reflecting maintainer review:

| Finding | Verdict |
| --- | --- |
| Watchlist durability (§3.1) | **Agree — real, high-priority bug** |
| DESIGN/tree drift (§2) | **Agree** |
| Daemon connection not explicitly closed (§3.2) | **Agree, medium/low severity** |
| Ctrl-C logged as a crash (§3.3) | **Disagree — original claim incorrect** |
| Writer ID gaps (§3.5) | **Technically true, not worth fixing by itself** |
| `glance.py` private SQL (§3.6) | **Agree with moving queries; evidence semantics need separate design** |
| Confirmed dead code (§1) | **Mostly agree** |
| Large-scale deduplication (§4) | **Mixed; be selective** |
| Overall architecture assessment | **Agree** |

Bottom line: the architecture is not over-engineered for its goals. The honest
"could it have been simpler" answers are the process/traceability tooling (a
deliberate governance bet) and a moderate amount of cross-module boilerplate.
The claimed ~600–900-line savings figure from the first revision is **not
supported by measurement** and is withdrawn; see §5.

---

## 1. Confirmed dead code

All verified by repo-wide grep (only the definition site matches). Agreed for
removal:

| Item | Location | Notes |
| --- | --- | --- |
| `cmd_not_implemented` handler factory | `cli.py:959` | Never wired into the argparse tree. `top`/`query` stubs it was meant to back are inlined as `print(...); return 2`. |
| `get_decl()` wrapper over `SOURCE_DECLS` | `sources/base.py:248` | Only referenced by its own `__all__`; callers use `SOURCE_DECLS["…"]` directly. |
| Unused `DISPATCH_STATES` constant (+ never-emitted `starting` state) | `store/outbox.py:35` | Constant never referenced; `DESIGN.md:870` claims a `starting` state the code never writes. Either honor the documented set or drop both AND fix the DESIGN sentence. |

API cleanup (not strictly dead code, but reasonable):

- `Outbox.recover()` returns a vestigial two-tuple whose second element is always
  `0` (`store/outbox.py:158-167`; the in-code comment admits it is "for the
  pre-M8 caller API"). Collapsing to a single `int` is an API change — update the
  3 tests that unpack the pair. Low value unless touching the file anyway.

Rejected as duplication:

- **The two version entry points are intentional CLI compatibility**, not dead
  code: `ftmon --version` (argparse action, `cli.py:1036-1038`) and the
  `ftmon version` subcommand both exist deliberately. Leave both.

Minor (optional): `web/demo_app.py:64-69 _DemoClock` is a third spelling of the
`Clock` protocol (only `now()`); harmless given the demo app is intentionally
isolated.

---

## 2. SPEC / DESIGN drift (agreed)

**Described in DESIGN but missing from the tree (logic lives elsewhere):**

- `engine/baseline.py` (CA-05) — `DESIGN.md:98` — logic lives in
  `store/retention.py` (`_update_baselines`/`BaselineLookup`).
- `sources/selfsrc.py` — `DESIGN.md:86` — the `self` source is
  `selfmon.py:62 SelfSampler`; `selfmon.py` is also listed separately at
  `DESIGN.md:119`, so the file is described twice.
- `notify/dispatch.py` — `DESIGN.md:110,808` — dispatch lives in
  `store/outbox.py` (`DispatchWorker`). Largest naming gap.

**Existing files omitted from the §1 tree:** `engine/{context,actions,episodes,
render,events}.py`, `store/doctor.py`, `notify/{http,osascript,toast}.py`,
`sources/{oslog,win_evtlog,repeats}.py`, `definitions/manage.py`,
`checks/{text,trust,model}.py` — none dead, tree simply out of date.

**DESIGN CLI mappings that don't match (`DESIGN.md:1304`):** `web.run_demo` (real
entry is `web/app.py:run` + `web/demo_app.py:create_demo_app`);
`definitions.install_builtins` → `cli.py:cmd_init`; `definitions.check_cli` →
`cli.py:cmd_check`.

**Stray top-level docs:** `PLAN-baseline-visibility.md`, `PLAN-platform-
foundation.md`, `PLAN-windows-msi-task-scheduler.md`, `WIN-BACKLOG.md` are
committed files referenced by no living document, contradicting the M10 "root
limited to living documents" rule. Archive to the issue tracker or `docs/`, or
delete.

Recommendation: refresh DESIGN §1 (exact file tree + the three CLI mappings) —
the doc/module drift is exactly the failure mode the repo's lint tests prevent
for other properties.

---

## 3. Correctness & consistency issues

### 3.1 Retention classification: watchlist entities are not marked `durable` [HIGH — confirmed, primary bug]

`SPEC.md` `DM-04` (SPEC.md:378) is explicit:

> 1-hour rollups are kept **400 d** for *durable* series (system, disk, self,
> **and watchlist-synthetic entities**) …

and the DDL comment and `store/retention.py:329` restate it. But
`engine/pipeline.py:31`:

```python
_DURABLE_SOURCES = {"system", "disk", "self"}  # DM-04 retention split (DESIGN 9)
```

`durable` is assigned as a single Boolean purely from the source
(`pipeline.py:212`). The builtin watchlist monitors use sources *outside* the
set — `profile/desktop/net.toml` (`source = "net"`, listen watchlist) and
`profile/desktop/service.toml` (`source = "unit"`, unit watchlist) — so their
watchlist entities are flagged `durable = 0` and get the **process** window
(7 d 5-min / 90 d hourly) instead of the durable window (30 d / 400 d). Issue
#102's fix (PR #112) made this more consequential by splitting the previously
flat 5-minute tier on the same flag, so the mis-classification now also shortens
5-minute watchlist history to 7 d.

**The fix must be entity-aware, not a blanket source add.** Adding `net`/`unit`
to `_DURABLE_SOURCES` would incorrectly classify the non-watchlist totals an
`net` monitor also emits (e.g. `net/totals`). Durability should be derived from
`source_options.watchlist` membership for the entities the watchlist synthesizes,
alongside the `system`/`disk`/`self` full-monitor case. File as a bug with
regression tests (assert watchlist `unit`/`listen` entities are `durable = 1`
while non-watchlist `net` totals are `durable = 0`).

### 3.2 Daemon SQLite connection and lock file not explicitly cleaned up [MEDIUM/LOW]

`daemon.py:136` opens `core.conn`; the `finally` in `run()` (`daemon.py:1011-
1022`) stops the dispatch worker and events engine but does not close
`core.conn` (nor the process-lifetime lock file opened at `daemon.py:939`). On
the normal path process exit lets SQLite/OS clean up, so this is a teardown
hygiene gap, not a corruption risk. Recommend closing both `core.conn` and the
lock file in the `finally`. Describing the present behavior as abandoning the
connection "mid-transaction" would overstate the evidence — it is not that.

### 3.3 Ctrl-C logged as a crash — [RETRACTED — original claim incorrect]

The first revision claimed `run()`'s `except BaseException` (daemon.py:1013)
logs "ftmon daemon crashed" on a normal Ctrl-C. This is wrong. `daemon.py:989`
`_stop` is installed for `SIGINT`/`SIGTERM` (`daemon.py:997-998`) and only sets
`core.stop`; a normal Ctrl-C therefore exits the scheduler loop normally and
reaches the `finally` / "daemon stopped" path without raising
`KeyboardInterrupt` into the `except BaseException` block. Separately handling
`KeyboardInterrupt`/`SystemExit` could still be a defensive nicety (e.g. to
skip the "crashed" log if a handler is ever removed), but it is **not** the
reported bug and should not be described as one.

### 3.4 Writer in-process ID gaps on failed tick [LOW — technically true, not worth fixing by itself]

`store/writer.py:458-466` resets `_series_cache`/`_next_series_id` on a failed
`commit_tick` (justified — a reused series id could corrupt data) but not
`_next_event_id`/`_next_incident_id`/`_next_outbox_id`/`_history_seq`, so the
next tick emits id gaps. These ids are only ever monotonically-increasing
key material; the gaps are cosmetic and harmless. Not worth fixing by itself —
a one-line comment noting the asymmetry is sufficient.

### 3.5 `glance.py` reaches across the module boundary into private `q._conn` [LOW — partial]

`glance.py:90-113` runs raw SQL through `q._conn` (private attribute across a
module boundary) and calls `q.incidents(state=None)` then filters
`state != 'cleared'` in Python. Moving the live-incident filter into a public
`Query` method with a server-side `WHERE state != 'cleared'` is good. However,
the "evidence" semantics — what counts as proof a monitor has participated in a
daemon cycle (`has_evidence` at `glance.py:90`, incl. the unscoped
`SELECT EXISTS(SELECT 1 FROM cursors)`) — is policy with no current owner; that
should be designed deliberately rather than swept along with a mechanical SQL
move. Treat the query relocation and the evidence redesign as separate pieces of
work.

---

## 4. De-duplication and simplification opportunities (selective)

These range from clearly worthwhile to actively counterproductive. Verdicts
reflect maintainer review:

### 4.1 Clearly good

- **Quiet-hours hold predicate is spelled three times** (`store/outbox.py:88-92`
  closure `backlog.is_held`, `_held` at `323-327`, and `_materialize_digest` at
  `342`). This is the exact kind of invariant the repo's own discipline says must
  live once. Implement it as an **outbox-level helper** (e.g. a private method on
  `Outbox`), not `QuietHours.delivers_hold()`, because the severity threshold is
  notification policy, not time-window configuration.
- **`daemon.py` `_config_file_stamp`/`_check_registry_stamp` (`277-291`) are
  byte-identical** except the attribute name → parameterize into `_stamp(path)`.
- **Self-metrics SQL triplicated** across `web/app.py:241-247`, `web/app.py:
  1223-1227`, and `mcp_server.py:200-204` → one `Query.latest_self_metrics()`;
  and `dashboard`/`self_page` re-wire `_self_panel`/`_self_budget_stats`
  separately → one `_self_state(...)` compose helper.
- **CLI connection boilerplate**: `connect()`/`try:`/`finally: conn.close()`
  (~7 sites: `cli.py:410,461,502,557,592,757,822`) and the `"no data - is the
  daemon running?"` guard (~6 sites) → one `_db()` context manager + one guard.
- **CLI check-registry loading copy-pasted 3×** (`cli.py:330,717,802`)
  reimplements `glance.check_aliases` → use the existing helper.

### 4.2 Plausible — do only when a concrete change touches the path

- **Move `glance.py` raw SQL + live-incident filter** behind public `Query`
  methods (§3.5).
- **Query-layer tier + presence-`EXISTS` SQL** is duplicated between
  `series_catalog` (`query.py:495-507`) and `list_observed_series_entities`
  (`517-552`), and the raw-vs-rollup branch logic is split across
  `series`/`series_points`/`series_point_budget` (`582-669`). Collapsing to one
  "resolve tier" + one `_presence(table, time_col)` helper would deduplicate the
  main driver of the file's length — but this is the read-hot path, so it should
  be measured, not churned speculatively.
- **Check pre-parser shims** (`_dispatch_check_install`/`_dispatch_check_trust`,
  `cli.py:968-1010`) duplicate `recipe install/trust` args and run two full
  argparse parses. Replacing with real sub-subparsers dissolves them — worth
  doing, but only in the same change that touches `check`.

### 4.3 Weak — skip

- **Turning the readable MCP registration list (`build_server`, `mcp_server.py:
  840-936`) into a registry loop.** The current one-entry-per-tool list is
  greppable and reviewable; a loop over `{name: (method, description)}` trades
  clarity for a few lines. Do not do this.
- **`_AttrCtx` stubs (`mcp_server.py:91-121`) vs `glance.StoredEntityCtx`** —
  similar surface, but they fulfil different eval contexts; merging is not
  clearly a win.

### 4.4 Risky without profiling

- **mtime-cached monitor-definition loading** (a `functools.lru_cache` keyed on
  directory mtime, suggested for the 6+ per-request `load_dir` calls in
  `web/app.py`). Freshness is a product property (30 s rescan) and the load cost
  hasn't been shown to matter. Profile first; skip unless the hot pages are
  measurably slow.

### 4.5 Probably counterproductive / mischaracterized

- **Centralizing every repeated small constant** (`FLAP_WINDOW_S`/`FLAP_COUNT`/
  `_BODY_MAX` in `engine/incidents.py:41`, `engine/episodes.py:50`,
  `engine/render.py:14`, `store/outbox.py:22`): matching literals in separate,
  deliberately-pure modules is not drift in practice (the two state machines are
  independent FROZEN units). Consolidating "solely to remove matching literals"
  adds coupling without clear benefit. Skip unless a behaviour change requires
  touching all of them.
- **Main-thread `Outbox` object** ("dead weight in production"): the object is
  lightweight and reuses the already-built notifier tuple; it is only exercised
  on the synchronous path (`daemon.py:749`). Calling it substantial production
  dead weight overstates the problem. Leave as is.

---

## 5. On the "~600–900 lines" savings estimate [withdrawn]

The first revision's headline savings figure was an unsupported guess, not a
measurement of the recommended consolidations. It is withdrawn. The §4 items
*individually justified* are concrete and small; whether their total is worth
the churn should be decided per-change (see the priority order below), not on an
aggregate line count.

## 6. Overall architecture assessment (agreed)

The product architecture is roughly right-sized; the overhead lives in process
tooling and deliberate modularity, not in design over-engineering:

- The single hardened HTTP opener and the single trust-predicate source are the
  correct consolidations (verified — no hidden duplicates).
- `expr/`'s parse→IR→eval split and the `engine/` pure state machines mirror the
  security/`FROZEN` design intent; collapsing them would cost safety.
- The synchronous-`Outbox`-reused-by-threaded-`DispatchWorker` design shares one
  implementation (good).
- The traceability machinery (213 requirement IDs, generated `reqindex.json`,
  pending-ratchet, doc-version-coherence tests, ~20k test lines) dominates
  "complication" but is a coherent, deliberate governance bet — worth revisiting
  periodically, not an accident.
- ~100 small modules (many 50–110 lines) is a per-requirement granularity trade;
  reasonable.

---

## 7. Recommended priority order

1. **Fix watchlist durability** (§3.1) with regression tests — the primary bug.
2. **Refresh DESIGN §1** (file tree + three CLI mappings) and **correct this
   document's stale issue-history references**; archive the stray top-level
   plans.
3. **Explicit daemon resource teardown** — close `core.conn` and the lock file in
   `daemon.py`'s `finally` (§3.2).
4. **Remove unquestionably dead symbols** — `cmd_not_implemented`, `get_decl`,
   unused `DISPATCH_STATES` (§1).
5. **Move cross-layer raw SQL behind focused `Query` methods** (§3.5, §4.2) with
   the evidence semantics redesigned separately.
6. **Other duplication only when a concrete change touches those paths** (§4.2
   plausible items); skip §4.3–4.5 speculative abstraction.

Validation commands (unchanged): `uv run ruff check src tests tools` and
`uv run pytest -q`.

---

## Appendix — GitHub issue cross-reference

> **Staleness note (rev. 2):** issue states in the first revision were a
> point-in-time snapshot from `gh issue list` and quickly drifted from `main`.
> This section records only what is verifiable in this checkout, not live issue
> state.

- **Issue #102** (durable/process retention split): fixed in this checkout —
  `retention.py:79-82`/`312-338` apply the split to both the 5-minute and hourly
  tiers, and `SPEC.md` `DM-04` (SPEC.md:378) records the v0.47 amendment. This
  is the change that makes §3.1's mis-classification consequential.
- **Issue #103 remains open** (confirmed live, `enhancement` label). PR #117
  landed related catalog-remediation work and PR #118 landed per-monitor
  attribution. Still unresolved are the preventive runtime admission guardrail
  and the promotion-cost documentation. The precise guardrail — entity-only,
  series-aware, or per-monitor — remains a design decision. Any audit that
  described issue #103 as having "no runtime action" was stale. §3.1's fix and
  this guardrail are distinct but both concern the persisted-entity catalog, so
  they should be co-ordinated.
- Other review findings (§1 dead code, §2 DESIGN drift, §3.2 teardown) are not
  tracked as issues in this checkout and would be new backlog items.
- Open self-monitoring work (#97/#106/#107 family, incl. per-series pruning
  cursors) overlaps §3.5/§4.2 — co-ordinate any session-query refactor with that
  work rather than competing query paths.
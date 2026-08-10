# Backlog candidates from the simplicity audit

Local scratch only. GitHub issues are the canonical backlog. These drafts are
derived from `docs/REVIEW-simplicity-audit.md` rev. 2 and were checked against
the live open issue list on 2026-08-10.

## Existing issue to update, not duplicate

### #103 — runtime persistence guardrail and promotion documentation

Issue #103 remains open. PR #117 landed related catalog-remediation work and
PR #118 landed per-monitor catalog attribution. Add a short status comment or
edit clarifying that the unfinished work is:

- a preventive runtime admission guardrail;
- the authoring warning that promotion controls persistence, not rule
  evaluation; and
- a decision between entity-only, series-aware, and per-monitor limits.

Do not describe a "per-monitor entity cap" as settled design until that choice
is made.

---

## 1. #119 — Pipeline: retain watchlist-synthetic history as durable

**Promoted:** <https://github.com/dannysheehan/ftmon/issues/119>

**Proposed labels:** `bug`, `backlog`

### Problem

DM-04 gives durable retention to system, disk, self, and watchlist-synthetic
series. `Pipeline._persist()` instead assigns one durability value to an entire
monitor from `_DURABLE_SOURCES = {"system", "disk", "self"}`. Consequently:

- `unit` watchlist entities are stored as non-durable;
- `net` listener-watchlist entities are stored as non-durable; and
- both receive the process windows: 7 days at 5-minute resolution and 90 days
  hourly, instead of 30 and 400 days.

The classifier cannot be fixed by adding `net` and `unit` wholesale: `net`
also emits the non-watchlist `totals` entity, which must remain distinct from
synthetic watchlist identities.

Existing installations add another constraint. `TickWriter.series_id()`
returns an existing series id without reconciling its stored `durable` flag, so
a classifier-only fix would repair new series but leave every existing
watchlist series misclassified.

### Direction

- Make durability an entity-level decision.
- Keep all `system`, `disk`, and `self` entities durable.
- Mark entities synthesized from a validated `source_options.watchlist` entry
  durable, including `unit` unit/process entries and `net` listener entries.
- Keep `net/totals` non-durable unless the specification explicitly decides
  otherwise.
- Promote an existing matching series from `durable = 0` to `durable = 1` on
  its next successful write. Perform that correction inside the tick
  transaction; never rewrite committed state before `commit_tick()` succeeds.
- Never demote an existing durable row implicitly. A false downgrade could
  make retention delete history that DM-04 still promises.
- Do not claim to reconstruct rollups already pruned under the old flag.

### Acceptance criteria

- [ ] A `unit` watchlist entity is created with `series.durable = 1`.
- [ ] A `net` listener named by the watchlist is created with
      `series.durable = 1`.
- [ ] The `net/totals` entity remains `series.durable = 0`.
- [ ] Two watchlist entries and malformed/duplicate entries cannot classify an
      unrelated entity as durable.
- [ ] An existing watchlist series stored as non-durable is promoted on the
      next successful tick.
- [ ] A failed tick rolls the correction back along with its samples.
- [ ] Existing durable series are never downgraded by reconciliation.
- [ ] Query tier selection gives corrected watchlist series the durable DM-06
      window while a mixed or non-durable cohort retains the shorter window.
- [ ] Retention tests cover the 30-day/400-day watchlist promise.
- [ ] Tests cite DM-04/DM-06 and the traceability pending list does not grow.

### Likely touchpoints

- `src/ftmon/engine/pipeline.py`
- `src/ftmon/store/writer.py`
- `src/ftmon/sources/net.py`
- `src/ftmon/sources/unit.py`
- `tests/unit/test_engine.py`
- `tests/unit/test_store.py`
- `tests/unit/test_retention.py`
- `SPEC.md` / `DESIGN.md` only if the implementation needs new normative
  wording; DM-04 already states the intended behavior

### Relevant requirements

DM-04, DM-06, SA-05, CA-08, PM-03, PM-10.

---

## 2. #121 — Docs: reconcile DESIGN's architecture inventory with the shipped tree

**Promoted:** <https://github.com/dannysheehan/ftmon/issues/121>

**Proposed labels:** `documentation`, `backlog`

### Problem

DESIGN section 1 describes modules that do not exist, omits a substantial set
of shipped modules, and maps several CLI commands to functions that are not the
real composition points. It also documents a notification dispatcher
`starting` state that the implementation never publishes. The stale map is
especially costly in a repository where agents are required to consult DESIGN
before changing code.

Four completed plan/backlog documents also remain at the repository root even
though the M10 hygiene rule limits the root to living documents:

- `PLAN-baseline-visibility.md`
- `PLAN-platform-foundation.md`
- `PLAN-windows-msi-task-scheduler.md`
- `WIN-BACKLOG.md`

### Direction

- Refresh DESIGN section 1 against the current source tree.
- Remove nonexistent `engine/baseline.py`, `sources/selfsrc.py`, and
  `notify/dispatch.py` entries, pointing to the actual owners instead.
- Add the omitted modules identified in the audit.
- Correct the `web`, `init`, and `check` CLI mappings.
- Reconcile the documented dispatcher-state vocabulary with the implemented
  one. Prefer documenting the deliberate pre-connection `unknown` state unless
  a real `starting` state has an operational consumer.
- Delete completed root plans when git history is sufficient, or move material
  that remains operationally useful into an explicitly historical location.
- State whether the section 1 tree is exhaustive. If exhaustive, add a focused
  drift check so the same mismatch is not silently recreated.

### Acceptance criteria

- [ ] Every path shown in DESIGN section 1 exists, except paths explicitly
      marked illustrative or generated.
- [ ] Every architecture-significant shipped module has an owner in the map.
- [ ] CLI mappings name the actual callable/composition point.
- [ ] Dispatcher states in DESIGN agree with `store/outbox.py` and doctor.
- [ ] No completed plan/backlog file remains at the root without a living
      reference and stated purpose.
- [ ] No product behavior or normative retention semantics change in this
      documentation-only issue.
- [ ] Documentation fence, traceability, and full tests pass.

### Likely touchpoints

- `DESIGN.md`
- the four root plan/backlog files
- `tests/unit/test_design_lint.py` or the closest existing documentation lint

### Relevant requirements

TS-19, DO-09, plus the requirement IDs already cited by the mapped modules.

---

## 3. #120 — Daemon: make process-lifetime resource ownership explicit

**Promoted:** <https://github.com/dannysheehan/ftmon/issues/120>

**Proposed labels:** `enhancement`, `backlog`

### Problem

`daemon.run()` explicitly stops the dispatch worker and event engine, but it
does not explicitly close the daemon's primary SQLite connection or its
process-lifetime lock file. Normal process exit lets Python and the OS reclaim
them, so this is teardown robustness rather than evidence of corruption.

The ownership boundary begins when the lock is acquired, not only when
`core.run_loop()` begins. An exception during `DaemonCore` construction or
fixture setup currently occurs before the existing `try/finally`.

### Direction

- Put every resource acquired after the daemon lock under one cleanup scope,
  using an outer `try/finally` or `ExitStack`.
- Stop the dispatch worker and event engine before closing the main database
  connection.
- Release the lock last, so a second daemon cannot start while the first is
  still tearing down background boundaries or its database handle.
- Keep cleanup idempotent for partially constructed cores and unexpected
  startup failures.
- Parameterize the duplicate config/check-registry file-stamp helper while
  this module is being touched; preserve inode/mtime/size semantics.

### Acceptance criteria

- [ ] Normal stop closes `core.conn` and the lock file.
- [ ] An exception from the run loop still stops background workers, closes the
      connection, and releases the lock.
- [ ] An exception during core construction releases the already-acquired
      lock without referencing an unconstructed core.
- [ ] The lock remains held until worker/event teardown and DB close finish.
- [ ] Cleanup failure does not conceal the original startup/runtime exception;
      any secondary failure is logged safely.
- [ ] A subsequent daemon invocation in the same test process can acquire the
      lock after the first invocation returns.
- [ ] Graceful SIGINT/SIGTERM behavior remains unchanged and is not logged as a
      crash.

### Likely touchpoints

- `src/ftmon/daemon.py`
- daemon lifecycle/unit and e2e tests

### Relevant requirements

PM-02, PM-03, PM-10, PM-12, PL-01.

---

## 4. #122 — Read model: own shared health and self-metric queries in `Query`

**Promoted:** <https://github.com/dannysheehan/ftmon/issues/122>

**Proposed labels:** `enhancement`, `backlog`

### Problem

Shared read-side policy reaches through `Query`'s private `_conn` for SQL,
loads every incident before filtering cleared rows in Python, and duplicates
latest-self-metric SQL across web and MCP. This makes storage semantics leak
into consumers and gives the same operational answer multiple spellings.

The event-monitor fallback in `glance.has_evidence()` is also underspecified:
the existence of any cursor currently proves evidence for any event monitor.
Moving the SQL is mechanical; changing what counts as evidence is a policy
decision and must be handled explicitly.

### Direction

- Add focused public `Query` methods for:
  - live incidents (`state != 'cleared'`) with filtering performed in SQL;
  - monitor load/series evidence;
  - latest self metrics used by dashboard, Self, and MCP status.
- Make `glance.py`, web, and MCP consume those methods instead of repeating SQL
  for these shared concepts.
- Specify event-monitor evidence before changing its behavior. Determine
  whether evidence comes from monitor load history, a cursor owned by the
  monitor's configured event source/channels, or another durable marker.
- Do not allow an unrelated source/channel cursor to make a monitor healthy.
- Keep raw values in `Query`; formatting and presentation remain consumer
  responsibilities.
- Coordinate with #106/#107 where query paths or self metrics overlap, without
  coupling this work to retention-cursor implementation.

### Acceptance criteria

- [ ] `glance.py` does not access `q._conn`.
- [ ] Live-incident lookup excludes cleared incidents in SQL.
- [ ] Dashboard, Self, and MCP get latest self metrics from one query method and
      retain identical values/None semantics.
- [ ] Tests cover a cursor belonging to an unrelated event source/channel and
      prove it cannot establish evidence for the wrong monitor.
- [ ] Multiple monitors sharing the same legitimate event source retain the
      intended startup/evidence behavior.
- [ ] No-data, disabled, stale, and config-error UI-14 precedence remains
      unchanged except for an explicitly specified evidence correction.
- [ ] Any evidence-semantics change updates SPEC/DESIGN and lands with tests in
      the same PR.

### Likely touchpoints

- `src/ftmon/store/query.py`
- `src/ftmon/glance.py`
- `src/ftmon/web/app.py`
- `src/ftmon/mcp_server.py`
- `tests/unit/test_glance.py`
- `tests/unit/test_store.py`
- `tests/unit/test_web.py`
- `tests/unit/test_mcp.py`

### Relevant requirements

UI-04, UI-14, UI-17, UI-18, DM-06, RB-02, MC-01.

---

## 5. Notifications: single-source quiet-hours hold classification

**Proposed labels:** `enhancement`, `backlog`

### Problem

The decision that a delivery is held by quiet hours is spelled independently
in `backlog()`, `Outbox._held()`, and digest materialization. These paths answer
different operational questions but must classify the same delivery the same
way. A drift could make doctor report a row as claimable while the dispatcher
holds it, or materialize a digest for a different set than backlog reports.

### Direction

- Introduce one outbox-level pure helper accepting quiet-hours configuration,
  severity, and creation timestamp.
- Keep `_QUIET_MAX_SEV` in notification policy; do not move severity knowledge
  into the generic `QuietHours` time-window configuration object.
- Use the helper for backlog reporting, claim filtering, and digest selection.
- Preserve the distinction between held durable debt and overdue claimable
  debt.

### Acceptance criteria

- [ ] All three paths call the same classification helper.
- [ ] Boundary tests cover quiet start/end, overnight windows, severity just
      below/above the hold limit, and no quiet configuration.
- [ ] Held rows remain pending but do not count as overdue or claimable.
- [ ] Digest materialization consumes exactly the rows backlog classified as
      held once quiet hours end.
- [ ] Existing NO-10 behavior and durable retry semantics remain unchanged.

### Likely touchpoints

- `src/ftmon/store/outbox.py`
- `tests/unit/test_quiet.py`
- `tests/unit/test_notification_dispatch.py`
- `tests/unit/test_doctor.py`

### Relevant requirements

NO-04, NO-07, NO-10, DM-14, DM-18.

---

## 6. Maintenance: remove audit-confirmed dead internal symbols

**Proposed labels:** `enhancement`, `backlog`

### Problem

Three internal symbols have no callers and preserve obsolete shapes in the
source:

- `cli.cmd_not_implemented`;
- `sources.base.get_decl`; and
- `store.outbox.DISPATCH_STATES`.

The first two duplicate the actual direct implementations. The third suggests
an enforced state vocabulary while no code consumes it, and it omits the
`starting` state claimed by DESIGN.

### Direction

- Delete the three unused symbols and update `__all__` where applicable.
- Leave the intentional `ftmon --version` and `ftmon version` entry points
  alone.
- Leave `Outbox.recover()`'s two-tuple compatibility shape alone unless a
  concrete caller change justifies that separate API cleanup.
- Reconcile the DESIGN dispatcher-state sentence in issue 2 rather than
  replacing the unused constant with another unused declaration.

### Acceptance criteria

- [ ] Repo-wide search finds no definitions or exports for the three symbols.
- [ ] `top` and `query` retain their current message and exit code.
- [ ] Source declarations remain accessible through `SOURCE_DECLS`.
- [ ] Dispatcher state publication and doctor behavior are unchanged.
- [ ] Full lint and tests pass without weakening unused-code checks.

### Likely touchpoints

- `src/ftmon/cli.py`
- `src/ftmon/sources/base.py`
- `src/ftmon/store/outbox.py`
- focused CLI/source/notification tests if imports are asserted

### Relevant requirements

No product requirement changes; preserve CL-01/CL-02 and PM-12 behavior.

---

## 7. CLI: centralize database and check-registry setup without changing UX

**Proposed labels:** `enhancement`, `backlog`

### Problem

CLI commands repeat database-existence checks, connection cleanup, and
external-check registry loading. The registry copies already differ in how
they report invalid configuration, so blindly replacing them with
`glance.check_aliases()` would erase command-specific failure behavior.

### Direction

- Add a small context manager that opens and always closes the CLI read/write
  connection.
- Centralize the common missing-database guard while allowing commands to
  retain their documented exit codes and output formats.
- Extract registry loading into one helper that returns both aliases and a
  redacted error/category, rather than reusing the current lossy
  `glance.check_aliases()` API.
- Let `check`, monitor approval/enablement, and doctor decide locally whether a
  registry error is rendered, accumulated, or treated as fail-closed.
- Do not combine this with the argparse check-install/trust parser redesign;
  that remains opportunistic work when the `check` command is otherwise
  changed.

### Acceptance criteria

- [ ] Every CLI database connection is closed on success and exception paths.
- [ ] Missing-database text and exit codes remain byte-for-byte stable where
      tests currently define them.
- [ ] An invalid registry still appears as a structured validation error in
      `ftmon check`.
- [ ] Monitor approval remains fail-closed when registry authority is invalid.
- [ ] Doctor still reports the redacted registry error and non-zero health as
      currently specified.
- [ ] No helper exposes registry argv or secrets in an error.
- [ ] JSON command output remains unchanged.

### Likely touchpoints

- `src/ftmon/cli.py`
- `src/ftmon/glance.py` if the shared alias API is generalized
- `src/ftmon/checks/registry.py`
- `tests/unit/test_cli.py`
- registry/definition management tests

### Relevant requirements

CL-02, CL-03, CL-05, EC-01, EC-06, SE-04.

---

## Deliberately not promoted to backlog issues

Keep these as review context and reconsider only when measured work touches the
same path:

- converting MCP's explicit tool registrations into a registry loop;
- merging `_AttrCtx` with `StoredEntityCtx`;
- mtime-caching monitor-definition loads without profiling;
- centralizing constants shared only by independent frozen state machines;
- removing the lightweight main-thread `Outbox` object;
- broad query-tier SQL refactoring before a concrete read-path change; and
- the withdrawn aggregate line-savings estimate.

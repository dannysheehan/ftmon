# Plan: baseline visibility (#17, MCP, #18)

Three atomic work packages, in this order:

1. [#17 — Metrics baseline overlay](https://github.com/dannysheehan/ftmon/issues/17)
2. MCP `list_baselines` (new backlog issue)
3. [#18 — Baselines index](https://github.com/dannysheehan/ftmon/issues/18)

Goal: honest visibility into CA-05 EWMA baselines — no manufactured timestamps,
no gap-filling, no horizontal “always was this level” lines across missing
history. Reset remains CLI-only; half-life tuning UI stays out of scope.

---

## Prerequisite for exact reconstruction (part of #17 / SPEC v0.23)

Per-monitor half-life remains valid SPEC behavior (CA-05). `Retention` already
accepts an injected half-life, so Query cannot reverse a stored EWMA without
knowing the α that produced every historical step.

**Decision:** `half_life_s` is **immutable for a baseline row’s lifetime**.

- Schema migration `0004_baseline_half_life.sql` uses a constant-default
  column add (not a bare `NOT NULL` plus later backfill, which fails on
  existing rows):

  ```sql
  ALTER TABLE baselines ADD COLUMN half_life_s REAL NOT NULL DEFAULT 259200;
  ```

  Final `PRAGMA user_version = 4`. No full table rebuild.
- On first insert (seed), store the half-life in effect.
- On a later rollup, if the configured half-life differs from the stored value,
  **reseed** that baseline before applying the new rollup: delete/replace the
  row with `value = rollup_avg`, `updates = 1`, `updated_bucket = bucket`,
  `half_life_s = new_half_life`. Prior learning is discarded (same operator
  expectation as `ftmon baseline reset` for that series).
- Reconstruction always uses the single stored `half_life_s` (safe because every
  retained update in that lifetime used it).
- Wiring per-monitor half-life from definition config into `Retention` remains a
  separate issue; this package defines the store invariant so reconstruction
  stays exact whenever that wiring lands.

---

## 1. Issue #17 — Metrics baseline overlay → SPEC v0.23

### SPEC / DESIGN

- Extend **CA-05** with read-side visibility semantics, persisted immutable
  `half_life_s`, and reseed-on-half-life-change.
- Extend **UI-13**, **TS-11**, and DESIGN Metrics / Query contracts.
- History is shown only at native five-minute bucket timestamps where retained
  `rollup5m` evidence exists. No hourly interpolation. No values invented at
  raw sample timestamps.

### Reconstruction semantics

- The current baseline row is history point `updates = N` at `updated_bucket`.
- Perform at most `N − 1` inverse EWMA steps through retained `rollup5m.avg`
  rows (newest-first), using α derived from the row’s stored `half_life_s`.
- Never reverse the seed update (`updates == 1`): that point’s level equals the
  first rollup avg.
- Emit one baseline point per reconstructed bucket (native 5m timestamps only).
- Range-relative truncation (compared on five-minute bucket boundaries, not
  wall-clock start):

  ```text
  first_bucket_in_range = ceil(selected_start / 300) * 300
  history_truncated = first_bucket_in_range < earliest_reconstructed_ts
                       AND reconstruction did not reach the seed
  ```

  So a selected start of `10:02` with a first native point at `10:05` is not
  truncated merely for landing between buckets.
- Reaching the seed is complete history for that series, even when the selected
  chart range starts earlier — that is not truncation.
- Lifetime updates pruned *before* the selected window do not set
  `history_truncated` when the in-range path is fully reconstructable (including
  reaching the seed).

### Resolution composition

| Primary resolution | Baseline overlay |
|---|---|
| `5m` | Reconstructed path at native 5m buckets that fall in range. Connect consecutive buckets (Δt = 300 s) as a dashed line; do not bridge larger gaps. |
| `raw` | Same native 5m baseline points only. **Rendering:** partition `baseline.points` into contiguous runs where successive timestamps differ by exactly `ROLLUP5M_S` (300). Draw each run as a dashed polyline. Do **not** union baseline points into the raw sample x-axis in a way that inserts nulls between buckets and then expect a single series to connect them. Do **not** enable `spanGaps`. Do **not** step-hold onto raw sample timestamps. Gaps larger than one rollup step remain visual breaks between runs. |
| `1h` | Render whatever retained five-minute baseline tail exists in range, using the same contiguous-run dashed rendering as above. If none, omit the historical overlay entirely; show current level / coverage / readiness only in the summary text. Never draw a horizontal reference line across the range. |

Implementation note: contiguous runs may be separate uPlot series sharing one
legend label, or one series drawn by a small plugin that strokes only
consecutive-bucket pairs. Either is fine; bridging a gap > 300 s is not.

### `/api/series` contract

Always include a `baseline` key:

- `null` when no stored baseline row exists for the series.
- Otherwise:

```text
{
  level,
  updates,
  required_updates,   # 240
  coverage,           # min(updates / 240, 1)
  ready,              # updates >= 240
  updated_at,         # updated_bucket (UTC seconds)
  half_life_s,
  points,             # [[ts, value], ...] native 5m only; may be []
                      # same pair shape as existing Metrics chart points
  history_truncated    # range-relative; see above
}
```

### UI

- Dashed baseline overlay from `baseline.points` via contiguous-run rendering.
- Accessible summary always states current level, coverage/readiness, and
  whether history is truncated or absent for the selected range.
- Demo app: seed at least one learning and one ready baseline with matching
  `rollup5m` history so Metrics overlay and summary states are exercisable.

### Tests

- Migration v3→v4: three-day default on existing rows, idempotent remigrate,
  updated `baselines` table shape includes `half_life_s`,
  `PRAGMA user_version == 4`.
- Half-life immutability: reseed when configured half-life changes; reverse
  reconstruction refuses mixed-α histories by construction.
- Retention reverse golden (forward sequence → reverse matches; seed not
  reversed); persisted `half_life_s` used for α.
- Missing-row → `baseline: null`; learning vs ready.
- `history_truncated` false for a short in-range view when only pre-range
  history was pruned; false when selected start falls inside the first
  in-range 5m bucket (e.g. 10:02 vs point at 10:05); true when
  `range_start_bucket` precedes earliest reconstructed point and seed was
  not reached.
- Contiguous-run rendering / no `spanGaps`; raw and 1h modes do not invent
  points; long-range 1h with/without retained 5m tail.
- Update `docs/manual.md`.

---

## 2. MCP `list_baselines` (new backlog issue) → SPEC v0.24

Depends on #17’s current-baseline Query (and persisted `half_life_s`).
Implement before #18 so the index reuses the same listing contract.

### SPEC / DESIGN

- Add `list_baselines` to the frozen **MC-01** tool table.
- Add **MC-07**: read-only, bounded, deterministic.

```text
list_baselines(
  monitor?, entity?, metric?, ready?,
  limit=100, cursor?
)
→ {
    tz,
    baselines: [{
        monitor,
        entity,
        metric,
        level,
        updates,
        required_updates,
        coverage,
        ready,
        updated_at,
        half_life_s
    }],
    next_cursor   # null when no further rows
}
```

### Semantics

- Source: **all stored baseline rows** (not filtered by whether a loaded rule
  uses `baseline(...)`).
- Optional exact filters: `monitor`, `entity`, `metric`, `ready`.
- Stable ordering: `(monitor, entity, metric)` ascending.
- `limit` optional; absent → default **100**. Reject non-integer values and
  integers outside `1..500` with MC-04 `invalid_params`; do **not** silently
  clamp. (Absence is not an error.)
- Pagination: opaque **keyset cursor** encoding both:
  - the last returned `(monitor, entity, metric)` key; and
  - the canonical filter set (or a fingerprint of it)
  so a stateless MCP server can enforce filter matching. Not an integer offset.
- A `cursor` MUST decode cleanly and match the request’s filter set; malformed
  cursors or filter mismatches return MC-04 `invalid_params`.
- `next_cursor` is null when no further rows exist.
- Return learning `level` as well as `coverage`; `ready=false` explains why
  `baseline(m)` still evaluates to `None`.
- Reuse the current-baseline Query helper from #17.
- Register in `TOOL_NAMES` and FastMCP.
- Tests in `tests/unit/test_mcp.py`: exact tool enumeration, filters, ordering,
  keyset cursor/`next_cursor`, cursor+filter mismatch → `invalid_params`,
  malformed cursor → `invalid_params`, absent limit → 100, invalid limit →
  `invalid_params`, no-database behavior, learning/ready boundary, timestamps,
  two-second contract.

Create the GitHub issue before implementation; label `enhancement` + `backlog`
until work starts.

---

## 3. Issue #18 — Baselines index → SPEC v0.25

Depends on the MCP listing model (#2): same filters, ordering, and pagination.

### SPEC / DESIGN

- Amend **UI-02** (page inventory) to include **Baselines**; primary nav entry.
- Read-only page; reset remains CLI-only (**UI-03** unchanged).

### Code

- `GET /baselines` with the same query parameters as `list_baselines`
  (`monitor`, `entity`, `metric`, `ready`, `limit`, `cursor`).
- Absent `limit` → 100. Non-integer or out-of-range `limit`, malformed
  `cursor`, or filter-mismatched `cursor`: HTTP 400 with a clear message
  (web analogue of MC-04; no silent clamp).
- Default: all stored rows, paginated (same defaults/caps and cursor shape
  as MCP).
- Columns: monitor, entity, metric, level, coverage/ready, updates, last update.
- Each row links to the matching shareable Metrics URL
  (`/metrics?monitor=…&entity=…&metric=…`).
- Empty state, no-database state, and accessible coverage text.
- Demo: the seeded baselines from #17 appear in the index.
- Tests: route, nav, filters, keyset pagination/`next_cursor`, invalid limit,
  malformed cursor, cursor+filter mismatch, learning/ready, empty/no-DB,
  Metrics link shape.
- Update `docs/manual.md`.

---

## Out of scope

- Half-life **tuning UI** and wiring per-monitor half-life from definition
  config into `Retention` (separate issue). This package freezes the store
  invariant (immutable `half_life_s`, reseed on change) so that wiring cannot
  corrupt reconstruction.
- UI reset / write affordances for baselines (remains `ftmon baseline reset`).
- Baseline seasonality (NG-07).
- A baseline-history table / per-update α log (reconstruction from retained
  `rollup5m` only, under the immutable-half-life invariant).

---

## Per-package landing checklist

Each work package updates SPEC status/changelog, DESIGN companion version,
regenerates `tests/reqindex.json`, and lands its requirement tests in the
same change.

```sh
python3 tools/gen_reqindex.py
python3 tools/gen_reqindex.py --check
uv run ruff check src tests tools
uv run pytest -q
```

# Backlog

Uncommitted local scratch (CLAUDE.md) -- promote an entry to a GitHub issue
when it's ready to be worked, then remove it from here.

## Windows: ProcessSampler is measurably slow (RB-01 miss)

**Problem**: `sources/process.py::ProcessSampler.sample()` -- shared by the
`hog`/`leak` monitors -- costs ~16ms per process on Windows (measured via
direct timing on a real dev machine: 543 processes, 16.6s per full scan).
Isolating which of the 9 collected psutil fields drive that cost:

| fields collected | ms/process |
|---|---|
| `create_time` + `name` only | 2.2 |
| + `cpu_percent` | 3.6 |
| + `memory_info` | 4.3 |
| full current set (+ `cmdline`, `username`, `exe`, `num_threads`, `io_counters`) | 16.0 |

`psutil.process_iter(attrs=[...])` batching was tested and does **not**
help (same ~15ms/process either way) -- the cost isn't the API shape, it's
Windows' per-process handle-open overhead multiplied by however many
separate calls are made against it.

With ~500+ processes sampled every 60s, this alone produces a sustained
~15-27% daemon CPU load on a real overnight run (`cpu_10m` steady-state
~15-16%, peaking to 59.6%) -- against RB-01's normative `<=1%` target
(SPEC.md). `profile/windows/self.toml`'s `cpu_budget_pct` was recalibrated
to 30 to stop the self-monitor's `cpu-budget` incident from being
permanently stuck open over this, but that only stops the alarm --  it
does not make RB-01 true on Windows. This item is that: actually close the
gap.

**Direction** (not yet decided, needs real investigation before picking one):
- `cmdline`/`username`/`exe` back the SA-09 display-identity feature
  (`exe_base`/`display`/`cmd_hint`) and the `collect_cmdline` privacy
  toggle -- real, intentional features, not accidental cost. Trimming them
  for "cold" (never-promoted, never-hogging) processes would trade away
  `process.py`'s documented CA-07 guarantee ("history for exempt or
  unselected entities must still exist so it can be queried later") on
  Windows specifically -- a real design tradeoff, not a safe optimization,
  needs its own decision before implementing.
- Alternative: sample the full field set only for entities that were
  already tracked/promoted last tick, and a cheaper subset (just
  `cpu_pct`/`rss_bytes`, the two fields hog/leak's rules actually
  evaluate) for everything else -- keeps CA-07 for entities that ever
  mattered, drops it only for ones that never will. Needs checking whether
  "never promoted, never will be" is knowable cheaply before the expensive
  fields would otherwise be read.
- Or: accept the recalibrated budget as the real, permanent Windows number
  (formally amend RB-01 in SPEC.md with a documented Windows carve-out)
  instead of treating this as a bug to fix. Rejected for now (user
  decision, 2026-07-26) in favor of investigating a real fix first, but
  worth revisiting if the investigation doesn't find a good one.

**Likely touchpoints**: `src/ftmon/sources/process.py`,
`src/ftmon/definitions/profile/windows/self.toml` (revert the
recalibration if the underlying cost is actually fixed),
`src/ftmon/engine/pipeline.py` (SA-06 caching, if a two-tier
expensive/cheap sampling split needs pipeline-level support).

**SPEC IDs**: RB-01, RB-02, CA-07, SA-05, SA-06, SA-09, SE-04.

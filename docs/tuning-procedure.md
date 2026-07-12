# FTMON monitor tuning procedure

Soak testing ([soak procedure](soak-procedure.md), TS-17) proves stability over
weeks. **Tuning** answers a different question: which threshold and
`confirm_cycles` values catch real leaks and CPU hogs on *your* workload mix
without paging on browser warm-up, compiles, or snap mounts?

Use controlled workloads, score candidate settings, then edit
`~/.config/ftmon/monitors/` (or bake winners into `ftmon init --profile
desktop` once validated).

## Relationship to CI scenarios

| Layer | Purpose |
| --- | --- |
| JSONL scenarios (`firefox-leak-2mb-min`, …) | Prove engine correctness in pytest (TS-04) |
| `tools/tuning/leaky.py` | Grow **real** RSS on a live host so the process sampler and ring buffers behave like production |
| `tools/tuning/stress_profile.sh` | Run a repeatable multi-phase session and capture before/after evidence |

CI scenarios are deterministic and fast. Tuning is slower, operator-driven,
and profile-specific (desktop vs server).

## Prerequisites

- FTMON daemon running (`ftmon status` shows a recent tick)
- Candidate monitor TOML in `~/.config/ftmon/monitors/` (`ftmon check` clean)
- Optional: `stress-ng` for CPU phases (`apt install stress-ng`)
- A quiet hour or two — notifications will fire unless you edit settings mid-run

## Quick start

```sh
# Terminal A: watch notifications
tail -f ~/.local/state/ftmon/notifications.jsonl

# Terminal B: one slow leak (2 MiB/min ≈ TS-04 firefox-leak scenario)
uv run python tools/tuning/leaky.py --rate-mib-per-hour 120 --duration 2700

# Or the full profile (~2–3 h with stress-ng installed)
chmod +x tools/tuning/stress_profile.sh
tools/tuning/stress_profile.sh -o tuning/evidence/$(hostname)-run1

# Smoke/dry-run (~15–20 min)
QUICK=1 tools/tuning/stress_profile.sh -o tuning/evidence/$(hostname)-quick1
```

Evidence lands under `tuning/evidence/` (gitignored if you add it to `.gitignore`;
do not commit host-specific results).

## Workload catalogue

### Memory (`leaky.py`)

| Mode | Command | Expected at stock defaults |
| --- | --- | --- |
| Slow leak | `--rate-mib-per-hour 120 --duration 2700` | Should open `leak-warn` after ~45m slope + confirm |
| Fast leak | `--rate-mib-per-hour 480 --duration 1800` | Should open warn, possibly crit |
| Startup burst | `--burst-mib 200 --rate-mib-per-hour 0 --duration 1500 --hold` | Tests false positives from warm-up ramps |
| Benign name | `--process-name chrome …` with exempt list | Should **not** notify when exempted |

Process name defaults to `tuning-leaky` (≤15 characters — Linux `comm` length) so
it is easy to spot in `ftmon incidents` and does not collide with real apps.

### CPU (`stress_profile.sh` phases)

| Phase | Behaviour | Tests |
| --- | --- | --- |
| `cpu-sustained` | 2 cores at 100% for 20m | `hog-warn` / `hog-crit` thresholds |
| `cpu-burst` | 90% for 30s every 5m | compile-like spikes; should **not** page if `confirm_cycles` is high enough |

Set `STRESS_CPU=4` on larger machines. Exempt `ftmon` and compilers in `hog.toml`
when sustained self-sampling trips the hog monitor.

## Parameter grid (desktop starting point)

Copy builtins, then sweep one axis at a time. Score each run:

| Metric | How to measure |
| --- | --- |
| **True positive** | `leaky.py` at 120 MiB/h opens incident within 60m |
| **False positive** | burst / chrome-exempt / cpu-burst produces **zero** popups |
| **Notification cost** | lines in `notifications.jsonl` during benign phases |

Suggested grid for `leak.toml`:

| Parameter | Stock (server-ish) | Desktop candidates |
| --- | --- | --- |
| `warn_mb_per_h` | 32 | 64, 96, 128 |
| `crit_mb_per_h` | 128 | 192, 256 |
| `confirm_cycles` | 3 | 6, 9, 15 |
| `promote_mb` | 16 | 32, 48 |
| slope window (`45m` in rules) | 45m | 45m, 90m |
| `exempt` | (commented) | browsers, IDE, `tuning-leaky` during calibration |

Suggested grid for `hog.toml`:

| Parameter | Stock | Desktop candidates |
| --- | --- | --- |
| `warn_pct` / `crit_pct` | 80 / 90 | keep or 85 / 95 |
| `confirm_cycles` | 5 | 8, 10 |
| `exempt` | (commented) | `ftmon`, compilers |

**Server profile:** keep stock builtins unless a workload proves otherwise;
the demo VPS soak is the reference.

## Scoring a run

After `stress_profile.sh` completes:

```sh
OUT=tuning/evidence/<stamp>
diff -u "$OUT/baseline-incidents.txt" "$OUT/after-leak-slow-incidents.txt"
grep -c '"kind": "open"' "$OUT"/*-notifications.jsonl 2>/dev/null || true
uv run python tools/soak_report.py ~/.local/share/ftmon/ftmon.db -o "$OUT/report.md"
```

Record in a simple table (spreadsheet or markdown in `tuning/evidence/`):

- candidate settings (hash or pasted `parameters` block)
- which phases opened incidents
- time from phase start to first `open` notification
- false positives during `cooldown`

Promote the winner to `~/.config/ftmon/monitors/`. The daemon reloads within
~30s; no restart.

## Desktop vs server

| Concern | Desktop | Server |
| --- | --- | --- |
| Leak noise | High (Electron, browsers) | Low |
| Events noise | Bluetooth, tracker, gnome-shell | sshd, systemd, kernel OOM |
| Defaults | Tune with this procedure | Keep shipped builtins |
| `init --profile` | Future: ship tuned copies | `server` disables desktop popups |

Do **not** weaken shipped `design/builtins/*.toml` to fix one desktop; tune the
operator copy or the desktop init profile.

## When to stop tuning

- Known leak (120 MiB/h) opens within one hour
- Benign burst and exempted names stay silent
- Fewer than one unexpected popup per work day during normal use
- Then return to [soak procedure](soak-procedure.md) for the 30-day clock

## See also

- [User manual — tuning knobs](manual.md) (`confirm_cycles`, `exempt`)
- [Definition reference](definitions.md)
- `uv run pytest tests/unit/test_fixtures.py -k firefox` — scenario regression

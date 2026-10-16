# Desktop tuning scorecard (XPS-15 workstation)

Evidence directories:

- `tuning/evidence/dsheehan-XPS-15-full1/` — leak phases (interrupted before CPU)
- `tuning/evidence/dsheehan-XPS-15-full1-resume/` — CPU + cooldown
- Host: 31 GiB RAM, GNOME desktop, Cursor/Chrome stack, DisplayLink USB-C Hybrid UHD dock (`17e9:6000`), EVDI + Intel i915 + NVIDIA GTX 1050

Settings under test match `design/profile/desktop/` (now installed by
`ftmon init --profile desktop`). Host-local extras that are not yet profile
recipes (e.g. external `temperature`) are noted below.

## Leak monitor

| Workload | Rate | Result | Verdict |
| --- | --- | --- | --- |
| `tune-leak-slow` | 120 MiB/h × 45m | `leak-warn` opened (~120 MB/h measured) | **TP** |
| `tune-leak-fast` | 480 MiB/h × 30m | `leak-crit` opened (~482 MB/h measured) | **TP** |
| `tune-leak-brst` | 100 MiB instant | No alert on burst process | **TN** (burst ignored) |
| `tune-leak-brst` | (ambient) | `gnome-shell` warned once during stress | **FP** → exempt in profile |
| Cooldown | idle 15m | 0 new notifications | **TN** |

**Chosen desktop values:** `warn_mb_per_h=96`, `crit_mb_per_h=256`, `promote_mb=48`,
`growth_confidence=0.85`, `confirm_cycles=9`, exempt browsers/IDE/`gnome-shell`/`1password`.

## Hog monitor

| Workload | Result | Verdict |
| --- | --- | --- |
| `cpu-sustained` (2×100% × 20m) | `hog-warn` on both `stress-ng-cpu` workers | **TP** |
| `cpu-burst` (30s spikes) | No new hog incidents during bursts | **TN** |
| Cooldown | Hog incidents cleared | — |

**Chosen desktop values:** keep 80/90% thresholds, `confirm_cycles=6`, exempt `ftmon`.

## Disk monitor

| Signal | Result | Verdict |
| --- | --- | --- |
| `/` at ~59% during stress | `filling` warnings (monotonic noise under load) | **FP** |
| `/var/snap/firefox/.../host-hunspell` | `filling` warnings | **FP** (snap bind mount) |

**Chosen desktop values:** `filling_frac=0.90`, `used_pct > 70` gate on filling rule,
`confirm_cycles=9` on filling (space ladder unchanged).

## Events monitor

| Signal | Result | Verdict |
| --- | --- | --- |
| Kernel Bluetooth spam | Suppressed by excluding `kernel` from catch-all `errors` | **TN** |
| OOM (30d journal scan) | No real OOM on host | rule kept with `confirm_count=2` / `1h` |
| EVDI `Edid length too small` | Fires on every DisplayLink connect / boot | **FP** if alerted — do **not** rule on it |
| `Can't update stage views` | ~2k hits / long docked boot; bursts of 6–8 on routine modeset | **FP** if alerted — too noisy alone |
| X11 `lagging behind` / `too slow` (2026-10-14 10:10:52) | Preceded compositor freeze by minutes | **TP** → `x11-input-lag` |
| gnome-shell `already disposed` ×2 (2026-10-14 10:19:56) | During freeze while host still sampled | **TP** → `gnome-shell-disposed` |
| i915 `Atomic update failure` (2026-10-10) | Modeset stress on same dock path | **TP** → `i915-atomic` (`confirm_count=2` / `1h`) |
| GPU hang / NVRM Xid / soft·hard lockup | Not observed this incident; kept as crash precursors | rule kept |

**Chosen desktop values** (`events` v4):

- Keep catch-all `errors` excluding `tracker-*`, `gnome-shell`, `kernel`
- Keep `oom` as above
- Add kernel GPU ladder: `i915-atomic`, `gpu-hang`, `nvidia-xid`, `lockup`
- Add UI-freeze precursors: `x11-input-lag` (1 hit, 30m cooldown), `gnome-shell-disposed` (2 in 10m)

### Incident note — 2026-10-14 UI freeze (host alive)

Not a full kernel panic. Sequence on ~19d uptime, lid closed, DisplayLink 5760×2160 layout:

1. **10:10:52** — X11 input lag (`system is too slow`)
2. **10:11:05** — screen unlock (fingerprint/password)
3. **10:11:07+** — gnome-shell stage-allocation stress; desktop becomes unusable
4. **10:14–10:23** — FTMON / NetworkManager / auditd still running (UI dead, host alive)
5. **10:24:18** — lid opened (recovery attempt, not the trigger) → modeset 5760×2160
6. **10:25:59** — unclean reboot (`last` reports crash; EFI dirty bit)

Operator takeaway: prefer SSH/`loginctl`/restart `gnome-shell` before hard power-off when FTMON is still ticking; treat `x11-input-lag` + `gnome-shell-disposed` as “compositor wedged” even if load looks fine.

## Temperature monitor (host-local external check)

Not part of `ftmon init --profile desktop`; installed via Exchange / external
`check_temperature` on this host.

| Signal | Result | Verdict |
| --- | --- | --- |
| Sustained ≥80 °C / ≥90 °C | 5 / 10 confirm cycles filter boost spikes | keep (laptop FP defense) |
| Brief 97 °C peak (2026-10-14 09:13) | Invisible to 5/10-cycle plugin gates | **FN** → add `temp-spike` |
| Pre-freeze 86 °C (10:14) | Short of sustained critical | expected under spike gate |

**Chosen host values** (`temperature` v6): keep warn/crit plugin gates; add
`spike_celsius=95`, `temp-spike` with `confirm_cycles=2` / `clear_cycles=5`.

## Self monitor

| Signal | Result | Verdict |
| --- | --- | --- |
| Steady daemon CPU ~3% (10m avg) on busy desktop | Stock `cpu_budget_pct=1.5` warned constantly | **FP** at stock threshold |
| RSS ~50 MiB, DB ~35 MiB | Within budgets | **TN** |

**Chosen desktop values:** `cpu_budget_pct=4.0`, `confirm_cycles=6` on `cpu-budget`. RB-01
design target (1%) is unchanged; soak still reads raw `self` metrics for evidence.

## Profile promotion

| Profile | Monitor source |
| --- | --- |
| `ftmon init --profile desktop` | `design/profile/desktop/` (this scorecard) |
| `ftmon init --profile server` | `design/builtins/` (stock thresholds) |

Operators who re-run calibration should add `'matches(name, "^tune-leak")'` to
`exempt` locally while `tools/tuning/stress_profile.sh` is running.

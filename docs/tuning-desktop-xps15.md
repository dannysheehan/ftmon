# Desktop tuning scorecard (XPS-15 workstation)

Evidence directories:

- `tuning/evidence/dsheehan-XPS-15-full1/` — leak phases (interrupted before CPU)
- `tuning/evidence/dsheehan-XPS-15-full1-resume/` — CPU + cooldown
- Host: 31 GiB RAM, GNOME desktop, Cursor/Chrome snap stack

Settings under test match `design/profile/desktop/` (now installed by
`ftmon init --profile desktop`).

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

## Profile promotion

| Profile | Monitor source |
| --- | --- |
| `ftmon init --profile desktop` | `design/profile/desktop/` (this scorecard) |
| `ftmon init --profile server` | `design/builtins/` (stock thresholds) |

Operators who re-run calibration should add `'matches(name, "^tune-leak")'` to
`exempt` locally while `tools/tuning/stress_profile.sh` is running.

# FTMON soak procedure (TS-17)

This document records how to run the pre-v1.0 operational soak required by
SPEC TS-17. The soak proves RB-01/DM-05 budgets, notification outbox draining,
and daemon stability over real wall-clock time.

## Requirements

- Two real hosts: one `desktop` profile, one `server` profile
- At least **30 consecutive days** per host without unexplained daemon restarts
- Evidence attached to release notes at v1.0

## Per-host setup

```sh
# Install from a release candidate checkout
uv sync
uv pip install -e . --prefix ~/.local

# Desktop profile (user systemd unit)
ftmon init --profile desktop
systemctl --user enable --now ftmon

# Server profile (see docs/install.md for hardened system unit)
ftmon init --profile server
# ... install ftmon-server.service per docs/install.md
```

## Weekly evidence capture

On hosts with the packaged capture script installed:

```sh
/opt/ftmon/bin/capture-soak-evidence.sh
```

Or manually:

```sh
# While the daemon is running
uv run python tools/soak_report.py ~/.local/share/ftmon/ftmon.db \
  -o soak/evidence/$(hostname)-$(date +%Y%m%d).md

ftmon doctor
ftmon incidents --all > soak/evidence/$(hostname)-incidents-$(date +%Y%m%d).txt
```

Store reports under `soak/evidence/` (gitignored) or attach to release notes.

Pass `--days N` to narrow the window; the default 30 is the TS-17 gate.

**Scope the window to the build under test.** A leg upgraded in place keeps the
previous build's history in the same database, so a rolling 30-day window blends
two builds and the older, longer one wins the percentiles — on the 2026-09-06
capture that was the difference between a reported RSS p95 of 194.5 MB and the
running build's actual 74.7 MB. Pass `--since` with the leg's start:

```sh
uv run python tools/soak_report.py ~/.local/share/ftmon/ftmon.db \
  --since 2026-09-04T13:49:15+10:00 -o report.md
```

`--since` takes ISO-8601 or epoch seconds and overrides `--days`. The packaged
capture script reads it from the host manifest (`window_starts_at`, falling back
to `deployed_at`), so the weekly timer needs no editing when the clock starts.
Every report prints the window it measured; check that line before quoting a
number from it.

## How each budget is measured

The report deliberately does not take the most convenient series for each
budget, because two of them do not say what the requirement says.

- **CPU is a 10-minute average.** RB-01 bounds CPU "averaged over 10 m", so
  percentiles are taken over 10-minute means, not over the per-tick samples
  underneath. A single tick's spike is not a budget breach — on real soak data
  the per-sample maximum read 6.60 % where the 10-minute maximum was 1.98 %.
- **Storage is used pages, not the file.** DM-05's target is used pages, and
  RB-02 states the physical file participates in no budget identity. WAL and
  freelist hold the file near the ceiling long after retention has released the
  space, so `db_file_mb` is reported but never judged. A leg sitting *at* the
  target with `db_degrading` clearing normally is retention working correctly,
  not a failure; sustained `db_degrading` is the failure.
- **Percentiles read the stored tiers directly.** `Query.series` downsamples
  with LTTB, which selects visually representative points for a chart — the
  wrong sample for a distribution.
- **The hourly tail is excluded from the verdict.** A 30-day window outlives
  raw retention, so its oldest stretch survives only as hourly rollups, which
  cannot express a 10-minute average or a peak. Those are summarized separately
  as trend, and the report states how much of the window the verdict covers.

## Gate checklist (TS-17)

| Check | Source |
| --- | --- |
| No unexplained daemon restarts | `self` daemon-start events, journalctl |
| RB-01 CPU: p95 of 10-minute means | `soak_report.py` `cpu_pct (10 m avg)` row |
| RB-01 RSS: daemon, and web/MCP ≤ 80 MB each | `soak_report.py` `rss_mb` row |
| DM-05: `db_used` at or under target, degradation not sustained | `soak_report.py` `db_used_mb` row / `ftmon doctor` |
| Outbox draining | pending `notification_deliveries` in report |
| No unexplained `self` incidents | report incident section |
| Clean `ftmon doctor` at end | doctor JSON `ok: true` |

A leg whose `self` monitor alarms above the normative budget cannot evidence
the incident criterion: set `cpu_budget_pct` to RB-01's figure on soak hosts, or
record the deviation in the host manifest.

## Clock reset

The soak clock restarts only for daemon-crash fixes, not unrelated commits.

## Per-host manifest

Each soak host keeps a private start record outside the repository, for example
`/var/lib/ftmon/soak/manifest.json` on a server-profile deployment. Do not commit
hostnames, addresses, or evidence files; only attach exported reports to release
notes at v1.0.

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

## Gate checklist (TS-17)

| Check | Source |
| --- | --- |
| No unexplained daemon restarts | `self` daemon-start events, journalctl |
| RB-01 budgets held | `soak_report.py` self-monitor percentiles |
| DB ≤ 200 MB after retention cycles | `soak_report.py` / `ftmon doctor` |
| Outbox draining | pending `notification_deliveries` in report |
| No unexplained `self` incidents | report incident section |
| Clean `ftmon doctor` at end | doctor JSON `ok: true` |

## Clock reset

The soak clock restarts only for daemon-crash fixes, not unrelated commits.

## Per-host manifest

Each soak host keeps a private start record outside the repository, for example
`/var/lib/ftmon/soak/manifest.json` on a server-profile deployment. Do not commit
hostnames, addresses, or evidence files; only attach exported reports to release
notes at v1.0.

# Pending apt package updates

## Why

Debian/Ubuntu hosts accumulate pending packages quietly until someone notices
a security advisory or a surprise reboot into a large upgrade. FTMON's built-ins
do not watch apt. This recipe counts upgradable packages (total and security
pocket), ages the apt cache, and raises confirmed incidents when security work
is waiting, the backlog crosses an operator threshold, or metadata goes stale.
Trends on `updates_total` show whether the backlog is growing rather than being
cleared.

## Install

This is an original FTMON-maintained `ftmon-json` check (MIT). Install the
script from the recipe (do not put it under FTMON's own data/state dirs):

```sh
# Dedicated / multi-user host:
sudo install -d -o root -g root -m 0755 /usr/local/lib/ftmon/checks
sudo install -o root -g root -m 0755 \
  extra-monitors/package-updates/scripts/check_apt_updates \
  /usr/local/lib/ftmon/checks/check_apt_updates

# Single-user desktop (daemon uid owns the file):
install -d -m 0755 ~/.local/lib/ftmon/checks
install -m 0755 \
  extra-monitors/package-updates/scripts/check_apt_updates \
  ~/.local/lib/ftmon/checks/check_apt_updates
# then set argv[0] in checks.toml to that absolute path
```

Verify trust before registration:

```sh
ftmon check trust /usr/local/lib/ftmon/checks/check_apt_updates
```

Requires the host `apt` CLI. The check only runs `apt list --upgradable` and
reads world-readable cache stamps under `/var/lib/apt` and `/var/cache/apt`.
It never runs `apt-get update` or installs packages.

## Configure

```sh
ftmon recipe install package-updates
```

Defaults (`-w 20`, cache stale at 7 days inside the check):

- **Security pending** (`updates_security > 0`) is critical after two confirms —
  the operational signal for hosts that are not auto-upgraded.
- **Many pending** warns when total upgradable packages exceed
  `updates_warn_count` (keep `checks.toml` `-w` equal to that parameter so
  `plugin_state` mirrors the monitor rule).
- **Cache stale** warns when `cache_age_s` exceeds seven days — usually means
  unattended-upgrades / apt timers stopped refreshing indexes.
- **Updates rising** is a notice on sustained backlog growth (slope + monotonic
  fraction over 6h), not a page by itself.

`apt list --upgradable` lines look like
`name/suite version arch [upgradable from: …]` (four fields before the
bracket). The check's regex must match that shape; an extra field matches
nothing and silently reports zero packages.

## Test

```sh
/usr/local/lib/ftmon/checks/check_apt_updates -w 20
echo "$?"   # must be 0; severity is JSON state
```

Exit status is always **0** for `ftmon-json`. Severity is the JSON `state`
field (0 OK, 1 warning, 2 critical, 3 unknown). Fixtures under `fixtures/`
match the live metric labels. Direct behavioral tests (offline):

```sh
uv run pytest -q extra-monitors/package-updates/tests
```

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevation and no network from the check itself: it invokes local `apt` and
reads cache metadata. Keep the installed file root- or daemon-owned, mode
`0755`, not group/world-writable, and not a symlink (`ftmon check trust`
enforces this). Remediation (`apt update` / `apt full-upgrade`) stays outside
FTMON actions unless the operator adds a separate, reviewed action script.

## Upstream and licence

Original FTMON recipe script under
[extra-monitors/package-updates](https://github.com/dannysheehan/ftmon/tree/main/extra-monitors/package-updates),
`MIT`. FTMON redistributes this script as the recipe's maintained check.

Verified on 2026-07-30 on Ubuntu 24.04.4 LTS: live
`check_apt_updates -w 20` returned exit 0 with `updates_total=22`,
`updates_security=0`, `cache_age` in seconds, `state=1` (above warn count);
trust check passed for the installed path.

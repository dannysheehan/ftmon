# Laptop battery charge and health

## Why

Always-plugged laptops still need battery monitoring. A loose USB-C / Thunderbolt
dock connection can drop mains while the OS keeps running: charge falls through
the BIOS conservation floor, screens flap when the dock USB controller dies, and
the power LED misbehaves. Separately, packs wear — `charge_full` declines versus
design — so a "full" battery holds less than it used to. FTMON's built-ins do
not sample ACPI batteries; this recipe adds charge, health and AC-presence
history plus confirmed alerts.

## Install

This is an original FTMON-maintained `ftmon-json` check (MIT). Install the
script from the recipe (do not put it under FTMON's own data/state dirs):

```sh
# Dedicated / multi-user host:
sudo install -d -o root -g root -m 0755 /usr/local/lib/ftmon/checks
sudo install -o root -g root -m 0755 \
  extra-monitors/battery/scripts/check_battery \
  /usr/local/lib/ftmon/checks/check_battery

# Single-user desktop (daemon uid owns the file):
install -d -m 0755 ~/.local/lib/ftmon/checks
install -m 0755 \
  extra-monitors/battery/scripts/check_battery \
  ~/.local/lib/ftmon/checks/check_battery
# then set argv[0] in checks.toml to that absolute path
```

Verify trust before registration:

```sh
ftmon check trust /usr/local/lib/ftmon/checks/check_battery
```

No package install is required. The check only reads `/sys/class/power_supply`.

## Configure

```sh
ftmon recipe install battery
```

Defaults (`-w 40,60` / `-c 15,40` plus `--require-ac`):

- **Charge** warning at ≤40%, critical at ≤15% — catches packs that fell
  below a typical Dell 50–90% conservation window after a mains flap.
- **Health** warning at ≤60% of design, critical at ≤40% — surfaces worn
  cells without waiting for a charge cliff.
- **`--require-ac`** raises at least warning when the AC adapter node is
  offline, so a dock power loss alerts before the pack is empty.

Plugin thresholds in `checks.toml` are authoritative for `plugin_state`. Keep
`warn_charge_pct` / `crit_charge_pct` (and the health pair) in the monitor TOML
aligned so the dashboard glance matches. A separate `ac-lost` rule watches the
stored `ac_online` gauge with two confirm cycles so brief cable reseats do not
page.

Pin a battery or adapter name only when discovery picks the wrong supply:

```text
"--battery", "BAT0",
"--ac", "AC",
```

### Why there is no Trends growth profile

Charge percentage is mean-reverting (drain then recharge). Health declines over
months, but a rising `slope(health_pct)` never applies, and a multi-week window
needs long retention before a decline rate is trustworthy. Prefer absolute
plugin thresholds plus Metrics charts of `charge_pct` / `health_pct` /
`ac_online`.

## Test

```sh
/usr/local/lib/ftmon/checks/check_battery -w 40,60 -c 15,40 --require-ac
echo "$?"   # must be 0; severity is JSON state
```

Exit status is always **0** for `ftmon-json`. Severity is the JSON `state`
field (0 OK, 1 warning, 2 critical, 3 unknown). Fixtures under `fixtures/`
match the live metric labels. Direct behavioral tests:

```sh
uv run pytest -q extra-monitors/battery/tests
```

Unknown coverage is a missing battery node (`--battery BAT0` against an empty
sysfs tree in tests, or a desktop without a pack).

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevation and no network: the script only reads sysfs power_supply nodes.
Keep the installed file root- or daemon-owned, mode `0755`, not
group/world-writable, and not a symlink (`ftmon check trust` enforces this).

## Upstream and licence

Original FTMON recipe script under
[extra-monitors/battery](https://github.com/dannysheehan/ftmon/tree/main/extra-monitors/battery),
`MIT`. FTMON redistributes this script as the recipe's maintained check.

Verified on 2026-07-29 on Ubuntu 24.04 (Dell XPS 15 9560, Linux 7.0): live
`BAT0`/`AC` sample returned charge/health/ac_online metrics with exit 0;
warn/crit reproduced by lowering `-w`/`-c`; missing battery path returned
`state=3`.

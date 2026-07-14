# Hardware sensors (lm_sensors)

## Why

`check_sensors` asks lm_sensors whether any channel is in **ALARM** or
**FAULT**. That catches fan stoppages and driver fault lines that FTMON's
thermal-zone Celsius check never sees. There is no perfdata — the value is the
plugin state itself — so FTMON confirms and notifies without a Trends growth
profile.

## Install

On Ubuntu 24.04:

```sh
sudo apt update
sudo apt install monitoring-plugins-basic lm-sensors
sudo sensors-detect --auto   # once per machine; review if unsure
/usr/lib/nagios/plugins/check_sensors
```

Executable: `/usr/lib/nagios/plugins/check_sensors` (regular shell script, not
a symlink). Upstream:
<https://www.monitoring-plugins.org/doc/man/check_sensors.html>.

## Configure

```sh
ftmon recipe install sensors
```

No numeric thresholds. Optional `--ignore-fault` can be added to argv if FAULT
lines from a known-noisy chip should not raise unknown. Confirm cycles are
**two** so brief ALARM chatter does not open incidents immediately.

### Why there is no Trends profile

The first Nagios line is status text only (`SENSORS OK` / alarm / fault) with
**no** `| perfdata`. Growth Trends need a numeric series; use the separate
`temperature` recipe (or Metrics on other checks) for Celsius history.

## Test

```sh
/usr/lib/nagios/plugins/check_sensors
echo "$?"
```

Exit states are 0 OK, 1 warning (`sensors` non-zero), 2 critical (line matching
`ALARM`), and 3 unknown (`sensors` missing or FAULT without `--ignore-fault`).
Fixtures mirror Monitoring Plugins 2.3.5 message text (including the singular
`SENSOR CRITICAL` wording).

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevation and no network. The plugin runs `sensors` from a fixed PATH and
reads hwmonsysfs via that tool. Install `lm-sensors` so the binary exists for
the FTMON account.

## Upstream and licence

[Monitoring Plugins `check_sensors`](https://www.monitoring-plugins.org/doc/man/check_sensors.html),
`GPL-3.0-or-later`. FTMON does not redistribute it.

Verified on 2026-07-14 with Monitoring Plugins **2.3.5**
(`monitoring-plugins-basic` on Ubuntu 24.04, Dell XPS 15): `SENSORS OK` with
fans and coretemp present; `trusted_executable_path` accepts the script.

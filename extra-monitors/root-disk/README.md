# Root filesystem space

## Why

FTMON already samples local disks natively, but many sites already operate
`check_disk` from Monitoring Plugins and trust its warn/critical thresholds.
This recipe reuses that exact probe while FTMON adds confirmed incidents,
bounded history of used bytes on `/`, and an explicit growth Trend when the
filesystem is filling steadily even before the plugin threshold trips.

## Install

On Ubuntu 24.04, install the distribution package:

```sh
sudo apt update
sudo apt install monitoring-plugins
```

The executable is `/usr/lib/nagios/plugins/check_disk`. Upstream documentation:
<https://www.monitoring-plugins.org/doc/man/check_disk.html>.

## Configure

Install while FTMON is running — no daemon restart:

```sh
ftmon recipe install root-disk
```

`ftmon check install root-disk` is equivalent. From a git checkout the recipe
is discovered automatically; otherwise set `FTMON_EXTRA_MONITORS` to your
`extra-monitors/` directory or pass the recipe path directly.

The default thresholds warn below **20% free** and go critical below **10% free**
on `/`. Edit `checks.toml` after install if your root volume needs different
plugin thresholds; edit `growth_bytes_per_h` in the monitor TOML for FTMON's
steady-growth rule.

## Test

Run the exact command as the FTMON user:

```sh
/usr/lib/nagios/plugins/check_disk -w 20% -c 10% -p / -t 10
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical, and 3 unknown. Fixture output in
`fixtures/` was taken from Monitoring Plugins 2.3.5 on Ubuntu 24.04.

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevated privileges are required: the plugin reads filesystem statistics for
the chosen mount point. It does not contact the network.

## Upstream and licence

The plugin comes from the
[Monitoring Plugins project](https://www.monitoring-plugins.org/doc/man/check_disk.html)
and is distributed under `GPL-3.0-or-later`. FTMON does not redistribute it.

Verified on 2026-07-13 with Monitoring Plugins 2.3.5 (`monitoring-plugins
2.3.5-1ubuntu3` on Ubuntu 24.04).

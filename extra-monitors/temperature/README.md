# Hardware temperature

## Why

FTMON's built-ins cover load, CPU hogs and memory pressure, but not board or
package temperature. Sustained heat is an early signal of fan failure, dust,
aggressive workloads, or a dying sensor path. This recipe uses
`check_temperature` from nagios-plugins-linux so FTMON can confirm over-temp
states and keep Celsius history for the hottest thermal zone.

## Install

Upstream project (do not vendor plugins into FTMON):
<https://github.com/madrisan/nagios-plugins-linux>

The check needs a readable `/sys/class/thermal` tree. On Ubuntu/Debian there is
no complete `apt` package for the full suite; build a release into the usual
plugin directory:

```sh
git clone --branch v35 --depth 1 \
  https://github.com/madrisan/nagios-plugins-linux.git
cd nagios-plugins-linux
autoreconf --install
./configure --libexecdir=/usr/lib/nagios/plugins
make
sudo make install
/usr/lib/nagios/plugins/check_temperature -V
```

Gentoo can install `net-analyzer/nagios-plugins-linux-madrisan` instead. Other
distributions may ship distro-built packages from the project's `packages/`
targets.

## Configure

```sh
ftmon recipe install temperature
```

Defaults warn at **80°C** and go critical at **90°C** for the hottest zone
(plugin behaviour when `-t` is omitted). Brief turbo spikes are common, so
warning and critical rules confirm for **three** cycles before notifying.

To pin a stable sensor (package temp, ACPI zone, …), list zones and edit the
registry argv:

```sh
/usr/lib/nagios/plugins/check_temperature --list
# then add: "-t", "<zone-number>"
```

The monitor entity stays `thermal:max` so history remains one series when the
hottest zone changes between polls. Raise or lower `-w`/`-c` in `checks.toml`
for your chassis; leave FTMON's rule expressions alone unless you change groups.

## Test

```sh
/usr/lib/nagios/plugins/check_temperature -w 80 -c 90
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical, and 3 unknown. Fixtures under
`fixtures/` match observed nagios-plugins-linux v35 first-line output
(including `temp=…C` perfdata). Unknown coverage uses a missing zone:

```sh
/usr/lib/nagios/plugins/check_temperature -t 999
```

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevated privileges: the plugin only reads sysfs thermal nodes. It does not
contact the network. On locked-down hosts, ensure the FTMON account can read
`/sys/class/thermal/*/temp`.

## Upstream and licence

[nagios-plugins-linux](https://github.com/madrisan/nagios-plugins-linux) by
Davide Madrisan, `GPL-3.0-or-later`. FTMON does not redistribute the plugin.

Verified on 2026-07-14 with nagios-plugins-linux **v35** on Ubuntu 24.04
(Dell XPS 15): hottest-zone OK at `-w 80 -c 90`, warn/crit confirmed by
lowering thresholds, unknown from `-t 999`.

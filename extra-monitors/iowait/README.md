# CPU I/O wait

## Why

FTMON's `load` builtin watches PSI and memory pressure, but not classic CPU
**iowait** — time spent idle waiting on block I/O. High iowait with moderate
load is a different failure mode: saturated disks, failing media, backups that
starve interactive work. `check_iowait` samples that slice so FTMON can confirm
spikes and keep a Metrics history of iowait versus user/system/idle/steal.

## Install

Upstream (do not vendor into FTMON):
<https://github.com/madrisan/nagios-plugins-linux>

Same build path as other nagios-plugins-linux recipes on Ubuntu/Debian:

```sh
git clone --branch v35 --depth 1 \
  https://github.com/madrisan/nagios-plugins-linux.git
cd nagios-plugins-linux
autoreconf --install
./configure --libexecdir=/usr/lib/nagios/plugins
make
sudo make install
/usr/lib/nagios/plugins/check_iowait -V
```

Upstream's `check_iowait` is a **symlink** to `check_cpu`. FTMON refuses
symlinked argv[0] (SE-07). Install a regular-file copy that keeps the
`check_iowait` basename (the plugin selects mode from argv[0]):

```sh
sudo mkdir -p /usr/local/lib/ftmon
sudo install -m 0755 /usr/lib/nagios/plugins/check_cpu \
  /usr/local/lib/ftmon/check_iowait
/usr/local/lib/ftmon/check_iowait -m -w 20% -c 40% 1 2
```

On a single-user desktop without sudo for `/usr/local`, copy to a path you own
(for example `~/.local/lib/ftmon/checks/check_iowait`) and set that absolute
path as `argv[0]` in `checks.toml` after recipe install.

## Configure

```sh
ftmon recipe install iowait
```

Defaults warn at **20%** iowait and go critical at **40%**, using a **1 s**
sample (`delay 1` / `count 2`). `-m` omits the CPU model string from the
status line. Three confirm cycles damp short bursts.

A separate FTMON rule warns when the **15-minute average** iowait exceeds
**15%** (`sustained_iowait_pct`), even if individual plugin samples dip below
the instant thresholds — useful for backups that keep disks warm for a while.

### Why there is no Trends growth profile

Growth Trends (as used for disk fill or temperature climb) assume a mostly
monotonic climb whose rate is meaningful. I/O wait is **spiky and
mean-reverting**: a backup, `apt upgrade`, or IDE index run drives iowait up,
then it falls. A rising `slope(cpu_iowait_pct)` over hours is usually a batch
job shape, not a steady underlying fault, so a growth Trend and rate-based
alert would be noisy and misleading. Prefer:

- plugin absolute thresholds for sharp stalls;
- the 15m average rule for sustained contention;
- **Metrics** charts of `cpu_iowait_pct` (and the other CPU % series) for
  investigation.

Tune `-w`/`-c` in `checks.toml` and `sustained_iowait_pct` in the monitor TOML
for your storage. VMs should also watch `cpu_steal_pct` on Metrics when the
hypervisor is contested.

## Test

```sh
/usr/lib/nagios/plugins/check_iowait -m -w 20% -c 40% 1 2
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical, and 3 unknown. Fixtures match
v35 first-line perfdata (`cpu_*=…%`). Unknown coverage uses the first line of
the plugin's usage banner when thresholds omit `%` (exit 3 on stderr in a live
run; the fixture is that banner line for offline parsing).

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevation and no network for the check itself: the plugin reads `/proc` CPU
accounting. The regular-file copy under `/usr/local/lib/ftmon/` should stay
root-owned and mode `0755` (not group/world-writable). Do not point FTMON at
the upstream symlink.

## Upstream and licence

[nagios-plugins-linux](https://github.com/madrisan/nagios-plugins-linux),
`GPL-3.0-or-later`. FTMON does not redistribute it.

Verified on 2026-07-14 with nagios-plugins-linux **v35** on Ubuntu 24.04
(Dell XPS 15): OK under `-w 20% -c 40% 1 2`; warn/crit reproduced by lowering
thresholds; invalid `-w 20` (no `%`) exits 3.

# HTTPS availability and response time

## Why

`check_http` provides a mature HTTPS probe without making HTTP client behavior
part of FTMON itself. FTMON adds the part a one-shot plugin cannot: confirmed
incidents and local history for response, connection, TLS and first-byte time.
That history helps distinguish a dead endpoint from a service that is still up
but becoming progressively slower.

This recipe checks <https://demo.ftmon.org/>. Copy it and change both the
registry hostname and monitor entity when protecting another endpoint.

## Install

On Ubuntu 24.04, install the distribution package rather than copying the
plugin into FTMON:

```sh
sudo apt update
sudo apt install monitoring-plugins
```

The executable used by this recipe is
`/usr/lib/nagios/plugins/check_http`. Upstream documentation is at
<https://www.monitoring-plugins.org/doc/man/check_http.html>.

## Configure

Install the recipe while FTMON is running — no daemon restart:

```sh
ftmon recipe install http-tls
```

This merges `[check.demo_ftmon_https]` from `checks.toml.example` into your
administrator registry, enables `demo_ftmon_https.toml`, and the daemon
reloads within ~30 seconds. `ftmon check install http-tls` is the same command.

On a fresh desktop host, `ftmon init --profile desktop` already installs the
monitor in the disabled state and registers the check when `check_http` is
present; run `ftmon recipe install http-tls` (or `ftmon monitor enable
demo_ftmon_https`) to turn it on.

To configure manually instead, merge the `[check.demo_ftmon_https]` table from
`checks.toml.example` into the administrator-owned check registry, then copy
`monitor.toml` into the active monitor directory and set `enabled = true`.

The exact argument list uses `--sni` because name-based HTTPS servers can
reject a handshake when the client omits the TLS server name. `-E` asks the
plugin for phase-level performance data. The plugin warns after one second,
becomes critical after three seconds and has its own eight-second timeout;
FTMON's nine-second outer timeout exists to bound failures while normally
allowing the plugin to report its own diagnosis.

The definition deliberately maps only `time`, `time_connect`, `time_ssl`,
`time_firstbyte` and `size`. These observed labels have stable units in the
verified output and answer useful operational questions. The response-time
metric also feeds a two-hour growth Trend so gradual degradation is visible
even before the plugin's absolute warning threshold is crossed.

Certificate-expiry checking is intentionally not combined with this recipe.
With Monitoring Plugins 2.3.5, `-C --continue-after-certificate` prints the
certificate result on one line and HTTP perfdata on the next. FTMON accepts
only first-line Nagios output, so combining them would silently discard the
timing metrics. Use a separate certificate check alias when expiry state is
required.

## Test

Run the exact command as the same unprivileged account that runs FTMON:

```sh
/usr/lib/nagios/plugins/check_http \
  -H demo.ftmon.org -S --sni -E -w 1 -c 3 -t 8
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical and 3 unknown. The fixture suite
checks representative OK, critical and unknown output offline; it does not
make `demo.ftmon.org` availability a CI dependency.

Validate the installed configuration with:

```sh
ftmon check
ftmon doctor
```

## Security and permissions

This check requires no elevated privileges. It makes an outbound HTTPS request
and therefore discloses the source host's address and the requested hostname to
the endpoint and normal network intermediaries. Do not put credentials in the
argument list; use a separately protected mechanism supported by the plugin if
an authenticated endpoint must be checked.

## Upstream and licence

The plugin comes from the
[Monitoring Plugins project](https://www.monitoring-plugins.org/doc/man/check_http.html)
and is distributed under `GPL-3.0-or-later with OpenSSL exception`. FTMON does
not redistribute it.

This recipe was exercised against `demo.ftmon.org` on 2026-07-12 using
Monitoring Plugins 2.3.5 from Ubuntu package `monitoring-plugins 2.3.5-1ubuntu3`.

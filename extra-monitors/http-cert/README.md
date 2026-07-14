# TLS certificate expiry

## Why

Certificate expiry is quiet until it is sudden: https://exchange.ftmon.org/
looks fine on availability probes until the leaf cert is past its notAfter.
`check_http -C` reconnects only for TLS and reports how many days remain,
so FTMON can confirm warning/critical windows without coupling to HTTP
latency checks.

This is a **separate** recipe from `http-tls`. With Monitoring
Plugins 2.3.5, combining `-C` and `--continue-after-certificate` prints the
certificate result on the first line and HTTP perfdata on the next. FTMON
keeps only first-line Nagios output, so a combined argv would silently drop
timing metrics. Keep latency history on `http-tls`; keep expiry here.

## Install

```sh
sudo apt update
sudo apt install monitoring-plugins
```

Executable: `/usr/lib/nagios/plugins/check_http`. Upstream:
<https://www.monitoring-plugins.org/doc/man/check_http.html>.

## Configure

```sh
ftmon recipe install http-cert
```

Defaults target **exchange.ftmon.org** with `-C 30,14` (warn below 30 days,
critical below 14) and SNI. Interval is **1h** — expiry does not need
minute-by-minute sampling. Copy the recipe or edit `checks.toml` /
`monitor.toml` entity and `-H` for another hostname.

### Why there is no Trends profile

`-C` returns status text only (no `| days=…` perfdata in 2.3.5). There is
nothing numeric to plot as a growth Trend; the alert is the remaining-day
threshold inside the plugin.

## Test

```sh
/usr/lib/nagios/plugins/check_http \
  -H exchange.ftmon.org -S --sni -C 30,14 -t 10
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical, and 3 unknown. Fixtures capture
observed message shapes against exchange.ftmon.org (OK) and forced tighter
`-C` windows (warning/critical). Unknown uses the plugin's missing-host usage
line.

```sh
ftmon check
ftmon doctor
```

## Security and permissions

No elevation. The check opens an outbound TLS connection to the named host and
therefore discloses the monitoring host's address. No credentials in argv.

## Upstream and licence

[Monitoring Plugins `check_http`](https://www.monitoring-plugins.org/doc/man/check_http.html),
`GPL-3.0-or-later with OpenSSL exception`. FTMON does not redistribute it.

Verified on 2026-07-14 with Monitoring Plugins **2.3.5** against
`exchange.ftmon.org` (certificate notAfter 2026-10-10 UTC): OK at `-C 30,14`,
WARNING under `-C 100,50`, CRITICAL under `-C 400,350`.

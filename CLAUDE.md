# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FTMON v2 succeeds a GPL-licensed Perl monitoring engine from 2001–2003. The
original source is intentionally kept out of this repository and remains
available from <https://sourceforge.net/projects/ftmon/>. Historical notes in
this file describe that system for design context; they are not instructions
to vendor or modify its source here.

## Running

The daemon requires `BASE_DIR` (env var or `-a` flag) pointing at the `base/` directory:

```sh
export BASE_DIR=/path/to/a/separate/ftmon-sourceforge-checkout/base
perl $BASE_DIR/bin/ftmon.pl -o <html_dir> -p <cfg_dir> -l <log_dir> -v <interval_seconds>
```

- Syntax-check a module: `perl -I$BASE_DIR/lib -I$BASE_DIR/lib/linux -c $BASE_DIR/lib/FTMON/Monitor.pm`
- Validate a config file: `ftmon.pl -c <full_path_to_config_file>`
- Query a running monitor: `ftmon.pl -z 1` (current problems) / `-z 2` (stats)
- Debug tracing: enable per-module `$FTMON::<Module>::DEBUG = 1` flags in `cfg/ftmon.cfg`, then run with `-d`
- `bin/ftmon_gui.pl` is a wxPerl (Wx) GUI for viewing status and editing config
- Production install uses `base/lib/linux/install.sh`, an init script (`start_ftmon.sh`), and `/etc/sysconfig/ftmon` (defines BASE_DIR, CFG_DIR, HTML_DIR, LOG_DIR, PID_FILE, INTERVAL); see `INSTALL.txt`

## Architecture

Two top-level directories, wired together at runtime by `BASE_DIR` / `-p cfg_dir`:

- `base/bin/` — entry points (`ftmon.pl` daemon, `ftmon_gui.pl` GUI, `ftmonsvc.pl` NT service wrapper)
- `base/lib/FTMON/` — the engine
- `cfg/` — the monitor definitions (see below)

### Config files are executable Perl

The central design fact: **config files are Perl source, executed with `do` into per-monitor packages**. `ConfigFileParser.pm` / `ConfigFile.pm` load them; each monitor config declares `package Vendor::Product::monitor;` and defines well-known hook subs the engine calls each cycle:

- `FT::MONITOR::VARIABLES_INIT` / `FT::MONITOR::VARIABLES` — declare tunables and row variables
- `FT::MONITOR::PRECALCS` — run the sampling command (e.g. `df -klT`) and parse output
- `FT::MONITOR::CALCS` / `POSTCALCS` — derived values (delta/avg/monotonic via `Calculation.pm`)
- `FT::MONITOR::THRESHOLDS` — returns an array of `[row_match_regex, condition_expr, severity, event_id, message, action]` rows
- `FT::MONITOR::SCHED`, `FT::MONITOR::COLS` — scheduling and display columns

Variable naming conventions carry meaning: `*_P` = user-tunable parameter, `*_V` = per-row sampled value, `*_I` = event id, `*_M` = message, `*_A` = action, `*_BLn_*` = baselined threshold level. Severities come from `$FT::ESEV[0..9]`.

### Config directory layering

`cfg/` is a hierarchy: `cfg/<Vendor>/<Product>/<monitor>.cfg`. Each level `do`-includes `common.cfg` from levels above, and each monitor splits into a top-level `.cfg` (site-tunable parameters/thresholds) and `impl/<monitor>.cfg` (the implementation: variable docs, PRECALCS, parsing). Optional `.bl` files hold baselines. Shipped trees: `cfg/RedHat/Linux/` (disk, load, process, service, socket, syslog) and `cfg/FTMON/` (self-monitoring and EventManager configs). Global settings live in `cfg/ftmon.cfg` (only editable by hand, requires restart) and `cfg/FTMON/common.cfg` (trading hours/holiday calendars used for scheduling suppression).

### Engine flow

`ftmon.pl` parses options, then `Scheduler.pm` drives the cycle: for each due `Monitor.pm` instance, run the hook subs, feed results to `CalculationManager.pm`, write RRD graphs (`RRD.pm`, platform-specific `RRDs.pm` in `lib/{linux,solaris,MSWin32}/`), generate HTML status pages into the html dir, and raise `Event.pm` objects. `EventManager.pm` dispatches events through subclasses in `lib/FTMON/EventManager/` (`Tivoli`, `BigBrother`, `LogFile`, `Linux`, `NT`); `EsculationPolicy.pm` (sic — the misspelling is throughout) escalates severity/message/action on repeat events. `LogFileScraper.pm` / `EventLogScraper.pm` handle log-based monitors; `SNMP.pm` handles SNMP polling. Passwords in configs are encrypted with `Crypt::CipherSaber` via `ftmon.pl -e string -w password`.

### Conventions and caveats

- Third-party modules in `base/lib/` (BER.pm, SNMP_Session.pm, SNMP_util.pm, NET/Telnet.pm, Crypt/CipherSaber.pm, Postemsg.pm, TraceFuncs.pm) are vendored and must not be modified — README.txt says to treat them as not part of FTMON.
- `base/lib/FTMON/NT/` is an XS module (perl Makefile.PL / make) compiled only on Windows for NT event log support.
- Global state lives in the `$FT::*` namespace (BASE_DIR, LOG_DIR, HTML_DIR, HOSTNAME, VERSION, etc.) — modules communicate through it rather than passing state.
- Code style is Perl 4/5-era: no `use strict`, `%FT::` package variables everywhere, 2-space indent, banner comment blocks with RCS keywords. Match it when editing.

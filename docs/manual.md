# FTMON User Manual

FTMON is a small systems monitor for your own desktop or workstation. It
watches for memory leaks, CPU hogs, disks filling up, dying services, and
log events worth knowing about — then tells you with a desktop notification,
remembers the history so you can ask "what happened Tuesday?", and exposes
everything to an AI assistant through MCP. It works fully without AI too,
through a CLI and a local web page.

> **Status**: this manual grows with the software. Sections marked
> *(arrives with M-n)* describe features not yet built; everything else is
> current. FTMON v2 is the successor of the 2001-2003 Perl FTMON.

---

## 1. Ideas you need (5 minutes)

FTMON's behavior follows from a handful of concepts. They come from the
original FTMON design, refined by twenty years of hindsight:

**Monitor.** A TOML file describing one thing to watch: where the numbers
come from (`source`), tunable `parameters`, computed values (`derived`),
which entities to ignore (`exempt`), and alerting `rules`. Your monitors
live in `~/.config/ftmon/monitors/` — plain files you can read, edit, and
put in git. Eight built-ins are installed by `ftmon init`: `leak`, `hog`,
`events`, `disk`, `load`, `service`, `net`, and `self` (FTMON watching its
own resource budget — the monitor must never become the hog).

**Entity.** One row a monitor watches: a process, a mount point, a systemd
unit, a listening port. Rules are evaluated per entity.

**Rule and confirmation.** A rule is a formula like
`avg(cpu_pct, "5m") > warn_pct`. It must hold for `confirm_cycles`
*consecutive* checks before anything fires — a one-cycle spike is not an
incident. This is FTMON's main defense against noise, and the reason it
doesn't cry wolf every time a compile pegs your CPU for a minute.

**Missing data is not evidence.** If a formula can't be computed (process
just started, PSI not available, baseline still learning), the result is
*unknown* — the rule neither fires nor counts as recovered. New machines
stay silent until there's enough data to say something true.

**Incident.** When a rule confirms, an incident opens and you get one
notification. While it persists you are re-notified on a backing-off
schedule (5 m → 15 m → 1 h → 6 h), with escalation if a more severe
threshold is crossed. Recovery sends one final notice. Acknowledging an
incident silences it without pretending it's fixed. Rules that form a
severity ladder (disk at 85/92/97%) share one incident that moves up and
down the ladder instead of stacking three.

**Restart and confirmation.** Confirm/clear cycle counters live in daemon
memory only (DESIGN D3). After a restart, open incidents are rebuilt from the
database with the owning rule marked confirmed — conservative, so an incident
never vanishes just because the daemon restarted, but a brief false positive
that cleared right before restart may need its full `confirm_cycles` again.

For how the daemon, CLI, web UI, and MCP fit together, see the
[architecture overview](../README.md#architecture) in the README.

**Episode.** The event-log flavor of an incident: a matching journal entry
opens it, repeats refresh it (with a cooldown), and it closes itself after
a quiet period. You get "OOM killer fired (12x in the last hour)", not
twelve notifications.

**Baseline.** For some metrics FTMON learns what "normal" is (a smoothed
average over ~3 days) and rules can compare against it:
`conn_total > baseline(conn_total) * 4`. Baselines return *unknown* for
the first ~24 h of data — baseline rules are automatically silent while
learning.

**Budget.** FTMON promises to stay under ~1% CPU, ~100 MB RAM, and a
200 MB database (about 48 h of raw minute-level data, a month at 5-minute
resolution, and roughly a year of hourly history). It prunes oldest,
coarsest data first, never incidents, and the `self` monitor warns you if
FTMON itself misbehaves.

---

## 2. Installation

```sh
git clone <repo> && cd ftmon
uv sync
uv run ftmon init --profile desktop  # use "server" for a headless host
uv run ftmon check     # validates every monitor definition
```

Directories FTMON uses (Linux):

| Path | Purpose |
| --- | --- |
| `~/.config/ftmon/config.toml` | global settings |
| `~/.config/ftmon/monitors/*.toml` | your monitor definitions |
| `~/.config/ftmon/monitors/drafts/` | definitions awaiting approval |
| `~/.config/ftmon/actions/` | operator-created action scripts |
| `~/.config/ftmon/checks.toml` | administrator-approved external commands |
| `~/.local/share/ftmon/ftmon.db` | metric/event/incident history (SQLite) |
| `~/.local/state/ftmon/` | daemon log + notification audit trail (JSONL) |

Running as a service is covered in `docs/install.md`; the packaged systemd
user unit runs `ftmon daemon`. Everything else (CLI, web UI, MCP) reads the
same database and works even when the daemon is stopped — you just see
stale data with a clear "last checked N minutes ago".

## 3. Daily use — CLI

```sh
ftmon status            # one screen; exit code 0/1/2 = clear/warnings/errors
ftmon check             # validate definitions (run after editing)
ftmon incidents         # open/acked problems (--all includes cleared)
ftmon ack 42            # stop re-notifying, keep watching
ftmon baseline reset leak         # forget learned "normal" (e.g. after an upgrade)
ftmon events --min-severity error   # stored journal events (--provider, --hours)
ftmon incident 42       # state, severity, rule, lifecycle, ack, ordered history
ftmon top rss --range 3h  # what was eating memory
ftmon doctor            # database integrity, WAL, orphans and config
```

On the desktop profile, popup notifications are live: an incident opens after
its confirmation cycles, re-notifies on the backing-off schedule, and sends
one recovery notice when it clears. The server profile explicitly disables
popups so remote channels can be configured instead. Every notification is
also appended to
`~/.local/state/ftmon/notifications.jsonl` (the audit trail).

Every listing command takes `--json` for scripting.

### Reading the web dashboard

Dashboard monitor tiles restore the original FTMON at-a-glance states while
adding an icon and text so color is never the only signal:

- `✓ clear` on green: no live incident;
- `▲ warning` on yellow: notice or warning is open or acknowledged;
- `✖ error` on red: error or critical is open or acknowledged;
- `● disabled` on grey: definition intentionally disabled;
- `? unknown` on grey: stale daemon, no committed data yet, or configuration error.

Acknowledging an incident stops repeat notifications but does not make its tile
green; only recovery clears the condition. A stale dashboard also never claims
green because old data cannot prove present health. Click a yellow/red tile to
open incidents already filtered to that monitor.

Some monitor definitions declare a primary glance readout below the state,
such as `/home 94% · warn 92% · error 97%`. The definition explicitly chooses
the metric, unit, `max|min` entity summary and labelled thresholds; FTMON does
not guess them from alert rules. Only a fresh raw value from an active entity is
shown, and entities matching the monitor's `exempt` expressions are excluded.
Missing or old data, disappeared entities, disabled monitors and stale
dashboard state omit the readout rather than presenting history as current.
The glance line is context only and never changes the tile's health state.

## 4. Tuning

Open the monitor's TOML file; every parameter has a comment saying what it
does. Change the value, run `ftmon check`, done — the daemon picks up
edits within 30 seconds, no restart. To apply a change immediately, run
`ftmon monitor rescan` (or send `SIGHUP` — `systemctl reload ftmon` with the
packaged units) — the same rescan runs at the next tick instead of waiting
out the interval. Three knobs cover most needs:

- **Thresholds** (`warn_pct`, `crit_mb_per_h`, ...) — the numbers ship as
  sensible defaults but *your* machine's normal is the real reference;
  prefer baseline-relative rules where offered.
- **`confirm_cycles`** — raise it if something legitimate keeps tripping a
  rule briefly (e.g. a nightly backup saturating IO).
- **`exempt`** — name patterns for entities that should be completely outside
  this monitor (compilers, encoders, read-only filesystems). Exempt entities
  neither alert nor retain samples, rollups, baselines, or graph history.
  Interpreter-hosted apps often
  report a generic process name (`MainThread`, `node`), so match on
  `exe_base` (the executable's basename) instead of `name` when targeting
  them — alerts likewise show the executable, e.g. `agent (MainThread)`.

For systematic threshold calibration on a live host, see the
[tuning procedure](tuning-procedure.md) (`tools/tuning/leaky.py` and
`stress_profile.sh`).

Baselines learn automatically (~24 h of data before they speak). If your
machine's "normal" genuinely changed — new job, big software upgrade —
`ftmon baseline reset <monitor>` starts the learning over; affected rules
go quiet while relearning rather than firing against the old normal.

FTMON keeps raw minute-level history for 48 h, 5-minute summaries for a
month, and hourly summaries for about a year, pruning automatically to stay
inside the 200 MB budget. Incident history is never pruned.

**Journal events** (the `events` monitor) watch the systemd journal live.
Not everything is stored — only entries at notice level and above, or ones
matching an event rule — so the database holds what matters, not the whole
journal (that's what `journalctl` is for). A matching entry opens an
*episode*: repeats within the cooldown just count up ("12x since open"),
and the episode closes itself after 30 quiet minutes without a popup. A
log-spamming app is automatically collapsed after 100 stored events/minute.

For applications that write only to non-standard log files, use Fluent Bit's
`tail` input and send its selected records to standard output. When Fluent Bit
runs as a systemd service, systemd records that output in journald, where
FTMON's existing `events` monitor can match it. This keeps file rotation,
multiline parsing, restart offsets, and backpressure in Fluent Bit instead of
duplicating a log shipper inside FTMON. See the
[Fluent Bit recipe](definitions.md#monitor-a-non-standard-log-file-with-fluent-bit)
for a minimal configuration and event rule.

**Services and sockets** (`service`, `net`) are watchlist-driven: they do
nothing until you name targets in their TOML —
`{ unit = "syncthing.service" }`, `{ process = "^ssh-agent$" }`,
`{ listen = "tcp:22" }`. A `during = "09:00-18:00"` field scopes a check to
working hours (a backup service *should* be dead at noon). Connection
totals are baselined, so `net` learns your normal before it warns.

## 5. The web UI

`ftmon web` serves a local page at `http://127.0.0.1:8420`: status
dashboard, incident browser with full explanations, metric charts, event
search, and monitor management (including approving AI drafts). Localhost
only, no external resources, works offline.

## 6. AI integration (MCP)

Register `ftmon mcp` with your AI client. For Claude Code:

```sh
claude mcp add ftmon -- ftmon mcp        # or: uv run ftmon mcp (from source)
```

The assistant gets tools for status, metric queries with automatic
resolution, top consumers, per-process history, events, incidents with a
full `explain_incident` story, monitor listing/validation — and exactly
two writes: acknowledging an incident, and *proposing* a monitor.
Proposals land in `drafts/` and do nothing until you approve them with
`ftmon monitor approve <name>` (or the web UI). The AI can never install
actions, run commands, or enable monitors by itself. It also gets packaged
resources for the monitor-definition reference, writing an external-check
executable, and registering that check safely, so authoring works on an
installed host without a repository checkout: `ftmon://docs/definitions`,
`ftmon://docs/check-authoring`, and `ftmon://docs/external-checks`. For external
checks,
`diagnose_monitor` reports not only load and trust but the last stored runtime
result (`plugin_state`, message, sample age) so an assistant can tell whether a
loaded monitor is actually producing. Checks cannot invoke `sudo` under the
shipped systemd hardening; the `ftmon://docs/external-checks` resource explains
the privileged exporter pattern for checks that genuinely need elevated reads.

`ftmon monitor enable <name>` / `disable <name>` flip a monitor's
`enabled` line in place — comments and formatting in your file survive.

### Historical trends

The **Trends** page explores declared growth profiles by monitor, entity, and
range. Value, signed rate, optional confidence, and optional projection appear
as separate synchronized panels because their units and meanings differ. Long
ranges use stored 5-minute or hourly rollups with min/max envelopes; missing
observations remain visible gaps. The complete selection stays in the URL for
bookmarking and links from dashboards or incidents open the same explorer.

The leak profile shows process RSS, MiB/hour growth, and growth consistency. It
does not forecast when memory will be “full”: host RAM, swap, cgroup limits, and
the OOM killer do not provide one honest process capacity. The disk profile can
project because total space and remaining bytes are known, but only while its
signed rate is positive and confidence passes the configured threshold. Flat,
shrinking, sparse, or irregular history explains why projection is unavailable
instead of displaying an enormous artificial number.

### Metrics explorer

The **Metrics** page is the lower-level companion to Trends. Its cascading
monitor, entity, and metric selectors list every series actually present in the
database, including historical series no longer in the current definition. Use
it to inspect one metric that has no curated profile, or follow an “Underlying
series” link from a Trend panel. Range and rollup value (`avg`, `min`, `max`, or
`last`) stay in the URL, so the exact diagnostic view can be bookmarked.
Metrics uses the same interactive time axis, zoom/cursor, min/max rollup
envelope, missing-data gaps, and incident markers as Trends. It remains
intentionally one series at a time and never invents rate, confidence, or
forecast meaning; when a definition declares such meaning, **Open Trend**
moves to that curated view.

When the selected series has a learned baseline, Metrics also shows its current
EWMA level and learning coverage. Rules still receive no `baseline(m)` value
until 240 five-minute updates have accumulated, but the page exposes the level
while it learns so you can see what “normal” is converging toward. The dashed
overlay contains only retained five-minute baseline updates: gaps stay gaps,
raw timestamps are not fabricated, and long ranges do not extend today's level
back across missing history. If older five-minute evidence has aged out, the
text summary says the visible baseline history is truncated.

### Baselines index

The read-only **Baselines** page lists every stored learned series with its
monitor, entity, metric, current level, update-count coverage, readiness, and
last update. Filters and bounded pagination keep the page useful even when
short-lived process identities have accumulated. Each row opens the matching
bookmarkable Metrics view for historical context.

Baseline reset remains an explicit maintenance command:

```sh
ftmon baseline reset <monitor> [entity]
```

Resetting discards the learned rows in that scope; rules return unknown while
the replacement baseline accumulates its first 240 updates.

## 7. Writing your own monitors

See the definitions reference (`docs/definitions.md`) — the complete TOML
schema, every formula function with examples, and a cookbook ("alert when
a log pattern appears", "alert when anything grows steadily"). The short
version: copy the nearest built-in, rename it, edit, `ftmon check`.

External checks let a separately installed Nagios plugin or your own small
script feed the same history, rules and Trends. The administrator registers an
exact argv in `checks.toml`; the monitor maps only the performance labels it is
prepared to store. Start with [External checks](external-checks.md), including
its privilege, credential and third-party licence guidance.

Curated recipes from `extra-monitors/` install with:

```sh
ftmon recipe install http-tls
```

The daemon picks up the new monitor and check alias within ~30 seconds — no
restart required (`ftmon monitor rescan` or `systemctl reload ftmon` skips
the wait). `ftmon paths` prints where definitions, drafts, and the check
registry live on this machine.

### Don't poke the live database

The FTMON database (`~/.local/share/ftmon/ftmon.db`) is written by the
daemon on every tick. An external `sqlite3` session (or any direct SQL
client, or an AI assistant offering to "just delete those rows") that
writes to it can hold the write lock longer than the daemon's
`busy_timeout`. The daemon survives that (it drops the tick's buffered
writes, counts `sqlite_lock_errors`, and records a self-event), but the
tick is still lost and contention can leave the dashboard looking stale
until the lock clears. For inspection, open the database read-only
(`sqlite3 "file:$HOME/.local/share/ftmon/ftmon.db?mode=ro"`) or stop the
daemon first; for any cleanup or bulk changes, always stop the daemon.

## 8. Notifications & quiet hours

Notifications are deliberately short; depth lives in `ftmon incident <id>`
(state, severity, monitor/group/entity, owning rule, opened/changed/cleared
times, clear reason, acknowledgment, notification count, occurrences,
flapping flag, and ordered history with JSON details) and the web UI. An audit
trail of every notification is kept at
`~/.local/state/ftmon/notifications.jsonl`. Desktop popups are enabled by the
desktop initialization profile and disabled by the server profile.

Desktop popups keep a bounded footprint in the notification tray: renotify and
recovery popups are transient (they show a banner but never pile up in the
tray), one incident updates a single tray entry across its whole open →
escalate → recover lifecycle, and only critical incidents use the
never-expiring `critical` popup urgency. A pile of stale tray entries is not
just clutter — a large backlog can crash GNOME Shell's calendar panel
(Launchpad #2138529), so the monitor refuses to be the thing that builds one.
On an older `notify-send` without these flags, popups simply behave as plain
persistent notifications. If popups are still noisier than you want, raise the
desktop channel's threshold in `config.toml`:

```toml
[notify.desktop]
min_severity = "warning"   # e.g. keep info-level recovery popups off entirely
```

Remote
ntfy, webhook, and SMTP delivery is configured with protected external secret
references; see `docs/install.md`. Delivery is independent per channel and
honestly at-least-once, so the one in-flight channel attempt may be duplicated
after a crash but another channel's success cannot conceal its failure. Secret
values are never valid directly in `config.toml` (SE-05).

On a server, `ftmon doctor` is the safe readiness check: it validates each
channel and credential reference but sends nothing. Delivery failures and
retry debt remain visible independently per channel, while the mandatory local
JSONL audit is the durable record. The installation guide explains protected
credential files, systemd credentials, and an offline loopback webhook smoke
test. This separation exists so an operator can diagnose configuration without
leaking a token or surprising recipients.

Quiet hours are set in `config.toml`:

```toml
[quiet_hours]
enabled = true
start = "22:00"   # local time; the window may cross midnight
end = "08:00"
```

During quiet hours, warning-and-below notifications are held and delivered
as **one digest** when the window ends; error and critical always come
through immediately. Incidents still open, escalate, and clear during quiet
hours — only the popups wait. After a crash FTMON may repeat at most one
notification — it will never silently lose one.

### Actions

A rule may name one executable in `~/.config/ftmon/actions/`. You create and
review that file yourself; FTMON deliberately never writes or chmods anything
there. An action runs only on the incident's initial open—not escalation,
renotify, downgrade, or recovery—and at most once per action every ten minutes.
It receives only a minimal PATH and the documented `FTMON_*` context, has no
shell or arguments, and is killed after 30 seconds. Its capped output, exit
status, timeout, or rate-limit suppression is retained in incident history.

## 9. Privacy

There is no telemetry. Operational listeners remain limited to the localhost
web page and MCP remains stdio-only. When you explicitly enable ntfy, webhook,
or SMTP, the rendered notification leaves the machine; raw incident attributes
and credential values do not. Review the destination's retention policy before
enabling it.
Process command lines are recorded (truncated) because they're usually
exactly what identifies a culprit; set `collect_cmdline = false` in
`config.toml` to keep only program names. Data files are private to your
user (0600).

### Public demonstration data

The live [demo.ftmon.org](https://demo.ftmon.org/) site is designed to explain
FTMON, not monitor its hosting server. Its persistent banner identifies a
deterministic synthetic scenario containing example health states, incidents,
gaps, and trends. It has no daemon, actions, notifications, credentials,
configuration editor, MCP endpoint, or visitor writes. Never use a real
operational database with `web --demo`; follow the separate deployment runbook
in `docs/install.md` when publishing or updating the site.

## 10. Troubleshooting

| Symptom | Look at |
| --- | --- |
| No data | Start `ftmon daemon`; check the service status. |
| Monitor failed after an edit | Run `ftmon check`; inspect `ftmon status`. |
| External check unavailable | Verify registry and executable with `doctor`. |
| Plugin metric is absent | Compare the mapped label and UOM with plugin output. |
| Too many notifications | Raise `confirm_cycles`, add an `exempt`, or ack. |
| FTMON over budget | Open the Self page or inspect the incident. |
| Database concerns | Run `ftmon doctor`; use its `--backup PATH` option. |

Never copy the live `ftmon.db` file directly: SQLite may have committed data
in its WAL file. `ftmon doctor --backup PATH` uses SQLite's snapshot API and
checks the resulting backup before reporting success.

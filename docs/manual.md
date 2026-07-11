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

## 2. Installation *(final packaging arrives with M6; works from source now)*

```sh
git clone <repo> && cd ftmon
uv sync
uv run ftmon init      # creates ~/.config/ftmon, installs built-in monitors
uv run ftmon check     # validates every monitor definition
```

Directories FTMON uses (Linux):

| Path | Purpose |
| --- | --- |
| `~/.config/ftmon/config.toml` | global settings (tick rate, quiet hours, privacy, web port) |
| `~/.config/ftmon/monitors/*.toml` | your monitor definitions |
| `~/.config/ftmon/monitors/drafts/` | AI-proposed definitions awaiting your approval |
| `~/.config/ftmon/actions/` | scripts rules may run (you create these by hand, on purpose) |
| `~/.local/share/ftmon/ftmon.db` | metric/event/incident history (SQLite) |
| `~/.local/state/ftmon/` | daemon log + notification audit trail (JSONL) |

Running as a service *(systemd unit ships with M6)*:
the daemon is `ftmon daemon`; everything else (CLI, web UI, MCP) reads the
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
ftmon incident 42       # full story of one incident        (soon)
ftmon top rss --range 3h  # what was eating memory          (soon)
ftmon doctor            # database health, backup           (M6)
```

Desktop notifications are live: an incident opens after its confirmation
cycles, re-notifies on the backing-off schedule, and sends one recovery
notice when it clears. Every notification is also appended to
`~/.local/state/ftmon/notifications.jsonl` (the audit trail).

Every listing command takes `--json` for scripting.

## 4. Tuning

Open the monitor's TOML file; every parameter has a comment saying what it
does. Change the value, run `ftmon check`, done — the daemon picks up
edits within 30 seconds, no restart. Three knobs cover most needs:

- **Thresholds** (`warn_pct`, `crit_mb_per_h`, ...) — the numbers ship as
  sensible defaults but *your* machine's normal is the real reference;
  prefer baseline-relative rules where offered.
- **`confirm_cycles`** — raise it if something legitimate keeps tripping a
  rule briefly (e.g. a nightly backup saturating IO).
- **`exempt`** — name patterns for legitimate heavy processes (compilers,
  encoders). Exempt entities are still recorded — only alerting stops —
  so you can still ask about them later.

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

**Services and sockets** (`service`, `net`) are watchlist-driven: they do
nothing until you name targets in their TOML —
`{ unit = "syncthing.service" }`, `{ process = "^ssh-agent$" }`,
`{ listen = "tcp:22" }`. A `during = "09:00-18:00"` field scopes a check to
working hours (a backup service *should* be dead at noon). Connection
totals are baselined, so `net` learns your normal before it warns.

## 5. The web UI *(arrives with M5)*

`ftmon web` serves a local page at `http://127.0.0.1:8420`: status
dashboard, incident browser with full explanations, metric charts, event
search, and monitor management (including approving AI drafts). Localhost
only, no external resources, works offline.

## 6. AI integration (MCP) *(arrives with M4)*

Register `ftmon mcp` with your AI client (Claude Code/Desktop snippet in
the install guide). The assistant can then answer "why was my machine slow
this morning?" from recorded data, explain any incident, and *propose* new
monitors — proposals land in `drafts/` and do nothing until you approve
them with `ftmon monitor approve <name>` or the web UI. The AI can never
install actions, run commands, or enable monitors by itself.

## 7. Writing your own monitors

See the definitions reference (`docs/definitions.md`) — the complete TOML
schema, every formula function with examples, and a cookbook ("alert when
a log pattern appears", "alert when anything grows steadily"). The short
version: copy the nearest built-in, rename it, edit, `ftmon check`.

## 8. Notifications & quiet hours

Notifications are desktop-native and deliberately short; depth lives in
`ftmon incident <id>` and the web UI. An audit trail of every notification
is kept at `~/.local/state/ftmon/notifications.jsonl`.

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

## 9. Privacy

Everything stays on your machine: no telemetry, no network listeners
except the localhost web page, MCP only over stdio to a client you run.
Process command lines are recorded (truncated) because they're usually
exactly what identifies a culprit; set `collect_cmdline = false` in
`config.toml` to keep only program names. Data files are private to your
user (0600).

## 10. Troubleshooting

| Symptom | Look at |
| --- | --- |
| "no data - is the daemon running?" | `ftmon daemon` running? `systemctl --user status ftmon` (M6) |
| a monitor stopped working after an edit | `ftmon check` — the daemon keeps the last good version and reports a config error in `ftmon status` |
| too many notifications from one rule | raise `confirm_cycles`, add an `exempt`, or ack the incident |
| FTMON itself flagged over budget | the `self` monitor fired — see the Self page (M5) / `ftmon incident` for which resource |
| database concerns | `ftmon doctor`, backups via `ftmon doctor --backup` (M6) |

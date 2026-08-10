# macOS platform spike findings

Spike-only validation per `docs/PLAN-platform-foundation.md` -> "Spike checklist
per platform -> macOS". Ran on real Intel macOS hardware as a non-root
interactive user. The account is UID 501 and is a member of the local `admin`
group, but every probe was unelevated: no `sudo`, authorization dialog, or
root shell was used.

These are raw findings for the eventual `platform-foundation` PR's SPEC/DESIGN
diff. No `SPEC.md` or `DESIGN.md` changes were made on this branch.

Environment: macOS 12.7.6 (21H1320), x86_64.

## 1. `log stream --style ndjson` and the DM-15 resume contract

Scripts: [`log_stream_spike.py`](log_stream_spike.py) and
[`log_emitter.c`](log_emitter.c).

**Streaming a non-Apple subsystem works unelevated.** A tiny unsigned C
program emitted through `os_log_create("org.ftmon.spike", "validation")`.
`/usr/bin/log stream --style ndjson --level debug --predicate
'subsystem == "org.ftmon.spike"'`, launched as an ordinary subprocess,
captured it without `sudo`, a TCC dialog, or any other setup. SIGINT produced
a clean exit. This validates FTMON's own/non-Apple subsystem on this account;
it does not prove that every private/sensitive subsystem is readable, and the
account's local-admin membership should be kept in mind.

**`ndjson` is not a stream of event objects only.** On this Monterey host,
stdout begins with a human-readable `Filtering the log data using ...` line,
then event JSON, blank lines, and finally `{"count":...,"finished":1}`.
Consumers must parse line-by-line, tolerate non-JSON lines, and select
`eventType == "logEvent"` rather than passing every line directly to a strict
event decoder.

The captured custom event had these fields:

`activityIdentifier`, `backtrace`, `bootUUID`, `category`, `eventMessage`,
`eventType`, `formatString`, `machTimestamp`, `messageType`,
`parentActivityIdentifier`, `processID`, `processImagePath`,
`processImageUUID`, `senderImagePath`, `senderImageUUID`,
`senderProgramCounter`, `source`, `subsystem`, `threadID`, `timestamp`,
`timezoneName`, and `traceID`.

Fields are not all stable/non-empty (`source` was null; some UUID/timezone
fields were empty in `logger` output), so an adapter should treat the
document as a sparse platform record. The useful normalized fields are
timestamp, message, subsystem, category, process path/PID, message type, and
the boot/mach identity fields.

**There is no exact persistent cursor. Use overlapping wall-time replay plus
deduplication.** The verified behavior is:

- `log stream` tails future events only and exposes no bookmark token.
- `log show --start` can replay the persistent store, and the start boundary
  is inclusive.
- Monterey rejects the stream's fractional timestamp
  (`2026-...SS.ffffff+1000`) as `--start`; accepted inputs are second
  resolution (`Y-M-D H:m:s+zzzz`) or Unix time.
- The same event's stream timestamp and later `log show` timestamp differed
  slightly in repeated probes (about 1 ms in the committed script's latest
  run, and about 16 ms in an earlier run). An exact timestamp equality check
  is therefore unsafe.
- `log show` returns a terminal count object, just like `log stream`.

The concrete DM-15 design should persist a **wall-clock high-water mark plus
a bounded recent-event identity set**, not pretend macOS has an opaque
cursor. On restart:

1. Run `log show --start` from a deliberate overlap before the persisted
   high-water mark (at least the previous whole second; a small multi-second
   overlap is safer for store-ingestion delay).
2. Deduplicate replayed records against persisted recent identities. A
   practical identity is `(bootUUID, machTimestamp, traceID, processID,
   senderProgramCounter)` with a hash of the normalized payload as a fallback;
   keep identities only for the overlap horizon.
3. Drain historical output to its terminal count object, then start
   `log stream`; overlap/deduplicate that handoff too so events arriving
   between commands are not silently lost.
4. Advance/persist the watermark and identity set only after the corresponding
   events have been durably accepted.

This gives at-least-once collection with bounded duplicate suppression. It
cannot guarantee Windows/journald-style exact resume. If the saved watermark
predates unified-log retention, `log show` does not provide a stale-cursor
error; the adapter needs an explicit retention-gap check and self-event if
DM-15 requires the gap to be observable.

## 2. `osascript display notification`

Script: [`notification_spike.py`](notification_spike.py).

**The command works with zero FTMON packaging setup, but the identity is
Script Editor.** `/usr/bin/osascript -e 'display notification ...'` returned
exit 0 with empty stdout/stderr from a plain shell. No app bundle, FTMON code
signature, or registration was involved. Notification Center preferences
identify the sender as `com.apple.ScriptEditor2`, path
`/System/Applications/Utilities/Script Editor.app`; it is not an FTMON
notification identity.

This account already had the Script Editor entry in Notification Center
preferences before the spike, so this machine cannot honestly validate the
first-ever permission dialog without destructively resetting user state.
No prompt appeared during the probe. A fresh-account/fresh-OS follow-up is
needed to record the exact first-use dialog and whether the current macOS
target behaves differently from Monterey.

**There is no clean supported PL-03 preflight through `osascript`.** Exit 0
means the notification request was submitted; it does not prove a banner was
shown. The only discovered state is the private
`~/Library/Preferences/com.apple.ncprefs.plist` entry with undocumented bit
flags. Depending on those private flags would be brittle, does not cleanly
cover Focus/Do Not Disturb, and still would not provide FTMON-specific status
because the sender is Script Editor. The graceful behavior should therefore
be best-effort submission: treat an `osascript` process error/timeout as an
adapter failure, but treat exit 0 as accepted even if Notification Center
suppresses it. Documentation should tell users to inspect Script Editor in
System Preferences -> Notifications. A real bundled notification helper
using `UNUserNotificationCenter` would be required for a supported,
FTMON-specific authorization-status API.

## 3. LaunchAgent and PM-11 reload

Scripts: [`launchagent_spike.sh`](launchagent_spike.sh) and
[`reload_target.py`](reload_target.py).

A generated plist with `Label`, absolute `ProgramArguments`, `RunAtLoad`,
`KeepAlive`, `StandardOutPath`, and `StandardErrorPath` passed `plutil -lint`
and bootstrapped successfully with:

```sh
launchctl bootstrap "gui/$(id -u)" /path/to/org.ftmon.spike.reload.plist
```

No elevation or plist installation into a system directory was needed.
`launchctl print gui/501/org.ftmon.spike.reload` showed the Python process
running with launchd's minimal default PATH
(`/usr/bin:/bin:/usr/sbin:/sbin`). The real wrapper must use absolute paths
and explicitly set required FTMON environment/path values. The spike used a
temporary plist; a real per-user installation belongs in
`~/Library/LaunchAgents/`.

**SIGHUP works normally under launchd.** Sending `kill -HUP` to the PID
reported by `launchctl print` reached the installed handler. The process
recorded the reload and retained the same PID. launchd did not intercept,
replace, or restart it. This directly matches the existing POSIX PM-11
contract, so macOS needs no Windows-style named-event substitute.

By comparison, `launchctl kickstart -k gui/501/<label>` changed the PID. It is
a forced restart, not a reload, and should not be presented as PM-11.
`launchctl bootout` removed the temporary service successfully after the
probe.

## 4. Running the actual package on macOS

Ran the branch's actual editable package with isolated `FTMON_CONFIG_DIR`,
`FTMON_DATA_DIR`, `FTMON_STATE_DIR`, and `FTMON_RUNTIME_DIR` paths under
`/tmp`; no existing user FTMON state was touched.

**There is a packaging/bootstrap failure on Intel macOS before FTMON runs.**
This host had neither `uv` nor a supported system Python (Apple Python is
3.9.6), so `uv 0.11.32` was bootstrapped into `/tmp` and uv supplied CPython
3.13.14. A plain `uv sync` then failed:

- the lock resolves `cryptography==49.0.0`;
- that release has macOS arm64 wheels in `uv.lock`, but no macOS x86_64
  wheel, so Intel builds from source;
- the source build requires Rust plus OpenSSL headers/libraries and initially
  failed because OpenSSL/pkg-config were absent;
- Homebrew on macOS 12 is Tier 3 and builds current OpenSSL from source. Two
  attempts reached OpenSSL's formula test suite after lengthy successful
  compiles/installs, but were stopped rather than spend another long cycle;
  no throwaway source patch or dependency change was kept.

To separate that dependency-distribution issue from FTMON's runtime code, the
environment was completed with `uv sync --python 3.13 --no-install-package
cryptography`. This installed the actual branch package and all other locked
dependencies. The following results are therefore valid for FTMON's CLI/web/
daemon paths, but do **not** turn the full `uv sync` result into a pass.

- `ftmon init --profile desktop`: **passes**. Wrote config and check registry
  and installed all 8 builtins. This exercises `atomic_write`; the guarded
  `os.fchmod` path is present and works on macOS. The Windows blocker does not
  reproduce.
- `ftmon daemon`: **passes**. Created the database, acquired its single-
  instance lock, ran, accepted SIGHUP without exiting, and then stopped
  cleanly on SIGINT with exit 0. This confirms local `fcntl` import/flock and
  `signal.SIGHUP` work on Darwin; the Windows import/signal guards do not
  block macOS.
- The load-time platform filter works: `disk`, `events`, `hog`, `leak`,
  `load`, `net`, and `service` all reported that they declare only `linux`
  and were skipped; the daemon started with the platform-neutral `self`
  monitor only. There was no attempt to instantiate Linux journald/systemd
  sources.
- `ftmon check`: **passes**, exit 0 both before and after the daemon run.
- `ftmon doctor`: before the first daemon run, correctly returned exit 1 with
  `database does not exist; start the daemon once`. Afterward it returned exit
  0, `quick_check: ok`, and clean table/orphan counts.
- `ftmon web --port 18420`: **passes**. Bound to
  `http://127.0.0.1:18420`, served the dashboard with HTTP 200 (5,750 bytes),
  and stopped cleanly on SIGINT with exit 0.
- Expected current-foundation limitation: the daemon warned
  `[notify.desktop] desktop_unavailable; channel disabled`, and `doctor`
  reported the desktop adapter as unavailable. That is graceful degradation,
  not a crash, but it confirms the macOS notification adapter is not yet
  implemented on this branch.

Net: the three Windows-specific blockers named in the request do not
reproduce on macOS, exactly as expected. The new breakage is distribution
rather than platform-guard logic: current locked dependencies do not install
zero-setup on Intel macOS 12.

## Summary for the platform-foundation PR

| macOS row | Verdict |
|---|---|
| Event source (`log stream`) | Custom non-Apple subsystem confirmed unelevated; parser must tolerate non-JSON/status lines and sparse records |
| Event cursor (DM-15) | No exact cursor: persist wall-time watermark + bounded identities, replay with overlap, deduplicate, and detect retention gaps explicitly |
| Notification (`osascript`) | Zero-bundle submission works but is attributed to Script Editor; no supported global-disable preflight; first-use prompt not reproducible on this preconfigured account |
| Service wrapper (LaunchAgent) | User-domain `bootstrap`/`bootout` works without elevation; use absolute paths and explicit environment |
| PM-11 reload | SIGHUP works unchanged and preserves PID; `kickstart -k` is a restart |
| Actual package | Runtime paths pass with `cryptography` omitted; full `uv sync` is blocked by the lack of a `cryptography 49` Intel macOS wheel/native OpenSSL prerequisites |

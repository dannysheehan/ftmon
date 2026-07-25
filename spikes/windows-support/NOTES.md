# Windows platform spike findings

Spike-only validation per `PLAN-platform-foundation.md` -> "Spike checklist
per platform -> Windows". Ran on real Windows 11 hardware, non-admin user
(UAC split token: member of `BUILTIN\Administrators` but "deny only" -- i.e.
a normal unelevated interactive session, not run-as-admin).

These are raw findings for the eventual `platform-foundation` PR's SPEC/DESIGN
diff (new `PL-*` IDs, matrix rows flipped from "planned" to real). No
SPEC.md/DESIGN.md changes made on this branch, per instructions.

Environment: `pywin32==312`, `windows-toasts==1.3.1`, Python 3.12.3, installed
into a throwaway `.venv-spike/` (gitignored, not `uv`-managed -- this is
spike-only, not a dependency decision for `pyproject.toml`).

## 1. `win32evtlog.EvtSubscribe` + `EvtBookmark` round-trip (DM-15)

Script: [`evtlog_spike.py`](evtlog_spike.py), [`evtlog_stale_bookmark_spike.py`](evtlog_stale_bookmark_spike.py).

**The happy path matches the journald-cursor mental model.** Subscribe with
`EvtSubscribeToFutureEvents`, bookmark an observed event, render the bookmark
to an XML string with `EvtRender(bookmark, EvtRenderBookmark)`, persist that
string (this is the DM-15 cursor value for Windows), later reconstruct a
bookmark handle from the persisted XML via `EvtCreateBookmark(xml)`, and
resume with `EvtSubscribeStartAfterBookmark`. Verified end to end: the
resumed subscription delivered an event written *after* the bookmark and did
**not** re-deliver the bookmarked event itself. Bookmark XML is small and
inert (`<BookmarkList><Bookmark Channel='...' RecordId='...' .../></BookmarkList>`,
~100 bytes) -- cheap to store as an opaque string column, same shape as the
journald cursor.

**Gotcha (real bug in my first draft, not a Windows quirk):** the `Event`
handle passed into an `EvtSubscribe` callback is only valid for the duration
of that callback invocation. Stashing it (e.g. in a dict keyed by rendered
XML) and calling `EvtUpdateBookmark` on it *after* the callback returns
raises `pywintypes.error(6, 'EvtUpdateBookmark', 'The handle is invalid.')`.
The bookmark (and any XML render) must happen synchronously inside the
callback. This is a hard constraint on how the real `EventSource`
implementation's callback is structured -- it cannot defer bookmarking to a
later point in the tick.

**Stale/invalid bookmark behavior differs from journald's explicit
"cursor not found" error -- this is the biggest DM-15 design gap:**

- A bookmark XML string that is syntactically valid but references a
  `RecordId` that no longer exists in the channel (log rolled over, record
  aged out) does **not** raise anywhere -- not at `EvtCreateBookmark`, not at
  `EvtSubscribe`, not via the error-action callback (`EvtSubscribeActionError`
  never fires). Instead, the subscription **silently falls through to
  "deliver future events from now"** behavior: no backlog, no error, just a
  live tail starting at the moment of the call. Confirmed by writing a fresh
  event after subscribing on the stale bookmark and observing it delivered.
  Practically this means a gap (events written between "last real cursor
  position" and "now") is silently dropped with no signal to record a
  self-event about it, unlike journald where an invalid cursor is a loud,
  catchable error the caller can turn into an explicit "resyncing, gap
  possible" self-event.
- A bookmark XML string that is not well-formed XML at all *does* raise
  cleanly at `EvtCreateBookmark`: `pywintypes.error(15008, 'EvtCreateBookmark',
  'The specified XML text was not well-formed...')`. So "corrupted stored
  cursor" is catchable; "stale-but-well-formed stored cursor" is not.

Design implication to carry into the PR: the Windows `EventSource` can't rely
on an exception to detect the "resuming from an old cursor produced a gap"
case the way the Linux implementation presumably can with journald's cursor
errors. If that gap needs to be observable (self-event, DM-15 parity), it
likely needs an explicit RecordId/age sanity check against
`EvtGetLogInfo`/`EvtQuery` before trusting `EvtSubscribeStartAfterBookmark`
went where we thought it did -- this needs a real design decision, not just
an implementation detail.

## 2. `windows-toasts` (notification adapter)

Script: [`toast_spike.py`](toast_spike.py).

- **Maintenance**: actively maintained. Latest release 1.3.1 (2025-05-06);
  repo (`DatGuy1/Windows-Toasts`) last pushed 2025-11-24 per the GitHub API
  (checked 2026-07-25); not archived; "Development Status :: 4 - Beta";
  20 open issues but low signal of the project being stuck (recent closes,
  active PRs). Reasonable to depend on.
- **Permission model / no-shortcut/no-AppID behavior**: works with zero
  setup. `WindowsToaster("arbitrary unregistered name")` and
  `InteractableWindowsToaster(...)` both called `show_toast()` successfully
  (returned `None`, no exception) with no Start-menu shortcut and no
  registered AUMID -- this was run from a plain console `python.exe`
  invocation, exactly the shape a daemon process would have. **Visually
  confirmed by the user**: both toasts actually rendered on screen, and the
  toast's displayed sender identity was the literal `applicationText` string
  passed to the constructor (e.g. `"ftmon spike (unregistered)"`), not a
  generic "Python" fallback. This is a materially better outcome than
  expected going in -- no installer-time AUMID registration step is a hard
  requirement for v1's toast adapter to work.
- Not yet tested in this spike: behavior when Windows notifications are
  globally disabled (Focus Assist / Settings > Notifications off) -- worth a
  follow-up since PL-03's "degrade gracefully" requirement needs to know
  whether that surfaces as an exception or another silent no-op.

## 3. Task Scheduler logon trigger (service wrapper) + PM-11 reload-equivalent

Script: [`task_scheduler_spike.py`](task_scheduler_spike.py), [`reload_signal_spike.py`](reload_signal_spike.py).

- **Read access works, write access does not, for this standard user, on
  this machine.** `schtasks /query` and the COM `Schedule.Service.Connect()`
  + `GetFolder('\')` + `GetTasks()` all succeed. But creating a task -- via
  both `schtasks /create ... /sc onlogon /rl limited` (CLI) and
  `IFolder.RegisterTaskDefinition` (COM), for a plain per-user logon trigger
  requesting only the *limited* (non-elevated) run level -- fails with
  `ERROR: Access is denied.` / `HRESULT 0x80070005` (E_ACCESSDENIED) in both
  paths. This was consistent across repeated attempts and both APIs, so it
  is not a flake.
- **This may be a property of this specific machine/policy, not Windows in
  general** -- standard users creating their own per-user scheduled tasks is
  normally allowed out of the box. Only `Windows Defender` showed up as an AV
  product (no third-party EDR), so if this is policy-driven it's either a
  local/group policy or an MDM-pushed restriction rather than obviously an
  EDR product's doing. **Needs re-verification on a second, unmanaged Windows
  install before being treated as a general finding** -- but the install docs
  and the service-wrapper implementation both need to handle this case
  regardless of root cause, because "standard user, can't create scheduled
  tasks" is a realistic real-world condition (locked-down corporate/managed
  endpoints are exactly the kind of machine a sysadmin would run FTMON on).
- **Working fallback with zero special permissions**: the per-user Startup
  folder (`shell:startup`, i.e.
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) is freely
  writable by a standard user -- confirmed by creating and removing a file
  there. A `.lnk` shortcut dropped in that folder is Windows' other
  well-known per-user "run at logon" mechanism, with no Task Scheduler
  privilege model involved at all. Worth designing the Windows service
  wrapper to *try* Task Scheduler first (it has retry-on-failure and
  richer lifecycle controls that the Startup folder lacks) and fall back to
  a Startup-folder shortcut with a clear message, rather than assuming Task
  Scheduler access is a given.
- **PM-11 reload-equivalent**: Task Scheduler itself has no operation that
  reaches into an already-running task's process the way `SIGHUP` does --
  its only verbs are Run/End/Enable/Disable/trigger-based restart, and "Run"
  would just collide with PM-02's single-instance lock (the new instance
  exits immediately, doesn't signal the existing one). So the reload path
  (`ftmon monitor rescan` / CL-07) needs a Windows-native cross-process
  primitive independent of the service wrapper. Spiked a **named Win32 Event
  object** (`Local\ftmon-...`) as the candidate: the daemon creates it once
  at startup and polls it with `WaitForSingleObject(handle, 0)` every tick
  (zero-timeout, no blocking, no I/O -- matches PM-11's "handler only
  records a flag" constraint); a second process opens the same name and
  calls `SetEvent`. Verified end to end across two real OS processes: the
  daemon-role process observed the signal within one tick of it being sent
  and printed the point at which it would perform the PM-04-shaped refresh.
  This looks like a solid, low-risk substitute for `SIGHUP` -- no admin
  rights, no filesystem coordination, auto non-signaled reset
  (`bManualReset=False` in `CreateEvent`) so it can't double-fire.

## Summary for the platform-foundation PR

| Windows row (SPEC SS4.1) | Verdict |
|---|---|
| Event source (`EvtSubscribe`) | Works as designed for the happy path; stale-bookmark-is-silent gap needs an explicit design decision (see SS1) |
| Event cursor (`EvtBookmark` XML) | Confirmed viable as the DM-15 cursor value; bookmark-inside-callback is a hard implementation constraint |
| Notification (`windows-toasts`) | Confirmed viable, actively maintained, zero-setup -- better than expected |
| Service wrapper (Task Scheduler logon) | Creation blocked by access denial on this machine for a standard user; needs re-test on a clean install and a documented Startup-folder fallback either way |
| PM-11 reload equivalent | Not provided by Task Scheduler; named Win32 Event object spiked and confirmed working as the substitute |

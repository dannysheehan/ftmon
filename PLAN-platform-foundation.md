# Plan: platform-foundation (Windows/macOS groundwork)

Work package for the branch `feature/platform-foundation`, which `main` will
gain before `feature/macos-support` and `feature/windows-support` do real
implementation work. Scope is deliberately narrow: make the existing seams
(PL-01) actually load-bearing, not add platform behavior.

## What already exists (verified in code, not assumed)

SPEC §4.1 and PL-01..PL-05 already specify the target design in detail —
there is no spec invention needed for this branch:

- `paths.py` already resolves all directories through `platformdirs`
  (FS-01) — generic across OSes today, nothing Linux-specific in it.
- `definitions/schema.py` (`PLATFORMS = frozenset({"linux","windows","darwin"})`)
  and `definitions/loader.py:309-327` already validate a monitor's
  `platforms = [...]` declaration and reject anything outside that set.
- `pyproject.toml` has no platform-conditional dependencies yet (`psutil`,
  `platformdirs` are cross-platform already).

## The actual gap (why this branch needs to exist)

1. **No runtime platform filter.** `loader.py` validates the *shape* of
   `platforms` but nothing checks it against the running OS — a monitor
   declaring `platforms = ["windows"]` still loads and runs on Linux today.
   Needs a filter at load time (skip + reason, same pattern as other
   `config_error`/skip states — PM-04) comparing `platforms` against
   `sys.platform`/`platform.system()`.
2. **No seam dispatch.** `daemon.py:672-674` hardcodes
   `from ftmon.sources.journald import JournaldEventSource` directly, and
   `daemon.py:105` hardcodes `ProcessSampler` (which is fine, it's
   `psutil`-backed and already cross-platform). There is currently no
   factory/dispatch point that could return a different `EventSource` or
   notification adapter per platform — that's the actual missing piece of
   PL-01, not a new abstraction (the `Sampler`/`EventSource` `Protocol`s in
   `sources/base.py` are already platform-neutral).
3. **No CI matrix.** `ci.yml` runs on one OS today; nothing exercises
   `platformdirs`/path resolution on Windows or macOS runners even at the
   smoke-test level.
4. **`docs/install.md` is Linux-only** — no placeholder sections to fill in
   once macOS/Windows implementations land.

## Foundation branch deliverables (small, mechanical, no new product behavior)

- [x] Platform-filter at load time: unsupported-platform monitors are
      skipped with a clear reason (mirrors existing `config_error`
      reporting path), covered by a traceability test citing PL-01/PL-02.
      Landed: `daemon.py::_load_definitions` now checks `mdef.platforms`
      against `paths.current_platform()` before loading either a sampler or
      event monitor; test `test_daemon_skips_monitor_not_declared_for_
      running_platform_pl_01` in `tests/unit/test_m10_release.py`.
- [x] One dispatch point (factory function) for `EventSource` and the
      notification adapter, keyed off `platform.system()`, with the current
      Linux implementations as the only registered case — this is the seam
      the two platform branches plug into, not a redesign.
      Landed: `ftmon.sources.event_source_for_platform()` and
      `ftmon.notify.desktop_notifier_for_platform()`, both returning `None`
      on unregistered platforms (already-handled code path on both call
      sites, not new fallback logic). `daemon.py` no longer imports
      `JournaldEventSource`/`DesktopNotifier` directly.
- [x] The three confirmed Windows-spike blockers, fixed at the seam:
      `paths.py::atomic_write`'s `os.fchmod` is now `hasattr`-guarded
      (POSIX-only call); `daemon.py`'s module-level `import fcntl` is gone,
      replaced by `paths.try_lock_exclusive()` (POSIX `fcntl.flock` /
      Windows `msvcrt.locking`, both raising/returning the same
      True/False contract so the call site stays platform-agnostic); and
      `daemon.py::run()`'s `signal.SIGHUP` registration (no Windows
      equivalent) is now `hasattr`-guarded — found while fixing the fcntl
      crash, not in the original spike notes, since the daemon never got
      far enough to hit it there. The real Windows reload primitive (named
      Win32 Event, spiked and confirmed on `feature/windows-support`) still
      needs to be wired up on that branch; this just stops the crash.
      All still gated behind `test_platform_conditionals_only_behind_four_
      seams_pl_01`'s source-tree scan (`paths.py` is an allowed file).
- [ ] CI: add `windows-latest`/`macos-latest` jobs that at minimum run
      `uv sync` + import smoke (`paths.py`, `platformdirs` resolution) —
      expected to fail loudly on anything Linux-specific we missed above.
- [ ] `docs/install.md`: add empty "Windows" / "macOS" sections marked
      not-yet-supported, linking forward to the tracking issues.
- [ ] No SPEC.md/DESIGN.md content changes in this branch beyond what's
      needed to describe the dispatch mechanism itself (if anything) —
      real platform-behavior claims (event cursor semantics, toast
      permissions, launchd quirks, etc.) stay out of SPEC until the spike
      findings from the platform branches validate them. Bumping the SPEC
      `Status:` header + §21 changelog entry (TS-19) only happens with that
      real content, not preemptively.

## What the platform branches inherit once this merges

`feature/macos-support` and `feature/windows-support` (currently branched
from this branch's tip, will need rebasing once this lands on `main`) each
get: a real seam to implement against, a CI job that will catch
Linux-specific assumptions immediately, and a `platforms` filter that makes
partially-implemented definitions safe to ship without affecting Linux
users.

## Spike checklist per platform (for the sessions running on real hardware)

Validate before writing any SPEC prose — these are the concrete choices
§4.1 already names, unverified against real installs:

**Windows**
- `pywin32` (`win32evtlog.EvtSubscribe`) subscription + `EvtBookmark` XML
  round-trip for the DM-15 cursor contract.
- `windows-toasts` package: current maintenance state, permission model,
  behavior when the app has no Start-menu shortcut/AppID.
- Task Scheduler logon-trigger creation (via `schtasks` or `pywin32`) for
  the service-wrapper seam, and what "reload" (PM-11 equivalent) looks like
  without SIGHUP.

**macOS**
- `log stream --style ndjson` subprocess: field shape, whether it needs
  `sudo`/TCC prompts for non-Apple subsystems, cursor/resume behavior
  (DM-15) since unified log has no persistent bookmark like `EvtBookmark`.
- `osascript display notification`: permission prompts, Notification
  Center behavior when the process has no bundle identity.
- `launchd` LaunchAgent plist authoring + reload equivalent to PM-11/SIGHUP.

Findings from both go back into the `platform-foundation` PR's SPEC/DESIGN
diff (new `PL-*` IDs, matrix rows flipped from "planned" to real), not
directly into the platform branches.

macOS spike completed on `feature/macos-support` (`83fa5d7`) and transferred
into SPEC v0.30 / DESIGN v0.15 on this branch:

- custom non-Apple unified-log streaming works unelevated; DM-15 uses
  overlapping wall-time replay plus bounded identities because stream/store
  timestamps are not an exact bookmark;
- zero-bundle `osascript` delivery works under Script Editor's identity, with
  no supported global-disable preflight;
- a user LaunchAgent bootstraps unelevated and passes SIGHUP through unchanged;
  `launchctl kickstart -k` is a restart;
- the actual package's POSIX guards and platform filter work on Darwin, but
  full locked installation on Intel macOS 12 is blocked by the current
  `cryptography` wheel/native OpenSSL build path.

No new `PL-*` requirement ID was added for spike-only behavior: TS-18 forbids
adding a testable requirement without its implementation tests. The validated
targets instead refine existing PL-01/PL-03, DM-15, NO-02, and PM-11 contracts;
platform-specific implementation branches can add tested IDs when they ship.

## Windows implementation (`feature/windows-support`)

Went past spike into real implementation (`5d90f19`..`05781f8`, merged with
`feature/platform-foundation` at `5e15ff1`): `WindowsEventSource`
(`sources/win_evtlog.py`, EvtSubscribe + per-channel EvtBookmark cursor),
`ToastNotifier` (`notify/toast.py`), a named-Win32-Event PM-11 reload
primitive, `windesktop`/`winserver` init profiles with a real Windows
monitor-definition tree (dead-rule removal validated against live data, not
just "doesn't crash"), and — reversing the earlier "ship v1 fail-closed,
defer ACL design" call — a real Windows ACL/DACL implementation of EC-01/
SE-07's ownership/writability trust check in `checks/trust.py`, replacing the
POSIX-only `os.geteuid()`/`st_mode` check that crashed outright on Windows.

**Security review of the EC-01 ACL trust reversal (2026-07-26):** run per
the condition attached to reversing the fail-closed decision — a trust/
authorization boundary change gets a dedicated adversarial pass, not just a
single read-through. Process: one sub-agent pass to identify candidate
vulnerabilities against `checks/trust.py`'s new Windows ACL code
(`_win_grants_beyond_owner`, `writable_beyond_owner`, `accessible_beyond_owner`,
`trusted_owner`) and its callers (`checks/registry.py`, `checks/runner.py`),
then independent verification sub-agents scoring each candidate's real-world
exploitability. Two candidates were raised and both were ruled out on
verification:

- NULL-DACL handling in `_win_grants_beyond_owner` treating `None` as "no
  grants" (pywin32 collapses "DACL absent" and "DACL present but NULL" to
  the same return) — ruled out (3/10): reaching that state requires
  `WRITE_DAC` on the file or write access to its parent directory, both of
  which the surrounding checks (`writable_beyond_owner`'s own ACE walk,
  `registry.py`'s parent-directory validation) already independently catch
  first.
- Missing `MapGenericMask` before filtering ACE masks, theoretically
  letting a raw unmapped `GENERIC_ALL`/`GENERIC_WRITE` ACE evade the write
  check — ruled out (2/10): real DACL-authoring tools (`icacls`, PowerShell
  `Set-Acl`, the Windows Security UI) always persist OS-mapped specific
  rights, never raw generic bits: the precondition doesn't occur in
  practice, and even if it did, Windows' real access-check wouldn't grant
  the claimed access either (the reviewer's exploit mechanics were
  backwards).

**Verdict: clear to ship.** No findings met the exploitability bar. Full
transcript lives in this conversation's history, not duplicated here —
this entry is the pointer for whoever cuts the next release to confirm the
condition was met, per DO-09 (review artifacts are maintainer-facing
records, not user documentation, and don't need their own `docs/REVIEW-N.md`
for a scoped single-module pass — that convention is for whole-repository
milestone audits).

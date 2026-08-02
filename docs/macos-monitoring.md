# macOS monitoring rationale

FTMON's macOS profile is intentionally not a renamed Linux profile. Darwin
contains a FreeBSD-derived BSD layer for processes, sockets and POSIX APIs, but
also uses Mach memory management, APFS volume groups, launchd, I/O Kit and the
Apple unified logging system. A signal is enabled by default only when it is
both available without privileged packaging and operationally actionable.

Apple background:

- [Darwin kernel architecture](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/KernelProgramming/Architecture/Architecture.html)
- [Activity Monitor process information](https://support.apple.com/en-asia/guide/activity-monitor/-actmntr1001/mac)
- [Activity Monitor memory pressure](https://support.apple.com/en-gb/guide/activity-monitor/actmntr1004/10.14/mac/15.0)
- [Console diagnostic reports](https://support.apple.com/en-ae/guide/console/cnsl664be99a/mac)
- [Unified-log levels](https://developer.apple.com/documentation/os/oslogtype)
- [APFS snapshots](https://support.apple.com/en-euro/guide/disk-utility/dskuf82354dc/mac)
- [launchd jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

Unified-log collection uses a bounded 10,000-record queue. During a source
storm FTMON drops the oldest queued records, increments `events_dropped`, and
records one `event_overflow` episode plus its final dropped count when the
queue recovers. Replay identities are bounded separately, so a storm cannot
grow deduplication memory for the daemon's lifetime. Coalesced raw identities
share one bounded pending window; only a drained run transfers those identities
to the durable replay checkpoint, so an overflow-dropped run cannot claim it
was accepted.

Before queue admission, consecutive records with the same source, provider,
event class, severity, and message are stored as one aggregate with occurrence
count and first/last source timestamps. The checkpoint still advances through
the final represented record. Aggregation never crosses an intervening event,
which preserves unified-log replay order. The Events dashboard rate counts all
raw arrivals, including these repeats, so a compacted storm does not look idle.

## Shipped profile

### Process CPU (`hog`)

Sustained per-process CPU is portable across Darwin's BSD process model and is
one of Activity Monitor's primary troubleshooting signals. FTMON keeps the
five- and fifteen-minute windows so short user-initiated bursts do not alert.
The values are percentages of one logical CPU, matching psutil's process
contract; a multithreaded process can therefore exceed 100%.

### Process RSS growth (`leak`)

RSS and stable `(pid, create_time)` identity are available on macOS. The rule
requires window coverage, net growth and a sustained slope, which distinguishes
a leak candidate from launch-time allocation or ordinary cache oscillation.
Per-process I/O is optional because some macOS psutil builds do not expose
`Process.io_counters`.

The default thresholds and nine-cycle confirmation match the calibrated
desktop persistence gate. Finder, browser/WebKit helpers, editors and
`photoanalysisd` are excluded because overnight testing showed their
cache/process churn repeatedly recovered without operator action. Generic
`node` and other service runtimes remain monitored: exempting an interpreter
name would hide genuine application leaks.

### Writable volume capacity (`disk`)

Only writable, visible volumes are capacity-alert targets. The macOS system
volume is sealed read-only, while its writable data volume is mounted at
`/System/Volumes/Data`; that data volume remains included even though it is
normally marked `nobrowse`. Other read-only or `nobrowse` mounts—application
disk images, snapshots and helper volumes—are excluded.

Space thresholds and filling-rate projection remain useful. Inode thresholds
are removed: APFS allocation does not present the fixed-inode exhaustion model
that makes those rules actionable on common Linux filesystems.

The filling rule also requires 90% monotonicity, at least 70% usage and nine
confirmation cycles. This avoids reporting ordinary background writes to a
mostly empty APFS data volume as an urgent capacity problem.

### System memory (`load`)

The current portable fallback alerts only when available memory remains below
five percent for five samples. It deliberately contains no Linux PSI rule.
macOS memory pressure combines free memory, swap rate, wired memory and file
cache, so a future Darwin system sampler should replace this fallback with
native pressure and compression metrics rather than infer PSI.

### Expected listeners (`net`)

The default rule acts only on administrator-authored listener watchlists.
Listener presence is stable and actionable; total desktop connection counts
are not, so the generic baseline-relative connection-spike rule is removed.
A future Darwin sampler may add `NWPathMonitor` path, DNS and interface state.

### Expected processes (`service`)

The watchlist uses process names because macOS has no systemd. Nothing alerts
until an operator declares an expected process. A future launchd adapter should
support explicit labels and last-exit/restart state; monitoring every Apple
launchd job would be wrong because many jobs are intentionally on demand.

### FTMON health (`self`)

Database, RSS, event-reader and daemon CPU rules remain enabled because a
monitor must report its own failure. The CPU budget is macOS-specific: live
sampling showed that the Linux threshold was too low for psutil enumeration on
this Intel host. The ten-minute average and confirmation cycles still reject
brief startup cost.

### Unified log (`events`)

The monitor is enabled behind a fixed source predicate. A live unrestricted
run dropped more than 27,000 records within minutes; the stored sample was
dominated by normal TCC, DiagnosticsReporter, analyticsd, iCloud, preferences,
networking and sandbox activity. A downstream rule cannot protect the reader
queue, so both `log show` replay and `log stream` apply the allowlist before
Python reads a record and use the normal `default` level instead of `debug`.

The standard allowlist admits only:

- `fault` records whose originating executable is under `/Applications`,
  `/Library` (excluding `/Library/Apple`), `/opt`, `/usr/local`, or `/Users`;
- kernel messages containing an explicit I/O, media, disk, filesystem, or
  APFS corruption phrase.

Those records are normalized to `third-party-fault` or `storage-integrity`
event IDs before the declarative rules run. The storage
threshold is `critical` (the canonical FTMON mapping of Apple `fault`), while a
matched storage-integrity `error` is still retained as rule evidence.

This follows Apple's model: subsystem/category and predicates are the intended
filtering boundary, `error` represents process-level failure, and `fault` is
reserved for system or multiprocess failure. Severity is still producer intent,
not proof of operator actionability. Crash and hang monitoring should use the
structured DiagnosticReports files, while authentication, Gatekeeper, and
XProtect monitoring belongs to an entitled Endpoint Security adapter—not
ambient unified-log text.

References:

- [Apple unified logging](https://developer.apple.com/documentation/os/logging)
- [Apple fault-level semantics](https://developer.apple.com/documentation/os/oslogtype/fault)
- [Apple crash-report guidance](https://developer.apple.com/documentation/xcode/diagnosing-issues-using-crash-reports-and-device-logs)
- [Apple Endpoint Security](https://developer.apple.com/documentation/endpointsecurity)

## Deliberately deferred

- Crash/spin report ingestion needs a bounded file-event/checkpoint design.
- Native memory pressure, compression and swap-rate sampling needs a Darwin
  system sampler rather than Linux PSI emulation.
- Thermal state and battery warning/time remaining need Foundation/I/O Kit
  adapters.
- Network path/DNS/interface status needs a Network framework adapter.
- launchd label state needs a service adapter with captured real output.
- Endpoint Security is out of scope for the zero-bundle profile because it
  requires an entitled system extension; FTMON is an operations monitor, not
  an endpoint-detection product.

These omissions are explicit: the profile stays quiet and truthful until each
Apple-native signal has a tested adapter and real-host fixtures.

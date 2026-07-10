# Review of FTMON v2 Specification

## Overall assessment

The concept is excellent: a local-first, resource-conscious workstation monitor with useful historical context, declarative configuration, and AI as a controlled interface rather than a dependency. It is a coherent product, not merely a modernization of the Perl version.

The specification is also unusually strong for a draft. It defines non-goals, authority boundaries, deterministic testing, resource budgets, platform seams, and failure behavior early. Those choices should prevent considerable implementation drift.

The main risk is scope. This is simultaneously a monitoring engine, time-series store, incident manager, expression language, event-ingestion service, web application, CLI, MCP server, and safe action runner. The concept remains sound, but it should be delivered in milestones rather than attempting to land the entire v1 surface at once.

## Issues to resolve before implementation

### 1. Incident identity and escalation are inconsistent

The logical incident record contains a single `rule_id` (§5.4), confirmation is per rule and entity, but escalation merges every firing rule into one incident per monitor and entity (§9.1, IN-03).

This leaves important behavior undefined:

- Which rule owns the incident and message?
- What happens when a critical rule clears but a warning remains true?
- Does severity downgrade?
- Which rule's `clear_cycles` applies?
- If two unrelated rules fire for one disk, should they be one incident?
- Which action runs?

Either make incidents strictly `(monitor, rule, entity)`—the simpler v1 design—or explicitly model an incident episode containing a set of active rule evaluations.

### 2. Event incidents have no clearing model

Metric rules eventually evaluate false, but an event is instantaneous. The events monitor says an event opens or refreshes an incident (§7.6.3), without defining how it clears.

Add explicit event semantics, such as:

- group matching events into an episode;
- refresh `last_seen` and the occurrence count;
- clear automatically after a rule-specific quiet period;
- distinguish cooldown from auto-clear;
- define whether a new occurrence reopens a cleared episode.

Also specify the stability of `message-hash`: normalization, hash algorithm, collision treatment, and removal of volatile values matter.

### 3. The expression language contradicts its examples

Several details need reconciliation:

- `severity >= error` uses `error` as a symbolic constant, but EX-02 only defines `None`, booleans, and byte units as constants.
- Functions may supposedly use `entity=`, while keyword arguments appear to be forbidden.
- Unresolvable names fail validation, but entity attributes are dynamic and platform-dependent.
- "Any comparison involving `None` is False" is not quite Kleene three-valued logic. The behavior of `not None`, `None or True`, chained comparisons, and `in` needs an explicit truth table.
- NaN, infinity, division by zero, numeric overflow, invalid regexes, and regex complexity need defined outcomes.
- Derived metrics can depend on other derived metrics, but ordering and cycle detection are unspecified.

Include a compact formal semantics table and make every built-in expression pass the validator before freezing the language.

### 4. The TOML schema is not complete enough to implement the built-ins

The example is called normative, but the full schema is deferred to code (§8.1, MD-01). Important structures are absent: watchlists, event rules, source parameters, schedules, clear cycles, promotion, notification overrides, and connection/service targets.

There are also small inconsistencies:

- `version = "1"` is a string annotated as an integer.
- `schema = 1`, required by VC-02, is missing from the example.
- `enabled` is referenced later but absent.
- `source = "derived"` lacks clear entity/source semantics.

Before implementation, write one complete valid TOML definition for each of the seven monitors. That exercise will expose most remaining schema and expression-language gaps.

### 5. Sampling ownership needs a clearer model

It is unclear whether a sampler runs once per source and feeds multiple monitors, or once per monitor. Running process enumeration separately for `leak`, `hog`, and `service` would waste resources and produce subtly different timestamps.

Define a pipeline such as:

```text
source collection -> normalized snapshot -> monitor projections -> derived metrics -> rules
```

Also, a reliable wall-clock timeout is difficult for sequential in-process Python code (§6, SA-02). Threads cannot safely terminate stuck calls. The specification should say whether timeouts are cooperative, subprocess-based, or merely detected after return.

### 6. Resource and retention feasibility needs a capacity model

The 200 MB database and 100 MB RSS targets are good constraints, but they are not yet derived from expected cardinality.

Potential tensions include:

- track-all process history stores only 15 in-memory samples (§6, SA-05);
- general calculation buffers may retain six hours or 10,000 points for every entity and metric (§7.3, CA-04);
- multiple process metrics across hundreds of processes could exceed the RSS target;
- pruning only rollups may not bring the database below 200 MB if raw samples, events, attributes, or incident history dominate;
- SQLite file size does not shrink merely because rows were deleted.

Add a capacity worksheet with expected rows per day, bytes per row, maximum active entities, ring-buffer allocation policy, and a deterministic degradation order. Also define hard size limits for incident history and entity attributes, even if incidents themselves are retained forever.

### 7. Event ingestion durability and backpressure are missing

The event reader needs defined behavior for:

- where ingestion begins on first run;
- persisted journald cursors or platform bookmarks;
- restart replay and deduplication;
- bounded queue size;
- overflow behavior;
- events generated while the daemon is down;
- malformed input;
- a reader that remains alive but stops producing;
- ordering when source timestamps are late or clocks move.

Without a cursor, restart behavior will either lose events or duplicate them.

### 8. Multi-process coordination also occurs through files

The document says processes share only SQLite (§3), but the CLI, MCP server, web UI, and daemon also coordinate through monitor files.

Specify:

- atomic writes using temporary files plus rename;
- file permissions;
- concurrent draft edits and approval races;
- symlink handling;
- locking or optimistic version checks;
- crash behavior midway through approval;
- how the "last good configuration" survives a daemon restart when the file remains invalid.

Persisting the active normalized definition and its hash/version in SQLite would make configuration history and restart behavior more robust.

### 9. The no-duplicate-notification guarantee needs an outbox design

The kill-9 test requires no duplicate notifications after restart (§16.4, TS-05). There is an unavoidable crash window between recording a notification and invoking `notify-send`; desktop notification systems generally provide no transactional or idempotent delivery.

The specification should choose an honest guarantee:

- at-most-once, accepting possible lost notifications;
- at-least-once, accepting possible duplicates; or
- best-effort deduplication through a durable outbox and delivery IDs.

A durable outbox is still worthwhile, but exactly-once delivery cannot be guaranteed at the external notification boundary.

### 10. The baseline algorithm is not implementable as written

"Exponentially-weighted p50" (§7.4, CA-05) does not name a standard, uniquely reproducible algorithm.

Define:

- the quantile estimator;
- decay or half-life;
- update interval;
- sample weighting;
- minimum coverage versus merely elapsed time;
- handling of missing data and entity restarts;
- whether alerts contaminate the learned baseline;
- whether day-of-week or time-of-day seasonality is intentionally absent.

For v1, an exponentially weighted mean or a precisely specified rolling median may be easier and more testable.

## Important product improvements

### Data privacy

Process command lines and logs can contain tokens, filenames, URLs, and personal information. Define file modes, optional command-line collection, redaction and truncation, export behavior, and what MCP clients are allowed to retrieve.

### Loopback web security

Binding to `127.0.0.1` is helpful but does not eliminate hostile-browser, DNS-rebinding, `Host` header, or cross-origin risks. Require an exact `Host` allowlist, strict `Origin` validation for writes, non-wildcard CORS, and secure response headers.

### Clock behavior

Scheduling should use monotonic time; event and sample timestamps should use wall time. State what happens after suspend/resume, NTP jumps, timezone changes, and missed cycles.

### Entity disappearance

A process, mount, service, or socket may vanish between samples. Specify whether disappearance produces a tombstone, a missing metric, immediate false, or `None`. This directly affects service rules and incident clearing.

### Monitor removal and renaming

PM-04 applies removal, while MD-06 only describes editing. State what happens to open incidents, stored history, baselines, and entity records when a definition disappears or changes name.

### Built-in monitor count

The document says seven built-in user monitors, but RB-02 introduces an eighth `self` monitor. Calling it an internal or system monitor would remove ambiguity.

### Operational recovery

Add database integrity checking, safe repair or rebuilding, export, and perhaps an `ftmon doctor` command. Backups should use SQLite's backup API because copying a WAL database naively can be inconsistent.

### Accessibility

Add requirements for severity indicators, keyboard navigation, reduced motion, and not relying on color alone.

## Recommendations for the current open questions

- **OPEN-1:** Treat all thresholds as provisional until exercised against recorded fixture data and at least a short real-system observation period.
- **OPEN-2:** Defer per-process network attribution.
- **OPEN-3:** Keep quiet hours global in v1.
- **OPEN-4:** Expose the monitor-definition guide as an MCP resource. Expose the full specification only if it helps monitor authors rather than confusing the operational interface.
- **OPEN-5:** Use five-second polling initially rather than SSE. Choose the smallest vendorable chart library that satisfies accessibility and long-range rendering requirements.
- **OPEN-6:** Keep the daemon and web UI as fully separate services, consistent with the stated process model.
- **OPEN-7:** Resolve licensing before implementation begins.

## Suggested delivery stages

1. Engine, SQLite, process/disk/system samplers, CLI, and file notifications.
2. Incident behavior, retention, deterministic fixtures, and initial built-in definitions.
3. Journald ingestion and service/network monitoring.
4. MCP draft and approval workflow.
5. Web UI.
6. Actions, after the core incident model is stable.

Each stage should be independently usable and retain the deterministic test substrate described in §16.

## Conclusion

The strongest part of the proposal is its philosophy: restrained local monitoring, inspectable definitions, excellent testability, and deliberately limited AI authority. The most important work now is not adding features; it is making incident aggregation, event lifecycle, sampling ownership, configuration coordination, resource feasibility, and failure guarantees completely precise.

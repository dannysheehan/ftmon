---
name: ftmon-add-extra-monitor
description: Add or update a Nagios-compatible or FTMON JSON integration in FTMON's extra-monitors catalogue, including execution registry, declarative monitor, observed metric mappings, fixtures, operator documentation, Exchange metadata, tests, and rationale-led commits. Use for Monitoring Plugins, Nagios plugins, local checks, hardware/service probes, or original FTMON JSON checks.
---

# Add an FTMON extra monitor

Create a reviewed integration recipe, not an unbounded plugin installation.
Keep third-party executables upstream while FTMON adds local history, confirmed
incidents and explicit Trends.

## Read authority first

1. Locate the repository and read its active `AGENTS.md`.
2. Read the relevant current sections of `SPEC.md`, `DESIGN.md`,
   `docs/external-checks.md`, `docs/check-authoring.md` (executable trust
   contract, protocol details, binary location convention),
   `extra-monitors/README.md`, the complete `extra-monitors/_template/`, and
   `tests/extra_monitors/test_recipes.py`.
3. Inspect existing recipes and `git status`. Preserve unrelated user changes.

Treat those files as authoritative over this skill. Never rely on a remembered
schema, requirement ID, validation command or catalogue field.

## Choose and verify the protocol

Use `nagios` for an existing check whose exit state and first output line follow
the Nagios convention. Use `ftmon-json` for a check that can emit FTMON's strict
typed JSON object. Do not wrap unreliable output merely to label it native.

For either protocol:

1. Find the exact executable using the supplied path or package metadata.
2. Record its actual version, authoritative HTTPS upstream and licence. Do not
   infer licensing from a filename.
3. Run the proposed exact argv as the FTMON service user when safe and available.
   Capture the exit state and bounded stdout without exposing credentials.
4. Map only finite labels and UOMs observed in that output. Never fabricate a
   fixture, success result, supported platform or compatibility claim.

For Nagios, inspect only perfdata after `|` on the first line; later lines are
intentionally ignored. For FTMON JSON, verify the strict schema, state, message,
metric object shapes, finite numeric values, unique labels and zero exit status.

Set evidence honestly:

- `real-system-verified`: exact argv exercised on the recorded system/version;
- `tested`: schema and deterministic protocol fixtures verified without a live run;
- `recipe-only`: compatibility evidence remains incomplete.

## Design execution authority

Write one exact `[check.<alias>]` in `checks.toml.example`:

- use an absolute executable and direct argv, never a shell;
- set the plugin's own timeout below FTMON's outer process-group timeout;
- add SNI, address family or similar flags only for an observed/documented need;
- keep passwords, tokens, URL user-info and private-key material out of argv;
- explain non-obvious flags and timeout differences with why-focused comments;
- never grant broad `sudo`, `docker`, `adm` or plugin-directory authority.

On a live host, `ftmon check trust <path>` reports every failing condition of
the executable trust contract (ownership, writability, symlinks) without
running the candidate; include it in the recipe's manual test or operator
steps so a rejected binary is diagnosed before registration, not after.

For an unavoidable privileged read-only check, prefer the privileged exporter
pattern from `docs/external-checks.md`: a root-owned timer snapshots the data
to a file and the check parses it unprivileged, treating a stale file as
unknown. `sudo` cannot work under the shipped `NoNewPrivileges=yes` units; only
on a custom unit without that hardening may `/usr/bin/sudo -n` invoke one exact
root-owned wrapper — document an argument-free `sudoers` rule, ownership,
`visudo -c`, and why elevation is required. Keep remediation separate.

Third-party Nagios and JSON executables remain separately installed. Ship a
script inside a recipe only when FTMON is its original maintainer, with an
explicit licence header and direct success/failure tests as required by XR-05.

## Create the recipe

Create `extra-monitors/<stable-id>/` from the current template through visible
file edits. Do not add extra files outside the recipe contract.

### Metadata and article

- Choose an accurate bounded category, sorted unique lowercase tags and the
  earliest FTMON version supporting every feature used.
- Record protocol, platforms, privilege, network, upstream, licence, evidence
  status and exact verified version without implying broader support.
- Complete every required README heading: why, package/upstream installation,
  configuration rationale, exact manual test, security/permissions, evidence,
  upstream and licence.
- Explain why thresholds, flags, mappings, timeouts and privilege exist. Do not
  narrate obvious syntax or reproduce upstream documentation.

### Monitor definition

- Keep `enabled = false` until an operator reviews installation-specific values.
- Reference only the registered alias; never place argv in `monitor.toml`.
- Choose a stable entity identity and map exact observed labels/UOMs to honest
  metric names, units and kinds.
- Confirm plugin warning/critical/unknown states across sensible cycles. Treat
  unknown as check health, never success.
- Add derived values and `[[trend]]` only when a returned metric has meaningful
  time behavior. Do not invent panels merely to decorate Exchange.
- Document tunable thresholds as parameters and keep plugin thresholds in argv.

### Fixtures and maintained scripts

Add deterministic fixtures for relevant OK, warning, critical, unknown or
malformed behavior. Stabilize volatile hosts, durations or counts only without
changing the protocol shape being claimed.

For an original maintained FTMON JSON script, use only documented platform
tools or the Python standard library, add the required licence header, bound its
runtime/output, avoid inherited secrets, and test direct success and failure
behavior. Never execute a contributed script merely to build Exchange.

## Validate from narrow to broad

Run the exact external command again when safe, then run the repository's
current equivalents of:

```sh
uv run pytest -q tests/extra_monitors
uv run ruff check tests/extra_monitors
uv run python tools/build_exchange.py --output dist/exchange
uv run pytest -q tests/exchange
uv run ruff check src tests tools/build_exchange.py
uv run pytest -q
git diff --check
```

Inspect the generated recipe page and search entry. Keep network, device and
root verification manual or explicitly opt-in; normal CI stays offline and
unprivileged. If live output disproves an existing example, correct every
affected SPEC, DESIGN and manual statement with the reason. Do not loosen a
parser, security boundary or test merely to accept surprising output.

## Finish within authority

Review the complete diff for secrets, vendored dependencies, unsafe paths,
false evidence, missing rationale and unrelated changes. Update relevant docs
and commit a coherent historical unit in the repository's current style,
including why the integration is designed this way.

Do not push, publish, install packages, edit `sudoers`, change DNS, or mutate an
external service unless the user explicitly authorizes that action. Report the
recipe path, observed result, mapped metrics, rules/Trends, validation, commit
and remaining operator steps.

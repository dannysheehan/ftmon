# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FTMON v2 is a lightweight, local-first, single-host systems monitor for Linux
(Python ≥ 3.11, MIT). It detects memory leaks, CPU hogs, disks filling, service
failures, and notable journal events; keeps bounded metric history in SQLite;
and exposes a CLI, a loopback-only web dashboard, and a local stdio MCP server.

It succeeds a GPL-licensed Perl monitoring engine from 2001–2003, porting its
design ideas (delta/monotonic calcs, consecutive-cycle confirmation,
baselining, escalation) but none of its code. The original source is
intentionally kept out of this repository (a local reference checkout may sit
in the gitignored `ftmon-legacy/`) and remains available at
<https://sourceforge.net/projects/ftmon/>. Do not vendor it here — the MIT/GPL
boundary is deliberate.

## Commands

Everything runs through `uv`:

```sh
uv sync                          # install/refresh the environment
uv run ruff check src tests tools  # lint (ruff is also the formatter; line length 100)
uv run pytest -q                 # full gate: unit + e2e + traceability (~35 s)
uv run pytest -q tests/ai_skills                       # shared-skill contract
uv run pytest -q tests/extra_monitors tests/exchange  # recipe/publication contract
uv run pytest tests/unit/test_expr_eval.py -q          # one file
uv run ftmon init --profile desktop|server             # write config + builtin definitions
uv run ftmon check             # one-shot sample/evaluate
uv run ftmon daemon            # the monitor loop
uv run ftmon web               # dashboard on http://127.0.0.1:8420/ (loopback only)
uv run ftmon doctor            # DB/config diagnostics
python3 tools/gen_reqindex.py --check   # regenerate/verify tests/reqindex.json from SPEC.md
```

Real-system smoke tests are opt-in (deselected by default); the CI suite is
deterministic and fixture-driven.

GitHub workflows: `ci.yml` (gate + reqindex check + build smoke on push/PR),
`release.yml` (a `v*` tag runs the gate, verifies the tag matches
`ftmon.__version__`, publishes to PyPI via Trusted Publishing, and creates the
GitHub Release — bump the version in both `pyproject.toml` and
`src/ftmon/__init__.py`), and `exchange.yml` (PRs build the static Exchange
site; only main deploys).

## Spec-driven workflow (the central process fact)

`SPEC.md` is the authoritative product spec; `DESIGN.md` is the companion
design (elements marked FROZEN must not be changed without amending the doc
first). Every requirement has a stable ID like `SA-06`, `IN-03`, `EC-02`.

Traceability is machine-enforced by `tests/unit/test_traceability.py` (TS-01):

- Tests cite the requirement IDs they verify in **bracketed docstrings**:
  `"""[EX-06] ..."""`.
- `tests/reqindex.json` is generated from SPEC.md by `tools/gen_reqindex.py`
  and must match on regeneration; `tests/traceability_pending.json` lists
  testable IDs not yet covered (a ratchet — an ID can't be both covered and
  pending).
- Adding/changing a requirement ⇒ regenerate the index. `NG-*`/`DO-*` IDs are
  exempt.
- The pending list was burned down to **empty** in M10 and TS-18 says it may
  only shrink — a new testable requirement must land with its tests in the
  same change, not by re-growing the pending list.
- SPEC changes must also bump the `Status:` header line and add a §21
  changelog entry, and DESIGN's "Companion to SPEC.md vX.Y" line must follow —
  this drifted twice before it became machine-checked (TS-19); the gate now
  fails on mismatch.

When you land a user-visible change, updating the matching docs
(`docs/manual.md`, `docs/install.md`, `docs/definitions.md`) is part of the
work package, not a follow-up. See `CONTRIBUTING.md` — its prime rule:
comments/docstrings record **why** (constraint, trade-off, spec ID), never
narrate mechanics. Every module docstring cites the spec IDs that shaped it.

## Architecture

Package layout (full annotated tree in DESIGN.md §1):

- `src/ftmon/expr/` — restricted expression language (parse → IR → eval).
  **Stdlib-only, imports nothing from `ftmon.*`** (EX-04); `eval` never raises
  (EX-06) — errors become `None` plus a counter tick. Three-valued logic in
  `tribool.py`.
- `src/ftmon/definitions/` — TOML monitor definitions: `schema.py` validator,
  `loader.py` (TOML → `MonitorDef`, topo-sorted derived metrics), and
  `builtins/*.toml` package data. **Definitions are data, never code.** The
  normative copies live in `design/builtins/*.toml` and are mirrored into
  `src/ftmon/definitions/builtins/`; keep the two trees identical.
- `src/ftmon/sources/` — `Sampler`/`EventSource` implementations (process,
  disk, system, net, unit, journald) plus deterministic `fixtures.py` fakes
  that ship in the prod package and keep the platform seams honest (PL-04).
- `src/ftmon/checks/` — administrator-registered external checks: `registry.py`
  (argv authority), `runner.py` (no shell, scrubbed env, process-group kill,
  bounded output), Nagios/FTMON-JSON adapters. AI/definitions may reference a
  check alias but can never create one (EC-01).
- `.ai/skills/` — canonical, portable contribution workflows. Read the complete
  matching `SKILL.md` when asked to use one, but treat this file, SPEC, DESIGN,
  templates and tests as higher authority. Vendor discovery locations are
  personal/ignored links, not independently edited copies (AS-01/04).
- `extra-monitors/` and `exchange/` — reviewed external-check recipes and their
  inert static catalogue publisher. Third-party executables remain separately
  installed; recipes and site builds never grant command authority (XR-*/AS-02).
- `src/ftmon/engine/` — scheduler tick loop, per-monitor `pipeline.py`
  (snapshot → rings → derived → rules), ring buffers, `incidents.py` (pure
  state machine, FROZEN), episodes, effects/actions.
- `src/ftmon/store/` — SQLite (WAL, incremental autovacuum): migrations gated
  by `PRAGMA user_version`, daemon-side batched `writer.py`, shared `query.py`,
  retention/rollups, durable notification `outbox.py`.
- `src/ftmon/notify/` — desktop/file/ntfy/webhook/SMTP adapters with
  independent durable retry.
- `daemon.py` (composition root; owns the only bulk-write connection),
  `cli.py`, `mcp_server.py`, `web/` (operational app + isolated synthetic demo
  app), `demo.py`, `selfmon.py`, `systemd/` units (incl. the soak-evidence
  service/timer).
- `tools/` — maintainer tooling, linted like source: `gen_reqindex.py`
  (traceability), `soak_report.py` + `capture_soak_evidence.sh` (TS-17
  release-gate evidence; procedure in `docs/soak-procedure.md`),
  `build_exchange.py` (static Exchange site; treats recipe bytes as
  untrusted, XR-08), and `tuning/` live-host workload generators
  (`docs/tuning-procedure.md`). Their outputs — `soak/`, `tuning/evidence/` —
  are local artifacts and gitignored.

Key invariants:

- **No direct time access** — `time.time`/`datetime.now`/`time.sleep` only in
  `clock.py` (TS-03); everything else takes an injected `Clock`.
- Platform-specific behavior lives behind exactly four seams: samplers, event
  sources, notification adapter, paths/service wrapper (PL-01). No platform
  conditionals elsewhere.
- The web UI binds to 127.0.0.1 only; no auth exists by design (NG-05) — never
  add a non-loopback listener.
- Core model types are frozen dataclasses; the pipeline is pure-ish data flow
  so incidents can be tested independently.
- Respect SPEC §1.1 non-goals (fleet monitoring, plugin loading, etc.) — they
  are enforced scope, not TODOs.

## Conventions

- Lint rules are enforced as tests; `uv run pytest -q` is the gate for
  everything.
- Name tests by behavior and requirement, e.g.
  `test_daemon_rejects_second_instance_pm_02`.
- Commit subjects are concise and imperative, often milestone-prefixed
  (`M9: add bounded external checks`, `Docs: ...`).
- `main` is protected (squash-merge PRs only, CI required): work on
  `feature/…`, `fix/…`, `docs/…`, `chore/…` branches; never push to `main`.
- GitHub issues are the canonical backlog; `BACKLOG.md` is uncommitted local
  scratch that gets promoted. Issue bodies state problem, direction, likely
  touchpoints, and SPEC IDs; roadmap items get `enhancement` + `backlog`
  (drop `backlog` on starting work; PRs say `Closes #N`). Never open public
  issues for undisclosed vulnerabilities — `.github/SECURITY.md`. Details in
  `CONTRIBUTING.md` and `docs/github-hygiene.md`.
- `dist/`, `.venv/`, caches, `ftmon-legacy/`, `soak/`, and `tuning/evidence/`
  are gitignored; don't commit build artifacts, evidence captures, or the
  legacy tree.
- Review artifacts and audit records (`docs/REVIEW-3.md`,
  `docs/drift-audit-m10.md`) are maintainer-facing records per DO-09, not
  user documentation — don't cite them from the manual or README.
- **Never run `sqlite3` (or any direct SQL client) against the live FTMON
  database while the daemon is running.** Connections set a 5 s
  `busy_timeout`, but an external write transaction that outlives it makes
  the writer's `BEGIN IMMEDIATE` in `commit_tick` raise
  `OperationalError("database is locked")`, and the daemon currently dies
  with a traceback instead of recovering (#23) — the web UI just shows
  "data is stale" until someone restarts it. For inspection, stop the
  daemon first or open the DB read-only (`file:...?mode=ro`); for cleanup,
  stop the daemon.

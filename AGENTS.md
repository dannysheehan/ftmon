# Repository Guidelines

> **Note to AI Agents:** The comprehensive, up-to-date instructions, architecture, and workflow for this repository are maintained in [CLAUDE.md](CLAUDE.md). Please read `CLAUDE.md` before proceeding with any code changes.

## Project Structure & Module Organization

This repository contains FTMON v2. The original Perl implementation remains
separately published at <https://sourceforge.net/projects/ftmon/> and must not
be added to this repository.

- `SPEC.md` is the authoritative product and architecture specification for new work. Requirements have stable IDs and tests should reference them.
- `CLAUDE.md` summarizes the v2 architecture, commands, and the spec/traceability workflow for AI coding agents.
- `README.md` gives users and contributors the repository entry point.
- `docs/` contains the install guide, user manual, and definition reference.
- `.ai/skills/` contains portable, repository-owned AI contribution workflows;
  current repository authority and tests always override skill prose.
- `extra-monitors/` contains tested external-check recipes rendered at FTMON
  Exchange.

New v2 code should follow `SPEC.md`: Python package code in `ftmon/`, tests in `tests/`, and user-editable monitor definitions as TOML.

## Build, Test, and Development Commands

Use the checked-in `uv` environment for development and validation:

```sh
uv sync
uv run ruff check src tests
uv run pytest -q
```

## Coding Style & Naming Conventions

For v2, follow `SPEC.md`: Python 3.11+, `uv`, `ruff`, `pytest`, SQLite, and TOML monitor definitions. Keep platform-specific logic behind the specified adapters.

## Testing Guidelines

Add pytest coverage for all v2 behavior. Name tests by behavior, and include requirement IDs from `SPEC.md` where relevant, for example `test_daemon_rejects_second_instance_pm_02`. Prefer fixture-driven deterministic tests; keep real-system smoke tests opt-in.

## Commit & Pull Request Guidelines

This repository has no existing commit history, so no local convention is established. Use concise, imperative commit subjects such as `Add daemon lock handling` or `Document legacy config validation`.

Pull requests should describe the change, reference affected `SPEC.md` requirement IDs, and list validation commands run. Include screenshots only for web UI changes. Link closing issues with `Closes #N` when applicable; see **Issues** above.

## Security & Configuration Tips

Monitor definitions must remain declarative TOML with restricted expression evaluation, as specified in `SPEC.md`.

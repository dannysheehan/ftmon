# Repository Guidelines

## Project Structure & Module Organization

This repository is preparing FTMON v2 while preserving the original Perl implementation.

- `SPEC.md` is the authoritative product and architecture specification for new work. Requirements have stable IDs and tests should reference them.
- `CLAUDE.md` summarizes legacy architecture and caveats.
- `ftmon-legacy/` contains the 2001-2003 Perl implementation. Treat it as read-only unless a task explicitly targets legacy maintenance.
- `ftmon-legacy/base/bin/` contains entry points such as `ftmon.pl`, `ftmon_gui.pl`, and service wrappers.
- `ftmon-legacy/base/lib/FTMON/` contains legacy engine modules. Vendored third-party Perl modules in `ftmon-legacy/base/lib/` should not be modified.
- `ftmon-legacy/cfg/` contains executable Perl configuration files.

New v2 code should follow `SPEC.md`: Python package code in `ftmon/`, tests in `tests/`, and user-editable monitor definitions as TOML.

## Build, Test, and Development Commands

There is currently no v2 build system or test suite checked in. For new Python work, use `uv` and add the corresponding `pyproject.toml` scripts.

Useful legacy commands:

```sh
export BASE_DIR=$PWD/ftmon-legacy/base
perl -I$BASE_DIR/lib -I$BASE_DIR/lib/linux -c $BASE_DIR/lib/FTMON/Monitor.pm
perl $BASE_DIR/bin/ftmon.pl -c ftmon-legacy/cfg/RedHat/Linux/disk.cfg
perl $BASE_DIR/bin/ftmon.pl -o /tmp/ftmon-html -p ftmon-legacy/cfg -l /tmp/ftmon-log -v 60
```

The commands syntax-check a module, validate a config file, and run the legacy daemon locally.

## Coding Style & Naming Conventions

For v2, follow `SPEC.md`: Python 3.11+, `uv`, `ruff`, `pytest`, SQLite, and TOML monitor definitions. Keep platform-specific logic behind the specified adapters.

For legacy Perl edits, match the existing style: 2-space indentation, Perl 4/5-era package globals, banner comments, and `$FT::*` state. Do not modernize unrelated code.

## Testing Guidelines

Add pytest coverage for all v2 behavior. Name tests by behavior, and include requirement IDs from `SPEC.md` where relevant, for example `test_daemon_rejects_second_instance_pm_02`. Prefer fixture-driven deterministic tests; keep real-system smoke tests opt-in.

Legacy validation is mostly manual: use Perl syntax checks and config validation before changing legacy files.

## Commit & Pull Request Guidelines

This repository has no existing commit history, so no local convention is established. Use concise, imperative commit subjects such as `Add daemon lock handling` or `Document legacy config validation`.

Pull requests should describe the change, reference affected `SPEC.md` requirement IDs, list validation commands run, and call out any legacy files touched. Include screenshots only for web UI changes.

## Security & Configuration Tips

Legacy config files are executable Perl; do not treat them as inert data. New v2 monitor definitions must remain declarative TOML with restricted expression evaluation, as specified in `SPEC.md`.

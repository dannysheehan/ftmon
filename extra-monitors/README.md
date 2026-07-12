# Extra monitors

This directory is a curated collection of practical FTMON integrations. Each
recipe reads like a short technical article—what the check protects, why its
metrics matter, how to install it, and how to operate it safely—but also ships
machine-testable configuration and protocol fixtures.

FTMON does not copy the Nagios Exchange or vendor its plugins. Third-party
checks remain installed from their authoritative package or upstream project
under their own licence. A recipe documents how a known version fits FTMON's
bounded local-check convention.

## Recipe contents

Every recipe contains:

- `README.md` for the operator-facing explanation;
- `recipe.toml` for catalogue metadata and fixture expectations;
- `checks.toml.example` for administrator-owned execution authority;
- `monitor.toml` for the declarative FTMON monitor; and
- `fixtures/` with deterministic OK, warning, critical, unknown or malformed
  output relevant to that protocol.

The generic test in `tests/extra_monitors/test_recipes.py` discovers every
non-underscore directory. It validates metadata, required documentation,
registry/definition alias agreement, safe argv shape, definition schema and
fixture parsing without installing a plugin, using the network or requiring
root.

## Confidence labels

- **tested** means configuration and supplied output fixtures pass CI;
- **real-system-verified** additionally records a manually exercised upstream
  version;
- **recipe-only** documents an integration that still needs real-system
  verification;
- **privileged** means the operator must review an explicit, constrained
  permission boundary; and
- **networked** means the check contacts a service and may disclose its target.

These labels describe recipe evidence, not an endorsement or warranty for the
third-party project.

## Add a recipe

Copy `_template/`, choose a stable lowercase directory ID, and replace every
placeholder. Keep commands in `checks.toml.example`, never in `monitor.toml`.
Include the output fixture that taught you each mapping, especially labels and
units used by Trends.

Run:

```sh
uv run pytest -q tests/extra_monitors
uv run ruff check tests/extra_monitors
```

Network and real-system verification belongs in an explicitly opted-in test or
in the documented manual verification steps. Normal CI must remain offline,
unprivileged and independent of third-party packages.

See [External checks](../docs/external-checks.md) for the execution, privilege,
credential and licensing boundary that every recipe must preserve.

Accepted recipes are rendered at [FTMON Exchange](https://exchange.ftmon.org/).
The [publishing guide](../docs/exchange.md) explains the deterministic local
preview, publication metadata and GitHub Pages deployment boundary.

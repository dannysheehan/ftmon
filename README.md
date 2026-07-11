# FTMON

<!-- markdownlint-disable MD033 -->
<!-- HTML is needed to size the large source mark. -->
<p align="center">
  <img
    src="src/ftmon/web/static/brand/ftmon-mark.png"
    alt="FTMON monitor dial logo"
    width="160"
  >
</p>
<!-- markdownlint-enable MD033 -->

FTMON is a lightweight, local systems monitor for Linux desktops, workstations,
and standalone servers. It detects problems such as memory leaks, CPU hogs,
disks filling, service failures, and notable journal events while keeping
bounded metric history on the monitored machine.

It is designed for people who need more history and alerting than `htop` or
`btop`, but do not need a central monitoring stack such as Nagios, Zabbix, or
Prometheus and Grafana for one independently managed server.

> **Development status:** FTMON v2 is pre-release software. Interfaces and data
> formats may change before the first stable release.

**Live demo:** Explore the read-only interface at
[demo.ftmon.org](https://demo.ftmon.org/). It uses clearly labelled synthetic
data and does not monitor the demo server or expose an operational FTMON
installation.

## Why FTMON?

- Runs locally as your user or a dedicated service account, without a central
  monitoring server or cloud account.
- Provides a CLI and accessible offline web dashboard with historical charts.
- Stores metrics and incidents in SQLite with bounded retention.
- Uses editable, declarative TOML monitor definitions.
- Sends durable alerts through email, ntfy, generic webhooks, or desktop
  notifications, with each channel retrying independently.
- Offers a local stdio MCP server for AI-assisted investigation and definition
  drafting, with explicit user approval for changes.
- Ships deterministic unit and end-to-end tests for its monitoring behavior.

## A practical monitor for one server

FTMON is suitable for a small hosted server, VPS, home server, or lab machine
that needs dependable local monitoring without operating a separate monitoring
platform. The server profile disables desktop popups, runs under a dedicated
unprivileged account, retains history locally, and can notify an administrator
through remote channels when an incident opens or recovers.

The operational dashboard remains bound to loopback and is reached through an
SSH tunnel. This keeps the unauthenticated management interface off the public
Internet while still making it useful on a remote headless host. A hardened
systemd unit and complete single-server installation procedure are provided in
the [installation guide](docs/install.md#dedicated-single-server-service).

## Quick start

FTMON requires Python 3.11 or newer and
[uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/dsheehan/ftmon.git
cd ftmon
uv sync
uv run ftmon init --profile desktop
uv run ftmon check
uv run ftmon daemon
```

In another terminal, start the local dashboard:

```sh
uv run ftmon web
```

Then open <http://127.0.0.1:8420/>. FTMON binds only to loopback and the web UI
loads no external assets.

For a headless single server, initialize with `--profile server`. This writes
explicit settings with desktop popups disabled; remote ntfy, webhook, and SMTP
channels use environment or protected-file credential references and maintain
independent durable retry state.

For a user-level service or a hardened dedicated `ftmon` server account,
follow the [installation guide](docs/install.md). The operational dashboard
stays on loopback; reach it remotely with an SSH tunnel rather than exposing
the unauthenticated UI through a public reverse proxy.

The live [public demonstration](https://demo.ftmon.org/) is a different,
GET-only application over deterministic synthetic data. Its reproducible DNS,
Caddy, systemd, verification, update, and rollback runbook is in the
[installation guide](docs/install.md#publish-the-synthetic-demo-website).
Never expose an operational FTMON database as a demo.

## Documentation

- [User manual](docs/manual.md) — concepts, daily use, tuning, trends, and
  troubleshooting.
- [Installation guide](docs/install.md) — `uv`, systemd, web, MCP, actions, and
  backups.
- [Monitor definition reference](docs/definitions.md) — TOML schema, expression
  language, and examples.
- [Product specification](SPEC.md) and [technical design](DESIGN.md) — normative
  behavior, rationale, architecture, and requirement IDs.
- [Contributing guide](CONTRIBUTING.md) — development and documentation
  standards.

## Original FTMON

This repository is a from-scratch Python successor to the original **Fast Track
Systems Monitor**, a Perl monitoring engine first published in 2002. The
original GPLv2 project and its downloads remain available from the
[official FTMON project on SourceForge](https://sourceforge.net/projects/ftmon/).

The original source is not included in this repository. Keeping the projects
separate makes their provenance and licensing boundaries clear: this v2
repository is MIT licensed, while the original SourceForge project is GPLv2.

## Development

```sh
uv sync
uv run ruff check src tests
uv run pytest -q
```

Tests reference stable requirement IDs from [SPEC.md](SPEC.md). When changing
behavior, update the relevant specification, design rationale, tests, and user
documentation together.

## License

FTMON v2 is available under the [MIT License](LICENSE). The separately published
original FTMON project retains its own GPLv2 license.

# Documentation drift audit — M10 (DO-09)

**Date:** 2026-07-12  
**Auditor:** development pass during TS-17 soak window  
**Scope:** `README.md`, `docs/install.md`, `docs/manual.md`, `docs/definitions.md`,
external claims in README

## Method

1. Execute documented CLI entry points and compare `--help` output.
2. Compare prose defaults and paths to `paths.py`, `cli.py`, and packaged units.
3. HTTP GET external URLs cited in README (demo, Exchange, repository).
4. Fix drift found in the same change set.

## Results

| Check | Result |
| --- | --- |
| `ftmon --help` subcommands match manual/install | **Pass** — all listed subcommands present |
| `ftmon init --profile {desktop,server}` | **Pass** |
| README quick start (`uv sync`, `init`, `check`, `daemon`, `web`) | **Pass** |
| README architecture section matches DESIGN process model | **Pass** |
| `ftmon incident`, `ftmon top` documented as available | **Fixed** — removed stale *(soon)* markers |
| Server install paths (`/var/lib/ftmon`, `/etc/ftmon/checks.toml`) | **Pass** — matches `ftmon-server.service` |
| Soak capture documented for server installs | **Fixed** — added install.md cross-link |
| Loopback / shared-login security posture | **Fixed** — expanded install.md (F-09) |
| Restart confirm-counter behavior | **Fixed** — added manual.md (F-07) |
| https://demo.ftmon.org/ | **Pass** — HTTP 200 |
| https://exchange.ftmon.org/ | **Pass** — HTTP 200 |
| https://github.com/dannysheehan/ftmon | **Pass** — HTTP 200 |
| `docs/definitions.md` schema examples | **Pass** — unchanged; validated by existing `test_definitions.py` gate |

## Notes

- Full line-by-line execution of every install.md demo/Caddy procedure was not
  repeated on this date; server demo deployment was verified operationally on
  2026-07-12.
- Prose docs are not machine-checked in CI; repeat this audit when user-visible
  behavior changes or before the v1.0 tag.

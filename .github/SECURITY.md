# Security Policy

## Supported versions

FTMON is pre-1.0 (`2.x` alphas). Security fixes land on the latest published
release when practical; there is no long-term support branch yet.

| Version | Supported |
| ------- | --------- |
| latest `2.x` on PyPI / GitHub Releases | yes |
| older alphas / untagged builds | no |

## Reporting a vulnerability

Please use GitHub **Private vulnerability reporting** on this repository
(Security → Report a vulnerability). Do not open a public issue for undisclosed
security bugs.

Reports should include:

- Affected version or commit
- Impact summary and reproduction steps
- Whether the issue is local-only (daemon/web/MCP on the monitored host) or
  involves supply-chain / release integrity

We will acknowledge privately and coordinate a fix and disclosure.

## Design boundaries (in scope vs not)

FTMON is local-first by construction (see SPEC §1.1 / SE-01):

- The operational web UI binds to loopback only and has **no authentication**
  (NG-05). Exposing it beyond `127.0.0.1` is out of scope and unsupported.
- Monitor definitions are declarative TOML with a restricted expression
  language; they are not a plugin/code-loading surface.
- External checks run only under administrator-registered aliases (`checks.toml`);
  AI/definitions cannot mint new command authority (EC-01).
- Releases publish to PyPI via Trusted Publishing (OIDC); there are no
  long-lived PyPI API tokens in this repository.

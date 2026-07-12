# Shared AI contribution skills

FTMON publishes a small number of reviewed contribution workflows under
`.ai/skills/`. They help an AI agent navigate repetitive work without replacing
the repository's authority or granting the agent additional permissions.

The canonical skill for extra-monitor contributions is:

```text
.ai/skills/ftmon-add-extra-monitor/SKILL.md
```

It covers Nagios-compatible and FTMON JSON checks, including evidence status,
execution and privilege boundaries, metric mappings, fixtures, documentation,
Exchange metadata, validation and commits. Manual contribution instructions in
`extra-monitors/README.md` remain complete for contributors who do not use a
skill-aware agent.

## Trust model

Audit a skill like executable code before installing it. A skill can instruct
an agent to read files, run commands and make edits with the agent's existing
permissions. FTMON's shared skill contains instructions and UI metadata only;
it grants no tools, credentials or approval bypass.

Repository authority has fixed precedence:

1. the user's current request and approvals;
2. `AGENTS.md`;
3. `SPEC.md` and `DESIGN.md`;
4. current templates, schemas and tests;
5. shared skill prose.

The skill therefore reads those files at the start of every task instead of
embedding a second copy of the monitor schema. If they conflict, follow the
higher authority and update stale skill text in the same change.

## Use without installation

Any filesystem-capable coding agent can be asked to read the canonical file:

```text
Read .ai/skills/ftmon-add-extra-monitor/SKILL.md completely, then use it to add
the requested extra monitor. Treat current repository files as authoritative.
```

This is the most portable form. Native automatic discovery and invocation are
product features, not properties of `.ai/skills/`.

## Install for Codex

Codex personal skills live under `${CODEX_HOME:-$HOME/.codex}/skills`. From the
repository root, link the canonical directory so updates remain visible:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/.ai/skills/ftmon-add-extra-monitor" \
  "${CODEX_HOME:-$HOME/.codex}/skills/ftmon-add-extra-monitor"
```

If the destination already exists, audit and remove or rename it first; do not
silently overwrite a personal skill. Start a new Codex session, then invoke:

```text
Use $ftmon-add-extra-monitor to add check_dns to the catalogue.
```

The optional `agents/openai.yaml` supplies Codex UI text only. `SKILL.md`
remains the portable semantic authority.

## Install for Claude Code

Claude Code supports personal skills in `~/.claude/skills/` and project skills
in `.claude/skills/`. This repository ignores `.claude/` because local agent
permissions and session state must not be committed.

For a checkout-local project skill:

```sh
mkdir -p .claude/skills
ln -s ../../.ai/skills/ftmon-add-extra-monitor \
  .claude/skills/ftmon-add-extra-monitor
```

For a personal skill usable from any checkout:

```sh
mkdir -p "$HOME/.claude/skills"
ln -s "$PWD/.ai/skills/ftmon-add-extra-monitor" \
  "$HOME/.claude/skills/ftmon-add-extra-monitor"
```

Start a new Claude Code session and ask it to use
`ftmon-add-extra-monitor`. Discovery behavior can change between product
versions, so consult current Claude Code documentation if it is not found.

## Validate a shared skill

Run the repository-owned, vendor-neutral checks:

```sh
uv run pytest -q tests/ai_skills
uv run ruff check tests/ai_skills
```

These tests catch structural drift, placeholders, missing repository references
and omitted safety concepts. They do not prove that every model will interpret
the workflow correctly. Review generated diffs, live-verification claims and
privilege changes exactly as for a human contribution.

Do not maintain edited copies under vendor directories. Change the canonical
skill, validate it, and let personal/project links expose the same reviewed
content to each tool.

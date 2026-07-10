# Contributing / Documentation Standard

This project is built spec-first and implemented partly by AI models working
from frozen contracts. Documentation is how rationale survives that process.
These rules are binding for all code, human- or model-written.

## The prime rule: document the WHY

Code shows *what* it does; comments and docstrings exist to record what the
code cannot show — **why it is this way**: the constraint, the trade-off, the
rejected alternative, the spec requirement. A comment that restates the next
line is noise and will be removed in review.

Bad:  `# loop over the entities`
Good: `# deadline is checked between entities, not inside psutil calls -
       a stuck native call cannot be interrupted in-process (SA-02)`

## Docstrings

- **Every module** starts with a docstring: one line of purpose, then the
  rationale/constraints that shaped it, citing SPEC requirement IDs in
  parentheses — e.g. `(EX-04)`, `(PM-06)`. The IDs are load-bearing: they let
  a reader jump from code to the reasoning in SPEC.md/DESIGN.md.
- **Every public class/function** gets a docstring stating behavior at the
  contract level (inputs, outputs, error behavior, None semantics). Private
  helpers need one only when their reason for existing is non-obvious.
- **Every test** carries the requirement ID(s) it verifies in its docstring,
  bracketed: `"""[EX-06] Three-valued semantics..."""` — the traceability
  tooling (TS-01) depends on this.

## Comments

- Explain invariants the type system cannot express ("oldest first",
  "one transaction per tick, PM-03").
- Explain deliberate omissions ("per-process attribution deferred, NG-06").
- Never narrate mechanics, never leave commented-out code, never write
  comments addressed to a reviewer about the change itself.

## Documentation deliverables (SPEC section 17)

| Doc | Audience | Grows |
| --- | --- | --- |
| `docs/manual.md` (DO-04) | end users - install, concepts, daily use, tuning | one chapter per milestone; placeholders are marked |
| `docs/definitions.md` (DO-01) | monitor authors, human or AI (also the MCP resource) | frozen with the language |
| `docs/install.md` (DO-02) | operators | M6 |
| `--help` text (DO-03) | CLI users | with each subcommand |
| SPEC.md / DESIGN.md | maintainers | change-controlled, changelog required |

When you land a user-visible feature, updating the manual chapter is part of
the work package, not a follow-up.

## Style

Python >= 3.11, ruff (line length 100) is the linter and formatter authority.
No direct `time.time`/`datetime.now`/`time.sleep` outside `clock.py` (TS-03).
The `expr/` package imports stdlib only (EX-04). Layering rules: DESIGN.md
section 1. All lint rules are enforced as tests - `uv run pytest tests -q`
is the gate.

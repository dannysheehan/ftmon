"""Line-oriented Markdown fence extractors for CI-validated authoring docs.

Recipe and filter-example bodies MUST NOT contain a line that is exactly
three backticks — that is always the outer closer under a ``` fence.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS_MD = _REPO_ROOT / "docs" / "definitions.md"


def _extract_fences(text: str, open_prefix: str) -> list[tuple[str, str]]:
    """Return (marker_suffix, body) for fences whose opener starts with open_prefix.

    Opener lines look like `` ```toml recipe=<id> `` or `` ```expr filter-example ``.
    Closes on the next line that is exactly `` ``` ``.
    """
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(open_prefix):
            suffix = line[len(open_prefix) :].strip()
            i += 1
            body: list[str] = []
            while i < len(lines) and lines[i] != "```":
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError(f"unclosed fence starting with {open_prefix!r}")
            out.append((suffix, "\n".join(body) + ("\n" if body else "")))
        i += 1
    return out


def extract_toml_recipes(text: str | None = None) -> dict[str, str]:
    """Map recipe id → TOML body for `` ```toml recipe=<id> `` fences."""
    src = DEFINITIONS_MD.read_text(encoding="utf-8") if text is None else text
    recipes: dict[str, str] = {}
    for suffix, body in _extract_fences(src, "```toml recipe="):
        if not suffix or any(ch.isspace() for ch in suffix):
            raise ValueError(f"invalid recipe id in fence: {suffix!r}")
        if suffix in recipes:
            raise ValueError(f"duplicate recipe id {suffix!r}")
        recipes[suffix] = body
    return recipes


def extract_filter_examples(text: str | None = None) -> list[str]:
    """Expression strings from `` ```expr filter-example `` fences (order preserved)."""
    src = DEFINITIONS_MD.read_text(encoding="utf-8") if text is None else text
    examples: list[str] = []
    for _suffix, body in _extract_fences(src, "```expr filter-example"):
        expr = body.strip()
        if not expr:
            raise ValueError("empty filter-example fence")
        if "\n" in expr:
            raise ValueError(f"filter-example must be a single expression: {expr!r}")
        examples.append(expr)
    return examples

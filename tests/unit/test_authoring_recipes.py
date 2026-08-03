"""[DO-01][MC-05] CI-validated authoring recipes and filter_expr examples."""

from __future__ import annotations

from ftmon.definitions.loader import load_text
from ftmon.expr.parse import NameEnv, compile_expr
from tests.support.doc_fences import (
    DEFINITIONS_MD,
    extract_filter_examples,
    extract_toml_recipes,
)

EXPECTED_RECIPES = frozenset({
    "fd-pct",
    "aggregate-pressure",
    "optional-metric",
    "process-match",
})


def test_definitions_marked_recipes_load():
    """[DO-01] Every ```toml recipe=<id> fence is a complete valid definition."""
    recipes = extract_toml_recipes()
    assert set(recipes) == EXPECTED_RECIPES
    for recipe_id, body in recipes.items():
        assert "```" not in body.splitlines(), recipe_id
        load_text(body)


def test_unmarked_toml_fences_are_not_extracted():
    """[DO-01] Fragment / non-recipe fences must not enter the recipe map."""
    sample = """
```toml
schema = 1
[monitor]
name = "fragment"
```

```toml recipe=ok
schema = 1

[monitor]
name = "ok"
description = "complete"
version = 1
platforms = ["linux"]
interval = "60s"
source = "disk"

[[rule]]
id = "r1"
when = "used_pct > 1"
severity = "warning"
confirm_cycles = 1
message = "hi {entity}"
```
"""
    assert set(extract_toml_recipes(sample)) == {"ok"}


def test_documented_filter_expr_examples_compile():
    """[EX-02][MC-04] CI compiles the exact filter_expr strings from definitions.md."""
    examples = extract_filter_examples()
    assert examples  # drift if the section loses marked fences
    text = DEFINITIONS_MD.read_text(encoding="utf-8")
    for expr in examples:
        assert expr in text
        # Representative process attrs, including optional exe_base when present.
        env = NameEnv(attrs=frozenset({
            "name", "cmdline", "username", "exe", "exe_base", "display", "cmd_hint",
        }))
        compile_expr(expr, env)

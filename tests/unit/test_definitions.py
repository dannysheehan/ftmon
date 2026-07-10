"""[MD-01..09][CA-04][CA-07] definitions schema/loader tests.

Covers: the eight built-in definitions load and validate (MD-07); an
invalid-TOML corpus asserting specific structured error codes/paths; the
MD-08 derived-metric topological ordering / cycle detection; duplicate rule
ids; missing `schema` key; and the layering/time lints required of every
new module in this package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ftmon.definitions import ValidationError, load_dir, load_file, load_text

BUILTINS_DIR = Path(__file__).resolve().parents[2] / "src" / "ftmon" / "definitions" / "builtins"
DEFINITIONS_SRC = Path(__file__).resolve().parents[2] / "src" / "ftmon" / "definitions"

BUILTIN_NAMES = (
    "disk",
    "events",
    "hog",
    "leak",
    "load",
    "net",
    "self",
    "service",
)


# --------------------------------------------------------------------------
# [MD-07] all eight built-in definitions load and validate
# --------------------------------------------------------------------------


def test_builtins_dir_has_exactly_the_eight_shipped_files():
    found = {p.stem for p in BUILTINS_DIR.glob("*.toml")}
    assert found == set(BUILTIN_NAMES)


@pytest.mark.parametrize("name", BUILTIN_NAMES)
def test_builtin_definitions_load_successfully(name):
    """[MD-07] every shipped built-in must pass the same validator as `ftmon check`."""
    md = load_file(BUILTINS_DIR / f"{name}.toml")
    assert md.name == name
    assert md.content_hash
    assert len(md.content_hash) == 64  # sha256 hex


def test_disk_builtin_has_three_ladder_groups():
    md = load_file(BUILTINS_DIR / "disk.toml")
    groups = {r.group for r in md.rules}
    assert groups == {"space", "inodes", "filling"}


def test_leak_builtin_has_promotion():
    md = load_file(BUILTINS_DIR / "leak.toml")
    assert md.promotion is not None
    assert md.source == "process"


def test_events_builtin_has_no_interval_and_event_rules():
    md = load_file(BUILTINS_DIR / "events.toml")
    assert md.interval_s == 0.0
    assert all(r.cooldown_s is not None for r in md.rules)
    assert all(r.clear_after_s is not None for r in md.rules)


def test_load_dir_over_builtins_returns_no_errors():
    defs, errors = load_dir(BUILTINS_DIR)
    assert errors == []
    assert {d.name for d in defs} == set(BUILTIN_NAMES)


# --------------------------------------------------------------------------
# minimal valid fixtures used as a base for the invalid corpus
# --------------------------------------------------------------------------

VALID_SAMPLER = """
schema = 1

[monitor]
name = "test"
description = "test monitor"
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
"""

VALID_EVENTS = """
schema = 1

[monitor]
name = "test"
description = "test monitor"
version = 1
platforms = ["linux"]
source = "events"

[[rule]]
id = "r1"
when = "severity >= error"
severity = "error"
cooldown = "10m"
message = "hi {message}"
"""


def test_valid_fixtures_actually_load():
    """Sanity check: the two base fixtures used below must themselves be valid."""
    load_text(VALID_SAMPLER)
    load_text(VALID_EVENTS)


def _errors_of(text: str) -> list[dict]:
    with pytest.raises(ValidationError) as ei:
        load_text(text)
    return ei.value.errors


def _assert_error(errors: list[dict], *, code: str, path_prefix: str) -> dict:
    matches = [e for e in errors if e["code"] == code and e["path"].startswith(path_prefix)]
    assert matches, f"expected an error code={code!r} path~={path_prefix!r} in {errors!r}"
    return matches[0]


# --------------------------------------------------------------------------
# invalid-corpus: at least 12 parametrized bad TOMLs (MD-01/03/04/08)
# --------------------------------------------------------------------------

INVALID_CASES = [
    pytest.param(
        # a bare key must precede any [table] header to actually land at the
        # document's top level (TOML scoping) -- see the disk.toml note above.
        VALID_SAMPLER.replace("schema = 1\n", "schema = 1\nbogus_top_level_key = 1\n"),
        "unknown_key",
        "bogus_top_level_key",
        id="unknown-top-level-key",
    ),
    pytest.param(
        VALID_SAMPLER.replace('name = "test"', 'name = "1bad"'),
        "invalid_value",
        "monitor.name",
        id="bad-monitor-name",
    ),
    pytest.param(
        VALID_SAMPLER.replace('source = "disk"', 'source = "proces"'),
        "unknown_source",
        "monitor.source",
        id="unknown-source-with-hint",
    ),
    pytest.param(
        VALID_SAMPLER.replace(
            'when = "used_pct > 1"', 'when = "used_pct > coalesce(used_pct, d=1)"'
        ),
        "expr_syntax",
        "rule[0].when",
        id="kwargs-in-expression",
    ),
    pytest.param(
        VALID_SAMPLER.replace('when = "used_pct > 1"', 'when = "usd_pct > 1"'),
        "unknown_name",
        "rule[0].when",
        id="unknown-metric-with-suggestion",
    ),
    pytest.param(
        VALID_SAMPLER
        + '\n[[derived]]\nname = "a"\nexpr = "b + 1"\n\n[[derived]]\nname = "b"\nexpr = "a + 1"\n',
        "derived_cycle",
        "derived",
        id="derived-dependency-cycle",
    ),
    pytest.param(
        VALID_SAMPLER.replace('severity = "warning"', 'severity = "info"'),
        "invalid_value",
        "rule[0].severity",
        id="bad-severity-info-not-allowed",
    ),
    pytest.param(
        VALID_SAMPLER.replace('interval = "60s"', 'interval = "5s"'),
        "invalid_value",
        "monitor.interval",
        id="interval-below-minimum",
    ),
    pytest.param(
        VALID_EVENTS + "confirm_cycles = 3\n",
        "unknown_key",
        "rule[0].confirm_cycles",
        id="event-rule-with-confirm-cycles",
    ),
    pytest.param(
        VALID_SAMPLER + 'cooldown = "5m"\n',
        "unknown_key",
        "rule[0].cooldown",
        id="sampler-rule-with-cooldown",
    ),
    pytest.param(
        VALID_SAMPLER.replace('message = "hi {entity}"', 'message = "hi {nope_field}"'),
        "unknown_field",
        "rule[0].message",
        id="bad-template-field",
    ),
    pytest.param(
        VALID_SAMPLER.replace('platforms = ["linux"]', 'platforms = ["amiga"]'),
        "invalid_value",
        "monitor.platforms",
        id="invalid-platform-value",
    ),
    pytest.param(
        VALID_SAMPLER.replace('id = "r1"', 'id = "Bad_ID!"'),
        "invalid_value",
        "rule[0].id",
        id="bad-rule-id-syntax",
    ),
]


@pytest.mark.parametrize("text,code,path_prefix", INVALID_CASES)
def test_invalid_corpus(text, code, path_prefix):
    errors = _errors_of(text)
    _assert_error(errors, code=code, path_prefix=path_prefix)


def test_unknown_source_hint_names_the_closest_match():
    errors = _errors_of(VALID_SAMPLER.replace('source = "disk"', 'source = "proces"'))
    err = _assert_error(errors, code="unknown_source", path_prefix="monitor.source")
    assert err["hint"] is not None and "process" in err["hint"]


def test_unknown_metric_hint_names_the_closest_match():
    errors = _errors_of(VALID_SAMPLER.replace('when = "used_pct > 1"', 'when = "usd_pct > 1"'))
    err = _assert_error(errors, code="unknown_name", path_prefix="rule[0].when")
    assert err["hint"] is not None and "used_pct" in err["hint"]


# NOTE on the "window points overflow" (CA-04) case from the WP3 brief: with
# the current caps (MAX_WINDOW_S == 6h, MIN_INTERVAL_S == 15s) the largest
# possible points count is 6h / 15s == 1440, always <= MAX_POINTS (10_000),
# so the overflow branch in loader.py's windows check is unreachable through
# the public schema today. The check is implemented (see `_build`'s
# `points_overflow` block) and kept as a forward-compatible guard, but no
# test can currently trigger it without either raising MAX_WINDOW_S or
# lowering MIN_INTERVAL_S -- doing so is out of scope for WP3.


# --------------------------------------------------------------------------
# duplicate rule id / missing schema key (called out explicitly by WP3)
# --------------------------------------------------------------------------


def test_duplicate_rule_id_is_an_error():
    text = (
        VALID_SAMPLER
        + '\n[[rule]]\nid = "r1"\nwhen = "used_pct > 2"\nseverity = "error"\nmessage = "dup"\n'
    )
    errors = _errors_of(text)
    _assert_error(errors, code="duplicate_id", path_prefix="rule[1].id")


def test_missing_schema_key_is_an_error():
    text = VALID_SAMPLER.replace("schema = 1\n", "")
    errors = _errors_of(text)
    _assert_error(errors, code="missing_key", path_prefix="schema")


# --------------------------------------------------------------------------
# MD-08 topological ordering (positive case: forward + backward references)
# --------------------------------------------------------------------------


def test_derived_metrics_are_topologically_ordered():
    text = (
        VALID_SAMPLER
        + """
[[derived]]
name = "c"
expr = "b * 2"

[[derived]]
name = "a"
expr = "used_pct + 1"

[[derived]]
name = "b"
expr = "a + 1"
"""
    )
    md = load_text(text)
    order = [n for n, _ in md.derived]
    assert order.index("a") < order.index("b") < order.index("c")


# --------------------------------------------------------------------------
# CA-04 windows aggregation
# --------------------------------------------------------------------------


def test_windows_union_across_rule_and_derived_expressions():
    text = VALID_SAMPLER.replace(
        'when = "used_pct > 1"', 'when = "avg(used_pct, \\"5m\\") > 1"'
    ) + '\n[[derived]]\nname = "d1"\nexpr = "max(used_pct, \\"10m\\")"\n'
    md = load_text(text)
    assert ("used_pct", 300.0) in md.windows
    assert ("used_pct", 600.0) in md.windows


# --------------------------------------------------------------------------
# malformed TOML itself
# --------------------------------------------------------------------------


def test_malformed_toml_syntax_raises_validation_error():
    with pytest.raises(ValidationError) as ei:
        load_text("schema = 1\n[monitor\n")
    assert ei.value.errors[0]["code"] == "toml_syntax"


def test_load_file_rejects_symlinks(tmp_path):
    real = tmp_path / "real.toml"
    real.write_text(VALID_SAMPLER)
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    with pytest.raises(OSError):
        load_file(link)


def test_load_dir_reports_per_file_errors(tmp_path):
    (tmp_path / "good.toml").write_text(VALID_SAMPLER)
    (tmp_path / "bad.toml").write_text(VALID_SAMPLER.replace("schema = 1\n", ""))
    defs, errors = load_dir(tmp_path)
    assert len(defs) == 1
    assert defs[0].name == "test"
    assert len(errors) == 1
    bad_path, bad_err = errors[0]
    assert bad_path.name == "bad.toml"
    assert isinstance(bad_err, ValidationError)


# --------------------------------------------------------------------------
# lints required of every module in this package
# --------------------------------------------------------------------------


def test_no_direct_time_calls_in_definitions_package():
    """[TS-03] no time.time/time.monotonic/datetime.now/time.sleep anywhere here."""
    offenders = []
    for py in DEFINITIONS_SRC.rglob("*.py"):
        text = py.read_text()
        for needle in ("time.time(", "time.monotonic(", "datetime.now(", "time.sleep("):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == []


_ALLOWED_FTMON_MODULES = ("ftmon.model", "ftmon.expr", "ftmon.paths", "ftmon.sources.base",
                            "ftmon.definitions")


def _imported_ftmon_modules(py: Path) -> set[str]:
    tree = ast.parse(py.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ftmon"):
                    mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("ftmon"):
            mods.add(node.module)
    return mods


def test_definitions_package_only_imports_the_allowed_ftmon_modules():
    """WP3 contract: import only ftmon.{model,expr,paths,sources.base} + stdlib + tomli_w."""
    offenders = []
    for py in DEFINITIONS_SRC.rglob("*.py"):
        for mod in _imported_ftmon_modules(py):
            if mod == "ftmon" or any(
                mod == allowed or mod.startswith(allowed + ".")
                for allowed in _ALLOWED_FTMON_MODULES
            ):
                continue
            offenders.append(f"{py.name}: {mod}")
    assert offenders == []


def test_schema_module_has_no_toml_or_expr_imports():
    """schema.py is pure declarative data + tiny predicates (no compiling/parsing logic)."""
    text = (DEFINITIONS_SRC / "schema.py").read_text()
    assert "tomllib" not in text
    assert "compile_expr" not in text


def test_normalized_toml_is_deterministic_and_hash_matches():
    md1 = load_text(VALID_SAMPLER)
    md2 = load_text(VALID_SAMPLER)
    assert md1.normalized_toml == md2.normalized_toml
    assert md1.content_hash == md2.content_hash
    import hashlib

    assert md1.content_hash == hashlib.sha256(md1.normalized_toml.encode("utf-8")).hexdigest()

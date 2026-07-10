"""[EX-01][EX-02][EX-05][EX-07] Parse-time allowlist and name resolution."""

import pytest

from ftmon.expr import (
    ExprError,
    ExprNameError,
    ExprSyntaxError,
    NameEnv,
    compile_expr,
    parse_duration,
)

ENV = NameEnv(
    metrics=frozenset({"rss_bytes", "cpu_pct", "used_bytes", "free_bytes"}),
    attrs=frozenset({"name", "fstype"}),
    params=frozenset({"warn_pct"}),
)

FORBIDDEN = [
    # [EX-01] every forbidden construct class, enumerated
    "().__class__",                       # Attribute
    "x.__dict__",                         # Attribute
    "cpu_pct[0]",                         # Subscript
    "[v for v in [1]]",                   # comprehension
    "lambda: 1",                          # lambda
    'f"{cpu_pct}"',                       # JoinedStr (f-string)
    "(x := 5)",                           # walrus
    "{1: 2}",                             # Dict
    "{1, 2}",                             # Set
    "cpu_pct ** 2",                       # Pow not in _BIN_OPS
    "cpu_pct // 2",                       # FloorDiv
    "cpu_pct << 1",                       # shift
    "cpu_pct | 1",                        # bitor
    "~cpu_pct",                           # Invert
    "cpu_pct is None",                    # Is
    "print(1)",                           # unknown function
    "avg(cpu_pct, w='5m')",               # [EX-05] keyword args
    "b'raw'",                             # bytes literal
]


@pytest.mark.parametrize("text", FORBIDDEN)
def test_forbidden_constructs_rejected(text):
    with pytest.raises(ExprError):
        compile_expr(text, ENV)


def test_unknown_name_carries_candidates():
    """[EX-02][MD-04] unresolvable names fail with candidates for suggestions."""
    with pytest.raises(ExprNameError) as ei:
        compile_expr("rss_byte > 5", ENV)
    assert ei.value.name == "rss_byte"
    assert "rss_bytes" in ei.value.candidates


def test_constants_resolve():
    """[EX-02] unit multipliers and severity levels are constants."""
    e = compile_expr("32 * MB > 1 * KB and error > warning", ENV)
    assert e.source


def test_series_args_validated():
    """[CA-01] metric-name and duration-literal argument kinds."""
    with pytest.raises(ExprSyntaxError):
        compile_expr('avg("cpu_pct", "5m")', ENV)  # string, not metric name
    with pytest.raises(ExprSyntaxError):
        compile_expr("avg(cpu_pct, 300)", ENV)  # number, not duration string
    with pytest.raises(ExprSyntaxError):
        compile_expr('avg(cpu_pct, "5x")', ENV)  # bad unit
    with pytest.raises(ExprSyntaxError):
        compile_expr('avg(cpu_pct, "7h")', ENV)  # [CA-04] window > 6h
    with pytest.raises(ExprSyntaxError):
        compile_expr("avg(name, '5m')", ENV)  # attr where metric required


def test_regex_validation():
    """[EX-07] invalid regex and oversize patterns rejected at compile."""
    with pytest.raises(ExprSyntaxError):
        compile_expr('matches(name, "(unclosed")', ENV)
    with pytest.raises(ExprSyntaxError):
        compile_expr(f'matches(name, "{"a" * 600}")', ENV)
    compile_expr('matches(name, "^(firefox|chrome)$")', ENV)  # valid ok


def test_windows_collected_for_ring_sizing():
    """[CA-04] compiled expression reports referenced (metric, window) pairs."""
    e = compile_expr('slope(rss_bytes, "45m") > 0 and avg(cpu_pct, "5m") > 1', ENV)
    assert ("rss_bytes", 2700.0) in e.windows
    assert ("cpu_pct", 300.0) in e.windows


def test_parse_duration():
    assert parse_duration("90s") == 90.0
    assert parse_duration("10m") == 600.0
    assert parse_duration("3h") == 10800.0
    with pytest.raises(ExprSyntaxError):
        parse_duration("10 m")


def test_builtin_definition_expressions_compile():
    """[MD-07] spot-check: expressions from design/builtins compile as written."""
    disk_env = NameEnv(
        metrics=frozenset({"used_bytes", "free_bytes", "used_pct", "inode_used_pct",
                           "filling", "full_in_h"}),
        attrs=frozenset({"fstype"}),
        params=frozenset({"space_warn_pct", "filling_frac"}),
    )
    for text in [
        'monot(used_bytes, "70m")',
        'free_bytes / clamp(slope(used_bytes, "70m") * 3600, 1, 1000000000000000)',
        "used_pct > space_warn_pct",
        'matches(fstype, "^(tmpfs|iso9660|squashfs|overlay)$")',
        "filling >= filling_frac and used_pct > 50",
    ]:
        compile_expr(text, disk_env)
    ev_env = NameEnv(metrics=frozenset({"severity"}),
                     attrs=frozenset({"provider", "event_id", "message", "source"}))
    compile_expr('severity >= error and not matches(provider, "^(tracker-|gnome-shell$)")', ev_env)
    compile_expr('provider == "kernel" and contains(message, "Out of memory")', ev_env)

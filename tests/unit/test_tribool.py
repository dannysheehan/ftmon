"""[EX-06] Three-valued semantics - the SPEC truth table, verbatim, plus
[CA-02] insufficient-data propagation through whole expressions."""

import pytest

from ftmon.expr import NameEnv, compile_expr
from ftmon.expr.tribool import TriBool, to_tribool
from tests.conftest import FakeCtx

ENV = NameEnv(metrics=frozenset({"x", "y"}), params=frozenset({"p"}))


def ev(text, **series):
    ctx = FakeCtx(
        series={k: [(1_700_000_000.0, float(v))] for k, v in series.items()},
        params={"p": 5.0},
    )
    return compile_expr(text, ENV).eval(ctx)


# x is present (=1.0) where needed; y is always missing (None / unknown).

TRUTH_TABLE = [
    # [EX-06] arithmetic with ? operand -> ?
    ("y + 1", None),
    ("1 - y", None),
    ("y * 2", None),
    # [EX-06] comparison with ? -> ?
    ("y == 1", None),
    ("y != 1", None),
    ("y < 1", None),
    ("y in [1, 2]", None),
    # [EX-06] chained comparison containing ? -> ?
    ("0 < y < 2", None),
    ("3 < 2 < y", None),  # even when an earlier link is false
    # [EX-06] not ? -> ?
    ("not y", None),
    # [EX-06] and/or rows
    ("y > 0 and False", False),
    ("False and y > 0", False),
    ("y > 0 and True", None),
    ("True and y > 0", None),
    ("y > 0 or True", True),
    ("True or y > 0", True),
    ("y > 0 or False", None),
    ("False or y > 0", None),
    # [EX-06] division/modulo by zero -> ?
    ("1 / 0", None),
    ("1 % 0", None),
    ("pct(1, 0)", None),
    # [EX-06] coalesce(?, d) -> d
    ("coalesce(y, 42)", 42),
    # [EX-06] IfExp with ? condition -> ?
    ("1 if y > 0 else 2", None),
    # sanity: known values behave classically
    ("x == 1", True),
    ("x > 0 and x < 2", True),
    ("not (x > 0)", False),
    ("x + 1", 2.0),
]


@pytest.mark.parametrize("text,expected", TRUTH_TABLE)
def test_truth_table(text, expected):
    assert ev(text, x=1) == expected


def test_rule_fires_only_on_exact_true():
    """[EX-06] a rule fires iff when is exactly True; [IN-01] feeding."""
    assert to_tribool(True) is TriBool.TRUE
    assert to_tribool(None) is TriBool.UNKNOWN
    assert to_tribool(False) is TriBool.FALSE
    assert to_tribool(5.0) is TriBool.FALSE  # numbers are not True
    assert to_tribool("x") is TriBool.FALSE


def test_cross_type_ordering_is_unknown_not_crash():
    """[EX-06] no expression evaluation ever raises."""
    ctx = FakeCtx(series={"x": [(0, 1.0)]}, attrs={"name": "leaky"})
    env = NameEnv(metrics=frozenset({"x"}), attrs=frozenset({"name"}))
    assert compile_expr("name < 5", env).eval(ctx) is None
    assert compile_expr("name == 5", env).eval(ctx) is False  # == across types is defined


def test_nan_and_inf_become_unknown():
    """[EX-06] float NaN/inf results -> ?"""
    assert ev("x * 1e308 * 1e308", x=1) is None  # inf
    assert ev("(x - x) / 1", x=1) == 0.0

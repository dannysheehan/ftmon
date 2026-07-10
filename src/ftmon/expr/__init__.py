"""Restricted expression language (SPEC section 8.2, EX-01..07).

Security boundary: this package imports ONLY the Python standard library
(EX-04, enforced by a lint test). Public API:

    compile_expr(text, names) -> CompiledExpr     may raise ExprError
    CompiledExpr.eval(ctx)    -> value | None     never raises
    parse_duration("5m")      -> seconds

Unknown/missing data is represented as Python None and propagates by the
EX-06 three-valued truth table (tribool module).
"""

from ftmon.expr.eval import CompiledExpr, EvalContext
from ftmon.expr.parse import (
    CONSTANTS,
    ExprError,
    ExprNameError,
    ExprSyntaxError,
    NameEnv,
    compile_expr,
    parse_duration,
)
from ftmon.expr.tribool import TriBool, to_tribool

__all__ = [
    "compile_expr",
    "CompiledExpr",
    "EvalContext",
    "NameEnv",
    "ExprError",
    "ExprSyntaxError",
    "ExprNameError",
    "CONSTANTS",
    "parse_duration",
    "TriBool",
    "to_tribool",
]

"""Compile expression text to IR under the EX-01 node allowlist.

All rejection happens here, at definition-validation time; eval never raises.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from ftmon.expr import ir
from ftmon.expr.eval import CompiledExpr

__all__ = [
    "ExprError",
    "ExprSyntaxError",
    "ExprNameError",
    "NameEnv",
    "CONSTANTS",
    "compile_expr",
    "parse_duration",
    "MAX_WINDOW_S",
    "MAX_REGEX_LEN",
]

# EX-02 language constants (in addition to None/True/False literals)
CONSTANTS: dict[str, int] = {
    "KB": 2**10,
    "MB": 2**20,
    "GB": 2**30,
    "TB": 2**40,
    "info": 0,
    "notice": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}

MAX_WINDOW_S = 6 * 3600  # CA-04
MAX_REGEX_LEN = 512  # EX-07

# CA-01 function table: argument kinds are
#   m  = metric name (bare Name resolving to a metric)
#   w  = duration string literal ("5m")
#   e  = sub-expression
#   re = regex string literal
#   tw = "HH:MM-HH:MM" string literal
_SERIES = {"avg", "min", "max", "delta", "rate", "slope", "monot", "coverage"}
FUNCS: dict[str, tuple[str, ...]] = {
    "last": ("m",),
    "avg": ("m", "w"),
    "min": ("m", "w"),
    "max": ("m", "w"),
    "delta": ("m", "w"),
    "rate": ("m", "w"),
    "slope": ("m", "w"),
    "monot": ("m", "w"),
    "coverage": ("m", "w"),
    "age": ("m",),
    "baseline": ("m",),
    "pct": ("e", "e"),
    "abs": ("e",),
    "roundv": ("e", "e"),
    "clamp": ("e", "e", "e"),
    "coalesce": ("e", "e"),
    "matches": ("e", "re"),
    "contains": ("e", "e"),
    "during": ("tw",),
    "dow": (),
}

_CMP_OPS = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.NotIn: "not in",
}
_BIN_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h|d)$")
_TIMEWINDOW_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


class ExprError(ValueError):
    """Base for all compile-time expression errors."""


class ExprSyntaxError(ExprError):
    def __init__(self, message: str, fragment: str = ""):
        self.fragment = fragment
        super().__init__(f"{message}: {fragment!r}" if fragment else message)


class ExprNameError(ExprError):
    def __init__(self, name: str, candidates: tuple[str, ...] = ()):
        self.name = name
        self.candidates = candidates
        super().__init__(f"unknown name {name!r}")


def parse_duration(text: str) -> float:
    """'90s' | '10m' | '3h' | '2d' -> seconds. Raises ExprSyntaxError."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ExprSyntaxError("invalid duration", text)
    return float(m.group(1)) * _UNITS[m.group(2)]


@dataclass(frozen=True)
class NameEnv:
    """Names visible to an expression (EX-02), built from SourceDecl + parameters."""

    metrics: frozenset[str] = frozenset()
    attrs: frozenset[str] = frozenset()
    params: frozenset[str] = frozenset()

    def all_names(self) -> tuple[str, ...]:
        return tuple(self.metrics | self.attrs | self.params | set(CONSTANTS))


@dataclass
class _Builder:
    src: str
    names: NameEnv
    windows: list[tuple[str, float]] = field(default_factory=list)

    def frag(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.src, node) or type(node).__name__

    def build(self, node: ast.AST) -> ir.Node:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                return ir.Lit(node.value)
            raise ExprSyntaxError("unsupported literal", self.frag(node))
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.BoolOp):
            items = tuple(self.build(v) for v in node.values)
            return ir.AndOp(items) if isinstance(node.op, ast.And) else ir.OrOp(items)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return ir.NotOp(self.build(node.operand))
            if isinstance(node.op, ast.USub):
                return ir.NegOp(self.build(node.operand))
            raise ExprSyntaxError("operator not allowed", self.frag(node))
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ExprSyntaxError("operator not allowed", self.frag(node))
            return ir.Bin(op, self.build(node.left), self.build(node.right))
        if isinstance(node, ast.Compare):
            ops = []
            for o in node.ops:
                sym = _CMP_OPS.get(type(o))
                if sym is None:
                    raise ExprSyntaxError("comparison not allowed", self.frag(node))
                ops.append(sym)
            operands = (self.build(node.left), *(self.build(c) for c in node.comparators))
            return ir.Cmp(operands, tuple(ops))
        if isinstance(node, (ast.List, ast.Tuple)):
            return ir.ListLit(tuple(self.build(e) for e in node.elts))
        if isinstance(node, ast.IfExp):
            return ir.IfExp(self.build(node.test), self.build(node.body), self.build(node.orelse))
        if isinstance(node, ast.Call):
            return self._call(node)
        # everything else: Attribute, Subscript, comprehensions, lambda,
        # JoinedStr (f-string), NamedExpr (walrus), Starred, Dict, ...
        raise ExprSyntaxError("construct not allowed", self.frag(node))

    def _name(self, node: ast.Name) -> ir.Node:
        n = node.id
        if n in self.names.metrics:
            return ir.Ref("metric", n)
        if n in self.names.attrs:
            return ir.Ref("attr", n)
        if n in self.names.params:
            return ir.Ref("param", n)
        if n in CONSTANTS:
            return ir.Ref("const", n, CONSTANTS[n])
        raise ExprNameError(n, self.names.all_names())

    def _call(self, node: ast.Call) -> ir.Node:
        if not isinstance(node.func, ast.Name):
            raise ExprSyntaxError("only bare function names may be called", self.frag(node))
        fn = node.func.id
        sig = FUNCS.get(fn)
        if sig is None:
            raise ExprNameError(fn, tuple(FUNCS))
        if node.keywords:
            raise ExprSyntaxError("keyword arguments are not allowed (EX-05)", self.frag(node))
        if len(node.args) != len(sig):
            raise ExprSyntaxError(
                f"{fn}() takes {len(sig)} argument(s), got {len(node.args)}", self.frag(node)
            )
        args: list[ir.Node] = []
        metric: str | None = None
        window_s: float | None = None
        regex: re.Pattern | None = None
        literal: str | None = None
        for kind, a in zip(sig, node.args, strict=True):
            if kind == "m":
                if not isinstance(a, ast.Name) or a.id not in self.names.metrics:
                    raise ExprSyntaxError(f"{fn}() expects a metric name", self.frag(a))
                metric = a.id
            elif kind == "w":
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                    raise ExprSyntaxError(f"{fn}() window must be a duration string", self.frag(a))
                window_s = parse_duration(a.value)
                if window_s > MAX_WINDOW_S:
                    raise ExprSyntaxError(
                        f"window exceeds maximum {MAX_WINDOW_S}s (CA-04)", a.value
                    )
            elif kind == "re":
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                    raise ExprSyntaxError(
                        "matches() pattern must be a string literal", self.frag(a)
                    )
                if len(a.value) > MAX_REGEX_LEN:
                    raise ExprSyntaxError("regex pattern too long (EX-07)", a.value[:40] + "...")
                try:
                    regex = re.compile(a.value)
                except re.error as e:
                    raise ExprSyntaxError(f"invalid regex ({e})", a.value) from e
            elif kind == "tw":
                if not (
                    isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and _TIMEWINDOW_RE.match(a.value)
                ):
                    raise ExprSyntaxError('during() expects "HH:MM-HH:MM"', self.frag(a))
                literal = a.value
            else:  # "e"
                args.append(self.build(a))
        if fn in _SERIES and metric is not None and window_s is not None:
            self.windows.append((metric, window_s))
        return ir.Call(fn, tuple(args), metric=metric, window_s=window_s, regex=regex,
                       literal=literal)


def compile_expr(text: str, names: NameEnv) -> CompiledExpr:
    """Parse and resolve an expression. Raises ExprError on any problem."""
    if not isinstance(text, str) or not text.strip():
        raise ExprSyntaxError("empty expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ExprSyntaxError(f"syntax error: {e.msg}", text) from e
    b = _Builder(src=text, names=names)
    node = b.build(tree.body)
    return CompiledExpr(node=node, windows=tuple(sorted(set(b.windows))), source=text)

"""IR interpreter. CompiledExpr.eval never raises (EX-03/EX-06).

Deadlines are cooperative: the caller may supply deadline_check() which is
consulted every _CHECK_EVERY nodes; direct clock access is forbidden here
(TS-03) and unnecessary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ftmon.expr import functions as fx
from ftmon.expr import ir
from ftmon.expr.tribool import clean_number, tri_not

__all__ = ["EvalContext", "CompiledExpr"]

_CHECK_EVERY = 64


class EvalContext(Protocol):
    """Data access for one entity's evaluation. All lookups may return None."""

    def metric_last(self, m: str) -> float | None: ...
    def metric_last_ts(self, m: str) -> float | None: ...
    def metric_window(self, m: str, seconds: float) -> Sequence[tuple[float, float]]: ...
    def attr(self, a: str) -> str | None: ...
    def param(self, p: str) -> float: ...
    def baseline(self, m: str) -> float | None: ...
    def now(self) -> float: ...


class _Deadline(Exception):
    pass


@dataclass
class _State:
    ctx: EvalContext
    counter: Callable[[str], None]
    deadline_check: Callable[[], bool] | None
    nodes: int = 0

    def tick(self) -> None:
        self.nodes += 1
        if (
            self.deadline_check is not None
            and self.nodes % _CHECK_EVERY == 0
            and self.deadline_check()
        ):
            raise _Deadline


@dataclass(frozen=True)
class CompiledExpr:
    node: ir.Node
    windows: tuple[tuple[str, float], ...]  # (metric, seconds) referenced (CA-04 sizing)
    source: str

    def eval(
        self,
        ctx: EvalContext,
        deadline_check: Callable[[], bool] | None = None,
        counter: Callable[[str], None] | None = None,
    ) -> object:
        """Returns float | str | bool | tuple | None. NEVER raises."""
        st = _State(ctx=ctx, counter=counter or (lambda _n: None), deadline_check=deadline_check)
        try:
            return _ev(self.node, st)
        except _Deadline:
            st.counter("eval_deadline")
            return None
        except Exception:  # EX-06: no exception ever escapes
            st.counter("eval_error")
            return None


def _truthy(v: object) -> bool:
    return bool(v)


def _ev(node: ir.Node, st: _State) -> object:
    st.tick()
    if isinstance(node, ir.Lit):
        return node.value
    if isinstance(node, ir.Ref):
        if node.kind == "metric":
            return st.ctx.metric_last(node.name)
        if node.kind == "attr":
            return st.ctx.attr(node.name)
        if node.kind == "param":
            return st.ctx.param(node.name)
        return node.const_value
    if isinstance(node, ir.NotOp):
        return tri_not(_ev(node.operand, st))
    if isinstance(node, ir.NegOp):
        v = _ev(node.operand, st)
        if not _is_num(v):
            return None
        return clean_number(-v)
    if isinstance(node, ir.AndOp):
        unknown = False
        for item in node.items:
            v = _ev(item, st)
            if v is None:
                unknown = True
            elif not _truthy(v):
                return False  # short-circuit: False and <anything> is False
        return None if unknown else True
    if isinstance(node, ir.OrOp):
        unknown = False
        for item in node.items:
            v = _ev(item, st)
            if v is None:
                unknown = True
            elif _truthy(v):
                return True  # short-circuit: True or <anything> is True
        return None if unknown else False
    if isinstance(node, ir.Bin):
        return _bin(node, st)
    if isinstance(node, ir.Cmp):
        return _cmp(node, st)
    if isinstance(node, ir.ListLit):
        return tuple(_ev(i, st) for i in node.items)
    if isinstance(node, ir.IfExp):
        t = _ev(node.test, st)
        if t is None:
            return None
        return _ev(node.body, st) if _truthy(t) else _ev(node.orelse, st)
    if isinstance(node, ir.Call):
        return _call(node, st)
    return None  # unreachable for well-formed IR


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _bin(node: ir.Bin, st: _State) -> object:
    left = _ev(node.left, st)
    right = _ev(node.right, st)
    if not (_is_num(left) and _is_num(right)):
        return None  # includes None operands (EX-06)
    try:
        if node.op == "+":
            r = left + right
        elif node.op == "-":
            r = left - right
        elif node.op == "*":
            r = left * right
        elif node.op == "/":
            r = left / right
        else:
            r = left % right
    except ZeroDivisionError:
        st.counter("div_zero")
        return None
    except OverflowError:
        return None
    return clean_number(r)


def _cmp(node: ir.Cmp, st: _State) -> bool | None:
    values = [_ev(o, st) for o in node.operands]
    if any(v is None for v in values):
        return None  # EX-06: any chained comparison containing ? is ?
    left = values[0]
    for op, right in zip(node.ops, values[1:], strict=True):
        try:
            if op == "==":
                ok = left == right
            elif op == "!=":
                ok = left != right
            elif op == "in":
                ok = _membership(left, right)
            elif op == "not in":
                m = _membership(left, right)
                ok = None if m is None else not m
            else:
                if type(left) is str and type(right) is str:
                    pass  # string ordering is fine
                elif not (_is_num(left) and _is_num(right)):
                    return None  # cross-type ordering is unknown, never a crash
                ok = {"<": left < right, "<=": left <= right,
                      ">": left > right, ">=": left >= right}[op]
        except TypeError:
            return None
        if ok is None:
            return None
        if not ok:
            return False
        left = right
    return True


def _membership(needle: object, haystack: object) -> bool | None:
    if not isinstance(haystack, tuple):
        return None
    if any(h is None for h in haystack):
        return None
    return needle in haystack


def _call(node: ir.Call, st: _State) -> object:
    fn = node.fn
    ctx = st.ctx
    if node.metric is not None and node.window_s is not None:
        pts = ctx.metric_window(node.metric, node.window_s)
        if fn == "avg":
            return fx.f_avg(pts)
        if fn == "min":
            return fx.f_min(pts)
        if fn == "max":
            return fx.f_max(pts)
        if fn == "delta":
            return fx.f_delta(pts)
        if fn == "rate":
            return fx.f_rate(pts, st.counter)
        if fn == "slope":
            return fx.f_slope(pts)
        if fn == "monot":
            return fx.f_monot(pts)
    if fn == "last":
        return ctx.metric_last(node.metric)
    if fn == "age":
        ts = ctx.metric_last_ts(node.metric)
        return None if ts is None else max(0.0, ctx.now() - ts)
    if fn == "baseline":
        return ctx.baseline(node.metric)
    if fn == "pct":
        return fx.f_pct(_ev(node.args[0], st), _ev(node.args[1], st), st.counter)
    if fn == "abs":
        return fx.f_abs(_ev(node.args[0], st))
    if fn == "roundv":
        return fx.f_roundv(_ev(node.args[0], st), _ev(node.args[1], st))
    if fn == "clamp":
        return fx.f_clamp(_ev(node.args[0], st), _ev(node.args[1], st), _ev(node.args[2], st))
    if fn == "coalesce":
        return fx.f_coalesce(_ev(node.args[0], st), _ev(node.args[1], st))
    if fn == "matches":
        s = _ev(node.args[0], st)
        if not isinstance(s, str) or node.regex is None:
            return None
        return bool(node.regex.search(s))
    if fn == "contains":
        return fx.f_contains(_ev(node.args[0], st), _ev(node.args[1], st))
    if fn == "during":
        return fx.f_during(node.literal or "", ctx.now())
    if fn == "dow":
        return fx.f_dow(ctx.now())
    return None

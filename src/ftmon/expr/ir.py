"""Private IR for compiled expressions. Built by parse.py, walked by eval.py.

The evaluator never touches ast nodes; author-controlled strings are resolved
to typed slots at compile time (EX-02).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Union

Node = Union[
    "Lit", "Ref", "NotOp", "NegOp", "AndOp", "OrOp", "Bin", "Cmp", "ListLit", "IfExp", "Call"
]


@dataclass(frozen=True)
class Lit:
    value: object  # int | float | str | bool | None


@dataclass(frozen=True)
class Ref:
    kind: Literal["metric", "attr", "param", "const"]
    name: str
    const_value: object = None


@dataclass(frozen=True)
class NotOp:
    operand: Node


@dataclass(frozen=True)
class NegOp:
    operand: Node


@dataclass(frozen=True)
class AndOp:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class OrOp:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Bin:
    op: Literal["+", "-", "*", "/", "%"]
    left: Node
    right: Node


@dataclass(frozen=True)
class Cmp:
    # ops[i] applies between operands[i] and operands[i+1] (chained comparison)
    operands: tuple[Node, ...]
    ops: tuple[Literal["==", "!=", "<", "<=", ">", ">=", "in", "not in"], ...]


@dataclass(frozen=True)
class ListLit:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class IfExp:
    test: Node
    body: Node
    orelse: Node


@dataclass(frozen=True)
class Call:
    fn: str
    args: tuple[Node, ...] = ()
    metric: str | None = None  # for series functions
    window_s: float | None = None  # for series functions
    regex: re.Pattern | None = None  # for matches()
    literal: str | None = None  # for during()

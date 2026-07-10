"""Three-valued (Kleene-style) logic helpers implementing SPEC EX-06 verbatim.

Unknown is represented as Python None throughout the expression language.
A rule fires iff its `when` evaluates to exactly True.
"""

from __future__ import annotations

import math
from enum import Enum

__all__ = ["TriBool", "to_tribool", "tri_not", "clean_number"]


class TriBool(Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def to_tribool(value: object) -> TriBool:
    """EX-06: a rule fires iff the result is exactly True; None is UNKNOWN;
    anything else (False, numbers, strings) is FALSE."""
    if value is True:
        return TriBool.TRUE
    if value is None:
        return TriBool.UNKNOWN
    return TriBool.FALSE


def tri_not(value: object) -> bool | None:
    if value is None:
        return None
    return not bool(value)


def clean_number(value: object) -> object:
    """EX-06: any float result that is NaN or +/-inf becomes None."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value

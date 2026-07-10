"""CA-01 function library: series math and helpers. Pure, stdlib-only.

Every function returns None on insufficient data (CA-02). Series points are
sequences of (ts, value) pairs, oldest first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from ftmon.expr.tribool import clean_number

Points = Sequence[tuple[float, float]]
Counter = Callable[[str], None]

_DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def f_avg(pts: Points) -> float | None:
    if not pts:
        return None
    return clean_number(sum(v for _, v in pts) / len(pts))


def f_min(pts: Points) -> float | None:
    return min((v for _, v in pts), default=None)


def f_max(pts: Points) -> float | None:
    return max((v for _, v in pts), default=None)


def f_delta(pts: Points) -> float | None:
    if len(pts) < 2:
        return None
    return clean_number(pts[-1][1] - pts[0][1])


def f_rate(pts: Points, counter: Counter) -> float | None:
    """Per-second rate; counter resets (negative delta) -> 0.0 (CA-03)."""
    if len(pts) < 2:
        return None
    dt = pts[-1][0] - pts[0][0]
    if dt <= 0:
        return None
    dv = pts[-1][1] - pts[0][1]
    if dv < 0:
        counter("counter_reset")
        return 0.0
    return clean_number(dv / dt)


def f_slope(pts: Points) -> float | None:
    """Least-squares slope in units/second; None with < 3 points."""
    n = len(pts)
    if n < 3:
        return None
    t0 = pts[0][0]
    xs = [t - t0 for t, _ in pts]
    ys = [v for _, v in pts]
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return clean_number((n * sxy - sx * sy) / denom)


def f_monot(pts: Points) -> float | None:
    """Fraction of consecutive deltas > 0 (0.0-1.0) - the legacy Filling test."""
    if len(pts) < 2:
        return None
    ups = sum(1 for a, b in zip(pts, pts[1:], strict=False) if b[1] > a[1])
    return ups / (len(pts) - 1)


def f_pct(a: object, b: object, counter: Counter) -> float | None:
    if a is None or b is None or not _num(a) or not _num(b):
        return None
    if b == 0:
        counter("div_zero")
        return None
    return clean_number(100.0 * a / b)


def f_abs(x: object) -> float | None:
    return clean_number(abs(x)) if _num(x) else None


def f_roundv(x: object, n: object) -> float | None:
    if not _num(x) or not _num(n):
        return None
    return clean_number(round(x, int(n)))


def f_clamp(x: object, lo: object, hi: object) -> float | None:
    if not (_num(x) and _num(lo) and _num(hi)):
        return None
    return clean_number(min(max(x, lo), hi))


def f_coalesce(x: object, default: object) -> object:
    return default if x is None else x


def f_contains(s: object, sub: object) -> bool | None:
    if not isinstance(s, str) or not isinstance(sub, str):
        return None
    return sub in s


def f_during(literal: str, now: float) -> bool:
    """Local-time window test; window may wrap midnight."""
    start_s, end_s = literal.split("-")
    t = datetime.fromtimestamp(now)
    minutes = t.hour * 60 + t.minute
    sh, sm = int(start_s[:2]), int(start_s[3:])
    eh, em = int(end_s[:2]), int(end_s[3:])
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def f_dow(now: float) -> str:
    return _DOW[datetime.fromtimestamp(now).weekday()]


def _num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)

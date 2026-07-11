"""Strict FTMON JSON check output adapter (EC-10)."""

from __future__ import annotations

import json
import math

from ftmon.checks.model import RawCheckResult, unknown
from ftmon.checks.text import clean_message

_TOP_KEYS = {"schema", "state", "message", "metrics"}
_ASCII_WS = b" \t\r\n"


class _DuplicateKey(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            # json.loads normally keeps the last value, which would let a
            # check make parser behavior depend on duplicate-key policy.
            raise _DuplicateKey(key)
        result[key] = value
    return result


def parse(stdout: bytes, duration_s: float) -> RawCheckResult:
    try:
        text = stdout.strip(_ASCII_WS).decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey):
        return unknown(duration_s, "protocol")
    if type(payload) is not dict or set(payload) != _TOP_KEYS:
        return unknown(duration_s, "protocol")
    state = payload["state"]
    message = payload["message"]
    metrics = payload["metrics"]
    if payload["schema"] != 1 or type(state) is not int or state not in range(4):
        return unknown(duration_s, "protocol")
    if type(message) is not str or type(metrics) is not dict or len(metrics) > 64:
        return unknown(duration_s, "protocol")
    values: dict[str, tuple[float, str]] = {}
    for label, metric in metrics.items():
        if type(label) is not str or not label or type(metric) is not dict:
            return unknown(duration_s, "protocol")
        if set(metric) != {"value", "uom"} or type(metric["uom"]) is not str:
            return unknown(duration_s, "protocol")
        value = metric["value"]
        if type(value) not in (int, float) or not math.isfinite(value):
            return unknown(duration_s, "protocol")
        values[label] = (float(value), metric["uom"])
    return RawCheckResult(state, clean_message(message), duration_s, values)

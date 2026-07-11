"""Nagios plugin output adapter (EC-03)."""

from __future__ import annotations

import math
import re

from ftmon.checks.model import RawCheckResult, unknown
from ftmon.checks.text import clean_message

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_VALUE = re.compile(rf"^(?P<number>{_NUMBER})(?P<uom>[^;\s]*)$")


def _valid_range(value: str) -> bool:
    value = value.removeprefix("@")
    if ":" not in value:
        return re.fullmatch(_NUMBER, value) is not None
    if value.count(":") != 1:
        return False
    start, end = value.split(":")
    return (
        (not start or start == "~" or re.fullmatch(_NUMBER, start) is not None)
        and (not end or re.fullmatch(_NUMBER, end) is not None)
    )


def _tokens(perfdata: str) -> list[tuple[str, str]] | None:
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(perfdata):
        while index < len(perfdata) and perfdata[index].isspace():
            index += 1
        if index == len(perfdata):
            break
        if perfdata[index] == "'":
            end = perfdata.find("'", index + 1)
            if end < 0:
                return None
            label = perfdata[index + 1 : end]
            index = end + 1
            if index >= len(perfdata) or perfdata[index] != "=":
                return None
            index += 1
        else:
            equal = perfdata.find("=", index)
            if equal < 0 or any(char.isspace() for char in perfdata[index:equal]):
                return None
            label = perfdata[index:equal]
            index = equal + 1
        end = index
        while end < len(perfdata) and not perfdata[end].isspace():
            end += 1
        if not label or end == index:
            return None
        result.append((label, perfdata[index:end]))
        index = end
    return result


def parse(stdout: bytes, exit_code: int, duration_s: float) -> RawCheckResult:
    if exit_code not in range(4):
        return unknown(duration_s, "exit_status")
    try:
        first_line = stdout.splitlines()[0].decode("utf-8") if stdout else ""
    except UnicodeDecodeError:
        return unknown(duration_s, "protocol")
    summary, separator, perfdata = first_line.partition("|")
    values: dict[str, tuple[float, str]] = {}
    ambiguous: set[str] = set()
    if separator:
        tokens = _tokens(perfdata)
        if tokens is None:
            return unknown(duration_s, "protocol", clean_message(summary))
        for label, raw_value in tokens:
            fields = raw_value.split(";")
            if len(fields) > 5:
                continue
            match = _VALUE.fullmatch(fields[0])
            if match is None:
                continue
            warn_crit = fields[1:3]
            minimum_maximum = fields[3:5]
            if any(value and not _valid_range(value) for value in warn_crit):
                continue
            if any(value and re.fullmatch(_NUMBER, value) is None
                   for value in minimum_maximum):
                continue
            value = float(match.group("number"))
            if not math.isfinite(value):
                continue
            if label in values:
                ambiguous.add(label)
            else:
                values[label] = (value, match.group("uom"))
        for label in ambiguous:
            values.pop(label, None)
    return RawCheckResult(exit_code, clean_message(summary), duration_s, values)

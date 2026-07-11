"""Protocol-neutral external check values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class CheckSpec:
    alias: str
    argv: tuple[str, ...]
    protocol: Literal["nagios", "ftmon-json"]
    timeout_s: float


@dataclass(frozen=True)
class RawCheckResult:
    state: int
    message: str
    duration_s: float
    values: Mapping[str, tuple[float, str]]
    failure: str | None = None

    def __post_init__(self) -> None:
        # A result is shared by definitions; a caller must not be able to mutate
        # the raw value cache while another definition projects it.
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


def unknown(
    duration_s: float, failure: str, message: str = "External check failed"
) -> RawCheckResult:
    return RawCheckResult(3, message, duration_s, {}, failure)

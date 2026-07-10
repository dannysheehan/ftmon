"""Declarative key inventory for monitor definitions (DESIGN.md section 7, MD-01).

This module is the single source of truth for "what keys exist, what they
look like, and where they are allowed" (MD-01: `ftmon check`, `define_monitor`,
and the daemon all share this table). It holds no TOML-parsing or
expression-compiling logic -- that orchestration lives in `loader.py`; this
module only has plain data and small, pure predicate/normalization helpers.
"""

from __future__ import annotations

import keyword
import re

__all__ = [
    "TOP_LEVEL_KEYS",
    "MONITOR_KEYS",
    "MONITOR_REQUIRED",
    "RULE_KEYS_COMMON",
    "RULE_KEYS_SAMPLER",
    "RULE_KEYS_EVENT",
    "RULE_REQUIRED",
    "DERIVED_KEYS",
    "PARAM_KEYS",
    "PLATFORMS",
    "RULE_SEVERITIES",
    "NAME_RE",
    "ID_RE",
    "WATCHLIST_ENTRY_KEYS",
    "WATCHLIST_TARGET_KEYS",
    "SOURCE_OPTIONS_WATCHLIST_SOURCES",
    "SOURCE_OPTIONS_TOPN_SOURCES",
    "TOP_N_MIN",
    "TOP_N_MAX",
    "TOP_N_DEFAULT",
    "CONFIRM_CYCLES_MIN",
    "CONFIRM_CYCLES_MAX",
    "MESSAGE_MAX_LEN",
    "DESCRIPTION_MAX_LEN",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_CLEAR_AFTER_S",
    "MIN_INTERVAL_S",
    "MAX_POINTS",
    "is_identifier",
    "valid_name",
    "valid_id",
]

# --- schema.name (monitor.name) / rule id syntax (DESIGN section 7 table) ---
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")

PLATFORMS = frozenset({"linux", "windows", "darwin"})

# info is not a valid rule severity (only notice..critical, per WP3 contract)
RULE_SEVERITIES = ("notice", "warning", "error", "critical")

# --- top-level keys ---
TOP_LEVEL_KEYS = frozenset(
    {"schema", "monitor", "source_options", "parameters", "promotion", "derived", "exempt", "rule"}
)

# --- [monitor] ---
MONITOR_KEYS = frozenset(
    {"name", "description", "version", "enabled", "platforms", "interval", "source"}
)
MONITOR_REQUIRED = frozenset({"name", "description", "version", "source", "platforms"})
DESCRIPTION_MAX_LEN = 200
MIN_INTERVAL_S = 15.0  # SA-01

# --- [source_options] ---
SOURCE_OPTIONS_WATCHLIST_SOURCES = frozenset({"unit", "net"})
SOURCE_OPTIONS_TOPN_SOURCES = frozenset({"process"})
WATCHLIST_TARGET_KEYS = frozenset({"unit", "process", "listen"})
WATCHLIST_ENTRY_KEYS = WATCHLIST_TARGET_KEYS | {"during", "expected"}
TOP_N_MIN = 5
TOP_N_MAX = 50
TOP_N_DEFAULT = 15

# --- [parameters] ---
PARAM_KEYS = frozenset({"value", "doc"})

# --- [[derived]] ---
DERIVED_KEYS = frozenset({"name", "expr"})

# --- [[rule]] ---
RULE_KEYS_COMMON = frozenset(
    {"id", "group", "when", "severity", "message", "action", "notify_recovery"}
)
RULE_KEYS_SAMPLER = frozenset({"confirm_cycles", "clear_cycles"})
RULE_KEYS_EVENT = frozenset({"cooldown", "clear_after", "confirm_count", "confirm_window"})
RULE_REQUIRED = frozenset({"id", "when", "severity", "message"})

CONFIRM_CYCLES_MIN = 1
CONFIRM_CYCLES_MAX = 60
MESSAGE_MAX_LEN = 200
DEFAULT_COOLDOWN_S = 600.0  # "10m"
DEFAULT_CLEAR_AFTER_S = 1800.0  # "30m"

MAX_POINTS = 10_000  # CA-04


def is_identifier(name: object) -> bool:
    """Bare-name check used for parameter and derived-metric names."""
    return isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name)


def valid_name(name: object) -> bool:
    """monitor.name syntax: `[a-z][a-z0-9_]{1,31}`."""
    return isinstance(name, str) and bool(NAME_RE.match(name))


def valid_id(value: object) -> bool:
    """rule.id / rule.group syntax: `[a-z0-9-]{1,32}`."""
    return isinstance(value, str) and bool(ID_RE.match(value))

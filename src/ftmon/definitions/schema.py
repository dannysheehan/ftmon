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

from ftmon import model

__all__ = [
    "TOP_LEVEL_KEYS",
    "MONITOR_KEYS",
    "MONITOR_REQUIRED",
    "RULE_KEYS_COMMON",
    "RULE_KEYS_SAMPLER",
    "RULE_KEYS_EVENT",
    "RULE_REQUIRED",
    "DERIVED_KEYS",
    "GLANCE_KEYS",
    "GLANCE_THRESHOLD_KEYS",
    "GLANCE_AGGREGATES",
    "MAX_GLANCE_THRESHOLDS",
    "TREND_KEYS",
    "PARAM_KEYS",
    "PLATFORMS",
    "RULE_SEVERITIES",
    "NAME_RE",
    "ID_RE",
    "WATCHLIST_ENTRY_KEYS",
    "WATCHLIST_TARGET_KEYS",
    "SOURCE_OPTIONS_WATCHLIST_SOURCES",
    "SOURCE_OPTIONS_TOPN_SOURCES",
    "EXTERNAL_SOURCE_OPTIONS_KEYS",
    "PERFDATA_KEYS",
    "MAX_PERFDATA_MAPPINGS",
    "EXTERNAL_ENTITY_MAX_LEN",
    "external_decl",
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
    {
        "schema", "monitor", "source_options", "parameters", "promotion",
        "derived", "exempt", "rule", "trend", "glance",
    }
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
EXTERNAL_SOURCE_OPTIONS_KEYS = frozenset({"check", "entity", "perfdata"})
PERFDATA_KEYS = frozenset({"label", "metric", "plugin_uom", "unit", "kind", "scale"})
MAX_PERFDATA_MAPPINGS = 32  # EC-08 bounds definition-controlled schema growth.
EXTERNAL_ENTITY_MAX_LEN = 256

# --- [parameters] ---
PARAM_KEYS = frozenset({"value", "doc"})

# --- [[derived]] ---
DERIVED_KEYS = frozenset({"name", "expr"})

# --- [glance] (MD-12) ---
GLANCE_KEYS = frozenset({"metric", "unit", "aggregate", "thresholds"})
GLANCE_THRESHOLD_KEYS = frozenset({"label", "parameter"})
GLANCE_AGGREGATES = frozenset({"max", "min"})
MAX_GLANCE_THRESHOLDS = 4

# --- [[trend]] (MD-10) ---
TREND_KEYS = frozenset({
    "id", "kind", "title", "value_metric", "value_unit", "rate_metric", "rate_unit",
    "confidence_metric", "confidence_threshold_param", "remaining_metric",
    "value_threshold_params", "rate_threshold_params", "incident_group",
})

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


def external_decl(perfdata: list[dict]) -> model.SourceDecl:
    """Compose the declaration that expressions see for one external monitor.

    Output labels cannot extend this declaration at runtime: only mappings that
    passed definition validation are supplied here (EC-04/05, MD-11).
    """
    fixed = (
        model.MetricDecl("plugin_state", "state", "gauge", "Plugin state 0..3"),
        model.MetricDecl("plugin_ok", "bool", "gauge", "1 only for plugin state OK"),
        model.MetricDecl("duration_s", "seconds", "gauge", "Check execution duration"),
    )
    mapped = tuple(
        model.MetricDecl(item["metric"], item["unit"], item["kind"],
                         f"Mapped external check value {item['label']!r}")
        for item in perfdata
    )
    return model.SourceDecl(
        name="external", kind="sampler", entity_kind="external",
        metrics=fixed + mapped,
        attrs=(model.AttrDecl("plugin_message", "Sanitized first-line check message"),),
    )


def is_identifier(name: object) -> bool:
    """Bare-name check used for parameter and derived-metric names."""
    return isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name)


def valid_name(name: object) -> bool:
    """monitor.name syntax: `[a-z][a-z0-9_]{1,31}`."""
    return isinstance(name, str) and bool(NAME_RE.match(name))


def valid_id(value: object) -> bool:
    """rule.id / rule.group syntax: `[a-z0-9-]{1,32}`."""
    return isinstance(value, str) and bool(ID_RE.match(value))

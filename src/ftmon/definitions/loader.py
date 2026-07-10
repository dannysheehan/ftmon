"""TOML monitor definitions -> `MonitorDef` (DESIGN.md section 7, MD-01..09).

Pipeline (MD-01's "same validator everywhere"): `tomllib` parse -> schema-table
key check (MD-03) -> `NameEnv` build from `SOURCE_DECLS` (PL-05) -> compile
every expression/template (MD-02/04) -> topo-sort derived metrics (MD-08) ->
aggregate windows (CA-04) -> frozen `MonitorDef` + normalized TOML + SHA-256
content hash (PM-04/07).

Validation collects as many errors as it practically can in one pass rather
than failing fast on the first problem, so a caller (human or AI) gets the
whole picture in one round trip (MC-04's quality bar).
"""

from __future__ import annotations

import difflib
import hashlib
import string
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from ftmon import model
from ftmon.definitions import schema
from ftmon.expr import (
    CompiledExpr,
    ExprNameError,
    ExprSyntaxError,
    NameEnv,
    compile_expr,
    parse_duration,
)
from ftmon.paths import reject_symlink
from ftmon.sources.base import SOURCE_DECLS

__all__ = [
    "RuleDef",
    "MonitorDef",
    "ValidationError",
    "load_text",
    "load_file",
    "load_dir",
]


@dataclass(frozen=True)
class RuleDef:
    id: str
    group: str
    when: CompiledExpr
    severity: int  # 1..4 via model.SEVERITIES index
    confirm_cycles: int
    clear_cycles: int
    message: str  # validated template
    action: str | None
    notify_recovery: bool
    cooldown_s: float | None  # event rules only
    clear_after_s: float | None  # event rules only
    confirm_count: int  # event rules only
    confirm_window_s: float | None  # event rules only


@dataclass(frozen=True)
class MonitorDef:
    name: str
    description: str
    version: int
    enabled: bool
    platforms: tuple[str, ...]
    interval_s: float
    source: str
    source_options: dict
    parameters: dict[str, float]
    promotion: CompiledExpr | None
    derived: tuple[tuple[str, CompiledExpr], ...]  # topologically ordered (MD-08)
    exempt: tuple[CompiledExpr, ...]
    rules: tuple[RuleDef, ...]
    windows: tuple[tuple[str, float], ...]  # union of all expressions' .windows (CA-04)
    normalized_toml: str
    content_hash: str  # sha256 hex of normalized_toml


class ValidationError(Exception):
    """Raised with one or more structured errors: {path, code, message, hint}."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        summary = "; ".join(f"{e['path']}: {e['code']}" for e in errors[:5])
        super().__init__(f"{len(errors)} validation error(s): {summary}")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _err(path: str, code: str, message: str, hint: str | None = None) -> dict:
    return {"path": path, "code": code, "message": message, "hint": hint}


def _check_unknown_keys(
    table: Any, allowed: frozenset[str], prefix: str, errors: list[dict]
) -> None:
    if not isinstance(table, dict):
        return
    for key in table:
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in allowed:
            errors.append(_err(path, "unknown_key", f"unknown key {path!r}"))


def _suggest(name: str, candidates: list[str] | tuple[str, ...]) -> str | None:
    matches = difflib.get_close_matches(name, list(candidates), n=1)
    return f"did you mean {matches[0]!r}?" if matches else None


def _compile(path: str, text: object, env: NameEnv, errors: list[dict]) -> CompiledExpr | None:
    if not isinstance(text, str):
        errors.append(_err(path, "invalid_type", f"{path} must be a string expression"))
        return None
    try:
        return compile_expr(text, env)
    except ExprNameError as e:
        errors.append(_err(path, "unknown_name", str(e), _suggest(e.name, e.candidates)))
    except ExprSyntaxError as e:
        errors.append(_err(path, "expr_syntax", str(e)))
    return None


def _duration(
    path: str, text: object, errors: list[dict], *, minimum: float | None = None
) -> float | None:
    if not isinstance(text, str):
        errors.append(_err(path, "invalid_type", f"{path} must be a duration string"))
        return None
    try:
        seconds = parse_duration(text)
    except ExprSyntaxError as e:
        errors.append(_err(path, "invalid_value", str(e)))
        return None
    if minimum is not None and seconds < minimum:
        errors.append(_err(path, "invalid_value", f"{path} must be >= {minimum}s, got {seconds}s"))
        return None
    return seconds


def _validate_message(
    path: str, template: object, allowed_fields: set[str], errors: list[dict]
) -> None:
    if not isinstance(template, str) or not template:
        errors.append(_err(path, "invalid_type", f"{path} must be a non-empty string"))
        return
    if len(template) > schema.MESSAGE_MAX_LEN:
        errors.append(_err(path, "invalid_value", f"{path} exceeds {schema.MESSAGE_MAX_LEN} chars"))
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError as e:
        errors.append(_err(path, "bad_template", f"malformed template: {e}"))
        return
    for _literal, field_name, _format_spec, _conversion in fields:
        if field_name is None:
            continue
        if field_name == "" or not field_name.isidentifier():
            errors.append(
                _err(
                    path,
                    "bad_template",
                    f"invalid template field {field_name!r} (bare names only)",
                )
            )
            continue
        if field_name not in allowed_fields:
            errors.append(
                _err(
                    path,
                    "unknown_field",
                    f"unknown template field {field_name!r}",
                    _suggest(field_name, sorted(allowed_fields)),
                )
            )


def _normalize(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _normalize(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# core builder
# --------------------------------------------------------------------------


def _build(parsed: dict, filename: str) -> tuple[MonitorDef | None, list[dict]]:
    errors: list[dict] = []

    if not isinstance(parsed, dict):
        return None, [_err("<root>", "invalid_type", "definition must be a TOML table")]

    _check_unknown_keys(parsed, schema.TOP_LEVEL_KEYS, "", errors)

    # --- schema ------------------------------------------------------------
    if "schema" not in parsed:
        errors.append(_err("schema", "missing_key", "missing required top-level key 'schema'"))
    elif parsed["schema"] != 1:
        errors.append(
            _err("schema", "invalid_value", f"schema must be == 1, got {parsed['schema']!r}")
        )

    # --- [monitor] -----------------------------------------------------------
    monitor_tbl = parsed.get("monitor")
    if not isinstance(monitor_tbl, dict):
        errors.append(_err("monitor", "missing_key", "missing required table [monitor]"))
        monitor_tbl = {}
    else:
        _check_unknown_keys(monitor_tbl, schema.MONITOR_KEYS, "monitor", errors)

    for key in schema.MONITOR_REQUIRED:
        if key not in monitor_tbl:
            errors.append(_err(f"monitor.{key}", "missing_key", f"missing monitor.{key}"))

    name = monitor_tbl.get("name")
    if name is not None and not schema.valid_name(name):
        errors.append(
            _err(
                "monitor.name",
                "invalid_value",
                f"monitor.name {name!r} must match {schema.NAME_RE.pattern}",
            )
        )
        name = None

    description = monitor_tbl.get("description")
    if description is not None and (
        not isinstance(description, str) or len(description) > schema.DESCRIPTION_MAX_LEN
    ):
        errors.append(
            _err(
                "monitor.description",
                "invalid_value",
                "monitor.description must be a string <= 200 chars",
            )
        )
        description = None

    version = monitor_tbl.get("version")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 1
    ):
        errors.append(
            _err("monitor.version", "invalid_value", "monitor.version must be an int >= 1")
        )
        version = None

    enabled = monitor_tbl.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(_err("monitor.enabled", "invalid_type", "monitor.enabled must be a bool"))
        enabled = True

    platforms_raw = monitor_tbl.get("platforms")
    platforms: tuple[str, ...] = ()
    if platforms_raw is not None:
        ok = (
            isinstance(platforms_raw, list)
            and bool(platforms_raw)
            and all(isinstance(p, str) for p in platforms_raw)
            and set(platforms_raw) <= schema.PLATFORMS
        )
        if not ok:
            errors.append(
                _err(
                    "monitor.platforms",
                    "invalid_value",
                    f"monitor.platforms must be a non-empty subset of {sorted(schema.PLATFORMS)}",
                )
            )
        else:
            platforms = tuple(platforms_raw)

    source = monitor_tbl.get("source")
    if source is not None and not isinstance(source, str):
        errors.append(_err("monitor.source", "invalid_type", "monitor.source must be a string"))
        source = None

    is_events = source == "events"
    decl = None
    if source is not None:
        decl = SOURCE_DECLS.get(source)
        if decl is None:
            candidates = tuple(SOURCE_DECLS)
            errors.append(
                _err(
                    "monitor.source",
                    "unknown_source",
                    f"unknown source {source!r}",
                    _suggest(source, candidates),
                )
            )
    decl_metrics = decl.metric_names() if decl is not None else frozenset()
    decl_attrs = decl.attr_names() if decl is not None else frozenset()

    interval_s = 0.0
    has_interval = "interval" in monitor_tbl
    if is_events:
        if has_interval:
            errors.append(
                _err(
                    "monitor.interval",
                    "unknown_key",
                    "monitor.interval is not allowed for source == 'events'",
                )
            )
    elif not has_interval:
        errors.append(_err("monitor.interval", "missing_key", "monitor.interval is required"))
    else:
        got = _duration(
            "monitor.interval", monitor_tbl["interval"], errors, minimum=schema.MIN_INTERVAL_S
        )
        if got is not None:
            interval_s = got

    # --- [parameters] --------------------------------------------------------
    params_tbl = parsed.get("parameters")
    parameters: dict[str, float] = {}
    param_names: set[str] = set()
    if params_tbl is not None and not isinstance(params_tbl, dict):
        errors.append(_err("parameters", "invalid_type", "[parameters] must be a table"))
        params_tbl = {}
    for pname, pval in (params_tbl or {}).items():
        ppath = f"parameters.{pname}"
        if not schema.is_identifier(pname):
            errors.append(
                _err(ppath, "invalid_value", f"parameter name {pname!r} must be an identifier")
            )
            continue
        if not isinstance(pval, dict):
            errors.append(_err(ppath, "invalid_type", f"{ppath} must be a table with value/doc"))
            continue
        _check_unknown_keys(pval, schema.PARAM_KEYS, ppath, errors)
        if "value" not in pval:
            errors.append(_err(f"{ppath}.value", "missing_key", f"missing {ppath}.value"))
            continue
        value = pval["value"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(_err(f"{ppath}.value", "invalid_type", f"{ppath}.value must be numeric"))
            continue
        if "doc" not in pval:
            errors.append(_err(f"{ppath}.doc", "missing_key", f"missing {ppath}.doc"))
        elif not isinstance(pval["doc"], str):
            errors.append(_err(f"{ppath}.doc", "invalid_type", f"{ppath}.doc must be a string"))
        parameters[pname] = float(value)
        param_names.add(pname)

    # --- [source_options] -----------------------------------------------------
    source_options_tbl = parsed.get("source_options")
    source_options: dict = {}
    if source_options_tbl is not None and not isinstance(source_options_tbl, dict):
        errors.append(_err("source_options", "invalid_type", "[source_options] must be a table"))
        source_options_tbl = {}
    if source_options_tbl is not None:
        if source in schema.SOURCE_OPTIONS_WATCHLIST_SOURCES:
            allowed_so = frozenset({"watchlist"})
        elif source in schema.SOURCE_OPTIONS_TOPN_SOURCES:
            allowed_so = frozenset({"top_n"})
        else:
            allowed_so = frozenset()
        _check_unknown_keys(source_options_tbl, allowed_so, "source_options", errors)

    if source in schema.SOURCE_OPTIONS_WATCHLIST_SOURCES:
        watchlist = (source_options_tbl or {}).get("watchlist", [])
        if not isinstance(watchlist, list):
            errors.append(
                _err("source_options.watchlist", "invalid_type", "watchlist must be an array")
            )
            watchlist = []
        for i, entry in enumerate(watchlist):
            epath = f"source_options.watchlist[{i}]"
            if not isinstance(entry, dict):
                errors.append(_err(epath, "invalid_type", f"{epath} must be a table"))
                continue
            _check_unknown_keys(entry, schema.WATCHLIST_ENTRY_KEYS, epath, errors)
            targets = [k for k in schema.WATCHLIST_TARGET_KEYS if k in entry]
            if len(targets) != 1:
                errors.append(
                    _err(
                        epath,
                        "invalid_value",
                        f"{epath} must set exactly one of unit/process/listen",
                    )
                )
            if "expected" in entry and not isinstance(entry["expected"], bool):
                errors.append(_err(f"{epath}.expected", "invalid_type", "expected must be a bool"))
        source_options = {"watchlist": watchlist}
    elif source in schema.SOURCE_OPTIONS_TOPN_SOURCES:
        top_n = (source_options_tbl or {}).get("top_n", schema.TOP_N_DEFAULT)
        if (
            not isinstance(top_n, int)
            or isinstance(top_n, bool)
            or not (schema.TOP_N_MIN <= top_n <= schema.TOP_N_MAX)
        ):
            errors.append(
                _err(
                    "source_options.top_n",
                    "invalid_value",
                    f"top_n must be an int in [{schema.TOP_N_MIN}, {schema.TOP_N_MAX}]",
                )
            )
            top_n = schema.TOP_N_DEFAULT
        source_options = {"top_n": top_n}

    # --- [promotion] -----------------------------------------------------------
    promotion_tbl = parsed.get("promotion")
    promotion: CompiledExpr | None = None
    if promotion_tbl is not None:
        if source != "process":
            errors.append(
                _err(
                    "promotion",
                    "unknown_key",
                    "[promotion] is only allowed when monitor.source == 'process'",
                )
            )
        if not isinstance(promotion_tbl, dict):
            errors.append(_err("promotion", "invalid_type", "[promotion] must be a table"))
            promotion_tbl = {}
        else:
            _check_unknown_keys(promotion_tbl, frozenset({"expr"}), "promotion", errors)
        if source == "process":
            if "expr" in promotion_tbl:
                penv = NameEnv(
                    metrics=decl_metrics, attrs=decl_attrs, params=frozenset(param_names)
                )
                promotion = _compile("promotion.expr", promotion_tbl["expr"], penv, errors)
            else:
                errors.append(_err("promotion.expr", "missing_key", "missing promotion.expr"))

    # --- [[derived]] (MD-08 topological order) ---------------------------------
    derived_raw = parsed.get("derived")
    derived_entries: dict[str, str] = {}
    derived_index: dict[str, int] = {}
    if derived_raw:
        if is_events:
            errors.append(
                _err("derived", "unknown_key", "[[derived]] is not allowed for source == 'events'")
            )
        elif not isinstance(derived_raw, list):
            errors.append(_err("derived", "invalid_type", "[[derived]] must be an array of tables"))
        else:
            for i, item in enumerate(derived_raw):
                dpath = f"derived[{i}]"
                if not isinstance(item, dict):
                    errors.append(_err(dpath, "invalid_type", f"{dpath} must be a table"))
                    continue
                _check_unknown_keys(item, schema.DERIVED_KEYS, dpath, errors)
                if "name" not in item:
                    errors.append(_err(f"{dpath}.name", "missing_key", f"missing {dpath}.name"))
                    continue
                dname = item["name"]
                if not schema.is_identifier(dname):
                    errors.append(
                        _err(
                            f"{dpath}.name",
                            "invalid_value",
                            f"derived name {dname!r} must be an identifier",
                        )
                    )
                    continue
                if "expr" not in item:
                    errors.append(_err(f"{dpath}.expr", "missing_key", f"missing {dpath}.expr"))
                    continue
                dexpr = item["expr"]
                if not isinstance(dexpr, str):
                    errors.append(
                        _err(f"{dpath}.expr", "invalid_type", f"{dpath}.expr must be a string")
                    )
                    continue
                if dname in derived_entries:
                    errors.append(
                        _err(
                            f"{dpath}.name",
                            "duplicate_name",
                            f"duplicate derived name {dname!r}",
                        )
                    )
                    continue
                derived_entries[dname] = dexpr
                derived_index[dname] = i

    derived_ordered: list[tuple[str, CompiledExpr]] = []
    if derived_entries:
        remaining = dict(derived_entries)
        ordered_names: list[str] = []
        progressed = True
        while remaining and progressed:
            progressed = False
            for dname in list(remaining):
                env = NameEnv(
                    metrics=decl_metrics | frozenset(ordered_names),
                    attrs=decl_attrs,
                    params=frozenset(param_names),
                )
                try:
                    compiled = compile_expr(remaining[dname], env)
                except ExprNameError:
                    continue  # may resolve once more derived names are ordered; retry next pass
                except ExprSyntaxError as e:
                    errors.append(
                        _err(f"derived[{derived_index[dname]}].expr", "expr_syntax", str(e))
                    )
                    del remaining[dname]
                    progressed = True
                    continue
                ordered_names.append(dname)
                derived_ordered.append((dname, compiled))
                del remaining[dname]
                progressed = True
        if remaining:
            errors.append(
                _err(
                    "derived",
                    "derived_cycle",
                    "dependency cycle (or unresolved reference) among derived metrics: "
                    f"{sorted(remaining)}",
                )
            )

    derived_names = frozenset(name for name, _ in derived_ordered)

    # --- exempt (CA-07, sampler sources only) -----------------------------------
    exempt_raw = parsed.get("exempt")
    exempt: tuple[CompiledExpr, ...] = ()
    if exempt_raw is not None:
        if is_events:
            errors.append(
                _err("exempt", "unknown_key", "exempt is not allowed for source == 'events'")
            )
        elif not isinstance(exempt_raw, list) or not all(isinstance(x, str) for x in exempt_raw):
            errors.append(_err("exempt", "invalid_type", "exempt must be an array of strings"))
        else:
            eenv = NameEnv(metrics=decl_metrics, attrs=decl_attrs, params=frozenset(param_names))
            compiled_exempt = []
            for i, text in enumerate(exempt_raw):
                c = _compile(f"exempt[{i}]", text, eenv, errors)
                if c is not None:
                    compiled_exempt.append(c)
            exempt = tuple(compiled_exempt)

    # --- [[rule]] ----------------------------------------------------------------
    rule_raw = parsed.get("rule", [])
    if rule_raw and not isinstance(rule_raw, list):
        errors.append(_err("rule", "invalid_type", "[[rule]] must be an array of tables"))
        rule_raw = []

    allowed_rule_keys = schema.RULE_KEYS_COMMON | (
        schema.RULE_KEYS_EVENT if is_events else schema.RULE_KEYS_SAMPLER
    )
    rule_metrics = decl_metrics | derived_names
    rule_attrs = decl_attrs
    rule_env = NameEnv(metrics=rule_metrics, attrs=rule_attrs, params=frozenset(param_names))
    allowed_fields = (
        set(rule_metrics) | set(rule_attrs) | param_names | {"entity", "monitor", "severity"}
    )

    rules: list[RuleDef] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(rule_raw):
        rpath = f"rule[{i}]"
        if not isinstance(item, dict):
            errors.append(_err(rpath, "invalid_type", f"{rpath} must be a table"))
            continue
        _check_unknown_keys(item, allowed_rule_keys, rpath, errors)
        for key in schema.RULE_REQUIRED:
            if key not in item:
                errors.append(_err(f"{rpath}.{key}", "missing_key", f"missing {rpath}.{key}"))

        rid = item.get("id")
        if not schema.valid_id(rid):
            errors.append(
                _err(
                    f"{rpath}.id",
                    "invalid_value",
                    f"rule id {rid!r} must match {schema.ID_RE.pattern}",
                )
            )
            rid = None
        elif rid in seen_ids:
            errors.append(_err(f"{rpath}.id", "duplicate_id", f"duplicate rule id {rid!r}"))
            rid = None
        if rid is not None:
            seen_ids.add(rid)

        group = item.get("group", rid)
        if group is not None and not schema.valid_id(group):
            errors.append(
                _err(
                    f"{rpath}.group",
                    "invalid_value",
                    f"rule group {group!r} must match {schema.ID_RE.pattern}",
                )
            )
            group = rid

        when = _compile(f"{rpath}.when", item["when"], rule_env, errors) if "when" in item else None

        severity_raw = item.get("severity")
        severity: int | None = None
        if severity_raw is not None:
            if severity_raw not in schema.RULE_SEVERITIES:
                errors.append(
                    _err(
                        f"{rpath}.severity",
                        "invalid_value",
                        f"severity must be one of {schema.RULE_SEVERITIES}",
                    )
                )
            else:
                severity = model.SEVERITIES.index(severity_raw)

        confirm_cycles = 1
        clear_cycles = 1
        cooldown_s: float | None = None
        clear_after_s: float | None = None
        confirm_count = 1
        confirm_window_s: float | None = None

        if is_events:
            cooldown_s = schema.DEFAULT_COOLDOWN_S
            if "cooldown" in item:
                v = _duration(f"{rpath}.cooldown", item["cooldown"], errors)
                if v is not None:
                    cooldown_s = v
            clear_after_s = schema.DEFAULT_CLEAR_AFTER_S
            if "clear_after" in item:
                v = _duration(f"{rpath}.clear_after", item["clear_after"], errors)
                if v is not None:
                    clear_after_s = v
            if "confirm_count" in item:
                cc = item["confirm_count"]
                if not isinstance(cc, int) or isinstance(cc, bool) or cc < 1:
                    errors.append(
                        _err(
                            f"{rpath}.confirm_count",
                            "invalid_value",
                            "confirm_count must be an int >= 1",
                        )
                    )
                else:
                    confirm_count = cc
            if "confirm_window" in item:
                v = _duration(f"{rpath}.confirm_window", item["confirm_window"], errors)
                confirm_window_s = v
        else:
            if "confirm_cycles" in item:
                cv = item["confirm_cycles"]
                if not isinstance(cv, int) or isinstance(cv, bool) or not (
                    schema.CONFIRM_CYCLES_MIN <= cv <= schema.CONFIRM_CYCLES_MAX
                ):
                    errors.append(
                        _err(
                            f"{rpath}.confirm_cycles",
                            "invalid_value",
                            "confirm_cycles must be an int in "
                            f"[{schema.CONFIRM_CYCLES_MIN}, {schema.CONFIRM_CYCLES_MAX}]",
                        )
                    )
                else:
                    confirm_cycles = cv
            if "clear_cycles" in item:
                cv = item["clear_cycles"]
                if not isinstance(cv, int) or isinstance(cv, bool) or not (
                    schema.CONFIRM_CYCLES_MIN <= cv <= schema.CONFIRM_CYCLES_MAX
                ):
                    errors.append(
                        _err(
                            f"{rpath}.clear_cycles",
                            "invalid_value",
                            "clear_cycles must be an int in "
                            f"[{schema.CONFIRM_CYCLES_MIN}, {schema.CONFIRM_CYCLES_MAX}]",
                        )
                    )
                else:
                    clear_cycles = cv
            else:
                clear_cycles = confirm_cycles

        message = item.get("message", "")
        if "message" in item:
            _validate_message(f"{rpath}.message", message, allowed_fields, errors)

        action = item.get("action")
        if action is not None and (
            not isinstance(action, str) or not action or "/" in action or "\\" in action
        ):
            errors.append(
                _err(
                    f"{rpath}.action",
                    "invalid_value",
                    "action must be a bare filename, no path separators",
                )
            )
            action = None

        notify_recovery_default = not is_events
        notify_recovery = item.get("notify_recovery", notify_recovery_default)
        if not isinstance(notify_recovery, bool):
            errors.append(
                _err(f"{rpath}.notify_recovery", "invalid_type", "notify_recovery must be a bool")
            )
            notify_recovery = notify_recovery_default

        if rid is None or when is None or severity is None:
            continue

        rules.append(
            RuleDef(
                id=rid,
                group=group or rid,
                when=when,
                severity=severity,
                confirm_cycles=confirm_cycles,
                clear_cycles=clear_cycles,
                message=message if isinstance(message, str) else "",
                action=action,
                notify_recovery=notify_recovery,
                cooldown_s=cooldown_s,
                clear_after_s=clear_after_s,
                confirm_count=confirm_count,
                confirm_window_s=confirm_window_s,
            )
        )

    # --- windows (CA-04) -----------------------------------------------------
    all_windows: set[tuple[str, float]] = set()
    if promotion is not None:
        all_windows.update(promotion.windows)
    for _, cexpr in derived_ordered:
        all_windows.update(cexpr.windows)
    for cexpr in exempt:
        all_windows.update(cexpr.windows)
    for r in rules:
        all_windows.update(r.when.windows)
    windows = tuple(sorted(all_windows))

    if not is_events and interval_s > 0:
        for metric, window_s in windows:
            points = window_s / interval_s
            if points > schema.MAX_POINTS:
                errors.append(
                    _err(
                        "windows",
                        "points_overflow",
                        f"window {window_s}s on metric {metric!r} implies {points:.0f} points "
                        f"(> {schema.MAX_POINTS}) at interval {interval_s}s",
                    )
                )

    if errors:
        return None, errors

    normalized_toml = tomli_w.dumps(_normalize(parsed))
    content_hash = hashlib.sha256(normalized_toml.encode("utf-8")).hexdigest()

    monitor_def = MonitorDef(
        name=name,
        description=description,
        version=version,
        enabled=enabled,
        platforms=platforms,
        interval_s=interval_s,
        source=source,
        source_options=source_options,
        parameters=parameters,
        promotion=promotion,
        derived=tuple(derived_ordered),
        exempt=exempt,
        rules=tuple(rules),
        windows=windows,
        normalized_toml=normalized_toml,
        content_hash=content_hash,
    )
    return monitor_def, []


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def load_text(text: str, filename: str = "<text>") -> MonitorDef:
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValidationError([_err("<root>", "toml_syntax", f"{filename}: {e}")]) from e

    monitor_def, errors = _build(parsed, filename)
    if errors:
        tagged = [dict(e, message=f"{filename}: {e['message']}") for e in errors]
        raise ValidationError(tagged)
    assert monitor_def is not None
    return monitor_def


def load_file(path: Path) -> MonitorDef:
    reject_symlink(path)  # PM-06c
    text = path.read_text(encoding="utf-8")
    return load_text(text, filename=str(path))


def load_dir(monitors_dir: Path) -> tuple[list[MonitorDef], list[tuple[Path, ValidationError]]]:
    defs: list[MonitorDef] = []
    errors: list[tuple[Path, ValidationError]] = []
    for path in sorted(monitors_dir.glob("*.toml")):
        try:
            defs.append(load_file(path))
        except ValidationError as e:
            errors.append((path, e))
        except OSError as e:
            errors.append((path, ValidationError([_err("<file>", "io_error", str(e))])))
    return defs, errors

"""MCP server (MC-01..05, DESIGN section 13): FTMON for AI assistants.

Two layers on purpose:
- `McpApi` — every tool as a plain method returning JSON-able dicts. This
  is the tested surface; it never touches stdio and takes injected `now`
  values (TS-03), so the whole tool surface unit-tests against a prepared
  database without an MCP client in sight.
- `build_server` — FastMCP registration only. Thin enough that the frozen
  tool list (MC-01) is checkable by introspecting the returned server.

Error philosophy (MC-04): tools return `{"error": {code, message, hint}}`
instead of raising — an exception would surface to the model as an opaque
protocol error, but a structured hint lets a less capable model self-correct
(the whole point of the drafts loop). The write authority is deliberately
tiny: `define_monitor` can only create drafts (PM-06), `ack_incident` can
only quiet an incident; nothing the model does can enable a monitor or run
an action.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ftmon.clock import SystemClock
from ftmon.definitions import loader, manage
from ftmon.expr.parse import ExprError, NameEnv, compile_expr, parse_duration
from ftmon.model import SEVERITIES, severity_name
from ftmon.paths import Paths, get_paths
from ftmon.store.db import connect
from ftmon.store.query import Query, SmallWrites

_EVENT_ENV = NameEnv(metrics=frozenset({"severity"}),
                     attrs=frozenset({"provider", "event_id", "message", "source"}))
_STALE_AFTER_S = 15.0  # 3x the 5s base tick (UI-04's staleness rule)


def _err(code: str, message: str, hint: str = "", **extra) -> dict:
    return {"error": {"code": code, "message": message, "hint": hint, **extra}}


def _manage_err(e: manage.ManageError) -> dict:
    out = _err(e.code, e.message, e.hint)
    if e.errors:
        out["error"]["errors"] = list(e.errors)
    return out


def _tz_name(now: float) -> str:
    """MC-02: the host zone, once per response, so the model can localize
    the UTC timestamps. IANA name when the OS records one; the abbreviated
    zone as fallback (still unambiguous next to UTC timestamps)."""
    try:
        return Path("/etc/timezone").read_text().strip()
    except OSError:
        return str(datetime.fromtimestamp(now).astimezone().tzinfo)


class _AttrCtx:
    """Minimal EvalContext over stored attrs/fields for filter_expr /
    match_expr — no metrics history, no params: these expressions filter
    recorded rows, they don't evaluate rules."""

    def __init__(self, attrs: dict, metrics: dict | None = None, now: float = 0.0):
        self._attrs = attrs
        self._metrics = metrics or {}
        self._now = now

    def metric_last(self, m):
        return self._metrics.get(m)

    def metric_last_ts(self, m):
        return self._now if m in self._metrics else None

    def metric_window(self, m, seconds):
        return []

    def attr(self, a):
        v = self._attrs.get(a)
        return None if v is None else str(v)

    def param(self, p):
        raise KeyError(p)

    def baseline(self, m):
        return None

    def now(self):
        return self._now


class McpApi:
    def __init__(self, paths: Paths, clock=None):
        self._paths = paths
        self._clock = clock or SystemClock()

    # -- plumbing ----------------------------------------------------------

    def _query(self) -> Query | None:
        if not self._paths.db_file.exists():
            return None
        return Query(connect(self._paths.db_file, readonly=True))

    def _no_db(self) -> dict:
        return _err("not_found", "no database yet",
                    "start the daemon once: ftmon daemon")

    def _range(self, range_, now: float) -> tuple[float, float] | dict:
        """MC-02: '90m'-style duration (ending now) or [iso, iso] pair."""
        if isinstance(range_, str):
            try:
                return now - parse_duration(range_), now
            except ExprError:
                return _err("invalid_params", f"bad range {range_!r}",
                            'use "90m"/"3h"/"2d" or ["iso-start", "iso-end"]')
        if isinstance(range_, (list, tuple)) and len(range_) == 2:
            try:
                start = datetime.fromisoformat(str(range_[0])).timestamp()
                end = datetime.fromisoformat(str(range_[1])).timestamp()
                return start, end
            except ValueError:
                return _err("invalid_params", f"bad ISO range {range_!r}",
                            "ISO-8601, e.g. 2026-07-11T09:00:00+10:00")
        return _err("invalid_params", f"bad range {range_!r}",
                    'use "90m"/"3h"/"2d" or ["iso-start", "iso-end"]')

    def _severity_index(self, name_or_none) -> int | dict:
        if name_or_none is None:
            return 0
        if name_or_none in SEVERITIES:
            return SEVERITIES.index(name_or_none)
        return _err("invalid_params", f"unknown severity {name_or_none!r}",
                    f"one of: {', '.join(SEVERITIES)}")

    # -- tools (MC-01 frozen surface) ---------------------------------------

    def get_status(self) -> dict:
        now = self._clock.now()
        q = self._query()
        defs, errors = loader.load_dir(
            self._paths.monitors_dir,
            actions_dir=self._paths.actions_dir,
            require_actions=True,
        )
        monitors = [{"name": d.name, "source": d.source, "enabled": d.enabled}
                    for d in defs]
        monitors += [{"name": p.stem, "state": "config_error", "error": str(e)[:200]}
                     for p, e in errors]
        drafts = sorted(p.stem for p in self._paths.drafts_dir.glob("*.toml")
                        ) if self._paths.drafts_dir.exists() else []
        out = {"tz": _tz_name(now), "monitors": monitors, "drafts": drafts}
        if q is None:
            out.update({"daemon_alive": False, "last_tick_age_s": None,
                        "open_incidents": 0, "self_metrics": {}})
            return out
        info = q.status(now=now)
        age = info.get("last_tick_age_s")
        rows = q._conn.execute(
            "SELECT se.metric, s.value FROM series se JOIN samples s "
            "ON s.series_id = se.id WHERE se.monitor = 'self' AND s.ts = "
            "(SELECT MAX(ts) FROM samples WHERE series_id = se.id)"
        ).fetchall()
        out.update({
            "daemon_alive": age is not None and age < _STALE_AFTER_S,
            "daemon_stale": age is not None and age >= _STALE_AFTER_S,
            "last_tick_age_s": age,
            "db_bytes": info["db_bytes"],
            "open_incidents": info["open_incidents"],
            "self_metrics": {r["metric"]: r["value"] for r in rows},
        })
        return out

    def query_metrics(self, monitor: str, metric: str, range,  # noqa: A002
                      entity=None, agg=None, filter_expr=None) -> dict:
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        r = self._range(range, now)
        if isinstance(r, dict):
            return r
        if agg not in (None, "avg", "min", "max", "last"):
            return _err("invalid_params", f"unknown agg {agg!r}",
                        "avg | min | max | last")
        results = q.series(monitor, metric, now=now, start=r[0], end=r[1],
                           entity_id=entity)
        keep = self._attr_filter(q, monitor, filter_expr, now)
        if isinstance(keep, dict):
            return keep
        series = []
        for res in results:
            if keep is not None and res.entity_id not in keep:
                continue
            entry: dict = {"entity": res.entity_id}
            if agg is None:
                entry["points"] = [[p.ts, p.value] for p in res.points]
            elif res.points:
                vals = [p.value for p in res.points]
                entry["agg"] = {"avg": sum(vals) / len(vals), "min": min(vals),
                                "max": max(vals), "last": vals[-1]}[agg]
            else:
                entry["agg"] = None
            series.append(entry)
        resolution = results[0].resolution if results else "raw"
        return {"tz": _tz_name(now), "resolution": resolution, "series": series}

    def _attr_filter(self, q: Query, monitor: str, filter_expr, now: float):
        """Compile filter_expr over entity attrs; returns the passing
        entity_id set, or None for no filter, or an error dict."""
        if not filter_expr:
            return None
        rows = q._conn.execute(
            "SELECT entity_id, attrs FROM entities WHERE monitor = ?", (monitor,)
        ).fetchall()
        attr_names = set()
        parsed = {}
        for row in rows:
            attrs = json.loads(row["attrs"] or "{}")
            parsed[row["entity_id"]] = attrs
            attr_names.update(attrs)
        try:
            expr = compile_expr(filter_expr, NameEnv(attrs=frozenset(attr_names)))
        except ExprError as e:
            return _err("invalid_params", f"filter_expr: {e}",
                        "attrs available: " + ", ".join(sorted(attr_names)))
        return {eid for eid, attrs in parsed.items()
                if expr.eval(_AttrCtx(attrs, now=now)) is True}

    def top_consumers(self, resource: str, range, n: int = 10) -> dict:  # noqa: A002
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        r = self._range(range, now)
        if isinstance(r, dict):
            return r
        metric = {"cpu": "cpu_pct", "rss": "rss_bytes", "io": "io_write_bytes"}.get(
            resource)
        if metric is None:
            return _err("invalid_params", f"unknown resource {resource!r}",
                        "cpu | rss | io")
        # io_* are counters: consumption over the range is max-min, not avg.
        agg_sql = "MAX(s.value) - MIN(s.value)" if resource == "io" else "AVG(s.value)"
        rows = q._conn.execute(
            f"SELECT se.entity_id, {agg_sql} AS agg_value "
            "FROM samples s JOIN series se ON se.id = s.series_id "
            "WHERE se.metric = ? AND s.ts BETWEEN ? AND ? "
            "GROUP BY se.monitor, se.entity_id",
            (metric, round(r[0]), round(r[1])),
        ).fetchall()
        # the same process may be persisted by several monitors (leak+hog):
        # keep one line per entity, the largest aggregate
        best: dict[str, float] = {}
        for row in rows:
            v = row["agg_value"] or 0.0
            if v > best.get(row["entity_id"], float("-inf")):
                best[row["entity_id"]] = v
        ranked = sorted(best.items(), key=lambda kv: -kv[1])[:max(1, int(n))]
        out = []
        for entity_id, value in ranked:
            attrs_row = q._conn.execute(
                "SELECT attrs FROM entities WHERE entity_id = ? LIMIT 1",
                (entity_id,)).fetchone()
            out.append({
                "entity": entity_id,
                "attrs": json.loads(attrs_row["attrs"]) if attrs_row else {},
                "agg_value": value,
            })
        return {"tz": _tz_name(now), "resource": resource, "metric": metric,
                "ranked": out}

    def get_process_history(self, name_or_pid, range) -> dict:  # noqa: A002
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        r = self._range(range, now)
        if isinstance(r, dict):
            return r
        needle = str(name_or_pid)
        # entity_id convention: "{name}:{pid}:{create_time}" (sources/process)
        pattern = f"%:{needle}:%" if needle.isdigit() else f"%{needle}%"
        rows = q._conn.execute(
            "SELECT * FROM entities WHERE entity_id LIKE ? "
            "ORDER BY last_seen DESC LIMIT 20", (pattern,)
        ).fetchall()
        entities = []
        for row in rows:
            series: dict[str, list] = {}
            for metric in ("cpu_pct", "rss_bytes"):
                for res in q.series(row["monitor"], metric, now=now,
                                    start=r[0], end=r[1],
                                    entity_id=row["entity_id"], max_points=500):
                    series[metric] = [[p.ts, p.value] for p in res.points]
            entities.append({
                "entity_id": row["entity_id"],
                "monitor": row["monitor"],
                "attrs": json.loads(row["attrs"] or "{}"),
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "gone_ts": row["gone_ts"],
                "series": series,
            })
        return {"tz": _tz_name(now), "entities": entities}

    def list_events(self, range, min_severity=None, provider=None,  # noqa: A002
                    match_expr=None, limit: int = 200) -> dict:
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        r = self._range(range, now)
        if isinstance(r, dict):
            return r
        sev = self._severity_index(min_severity)
        if isinstance(sev, dict):
            return sev
        expr = None
        if match_expr:
            try:
                expr = compile_expr(match_expr, _EVENT_ENV)
            except ExprError as e:
                return _err("invalid_params", f"match_expr: {e}",
                            "canonical fields: severity, provider, event_id, "
                            "message, source")
        rows = q.events(start=r[0], end=r[1], min_severity=sev,
                        provider=provider, limit=int(limit))
        events = []
        for row in rows:
            if expr is not None:
                ctx = _AttrCtx(
                    {"provider": row["provider"], "event_id": row["event_id"],
                     "message": row["message"], "source": row["source"]},
                    metrics={"severity": float(row["severity"])}, now=now)
                if expr.eval(ctx) is not True:
                    continue
            events.append({k: row[k] for k in
                           ("id", "ts", "source", "provider", "event_id",
                            "severity", "message")})
        return {"tz": _tz_name(now), "events": events}

    def list_incidents(self, state=None, range=None, monitor=None) -> dict:  # noqa: A002
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        since = None
        if range is not None:
            r = self._range(range, now)
            if isinstance(r, dict):
                return r
            since = r[0]
        rows = q.incidents(state=state, monitor=monitor, since=since)
        return {"tz": _tz_name(now), "incidents": [
            {**{k: row[k] for k in
                ("id", "monitor", "grp", "entity_id", "state", "severity",
                 "owning_rule", "opened_ts", "last_change_ts", "cleared_ts",
                 "clear_reason", "notify_count", "occurrences", "flapping")},
             "severity_name": severity_name(row["severity"])}
            for row in rows]}

    def explain_incident(self, id: int) -> dict:  # noqa: A002
        now = self._clock.now()
        q = self._query()
        if q is None:
            return self._no_db()
        row = q._conn.execute("SELECT * FROM incidents WHERE id = ?",
                              (int(id),)).fetchone()
        if row is None:
            return _err("not_found", f"no incident #{id}",
                        "list_incidents shows what exists")
        defs, _errors = loader.load_dir(
            self._paths.monitors_dir,
            actions_dir=self._paths.actions_dir,
            require_actions=True,
        )
        rule_text = None
        params: dict = {}
        for d in defs:
            if d.name != row["monitor"]:
                continue
            params = dict(d.parameters)
            for rule in d.rules:
                if rule.id == row["owning_rule"]:
                    rule_text = rule.when.source
        history = [
            {"seq": h["seq"], "ts": h["ts"], "kind": h["kind"],
             "detail": json.loads(h["detail"] or "{}")}
            for h in q._conn.execute(
                "SELECT * FROM incident_history WHERE incident_id = ? "
                "ORDER BY seq", (int(id),))
        ]
        events = [dict(e) for e in q.events(
            start=row["opened_ts"] - 600, end=row["opened_ts"] + 600,
            min_severity=0, limit=50)]  # DM-12: context ±10m around opening
        metrics = [m["metric"] for m in q._conn.execute(
            "SELECT DISTINCT metric FROM series WHERE monitor = ? AND entity_id = ? "
            "LIMIT 8", (row["monitor"], row["entity_id"]))]
        series = {}
        for metric in metrics:
            for res in q.series(row["monitor"], metric, now=now,
                                start=row["opened_ts"] - 1800,
                                end=min(now, (row["cleared_ts"] or now) + 1800),
                                entity_id=row["entity_id"], max_points=300):
                series[metric] = [[p.ts, p.value] for p in res.points]
        return {
            "tz": _tz_name(now),
            "incident": dict(row),
            "severity_name": severity_name(row["severity"]),
            "rule": {"id": row["owning_rule"], "expr": rule_text,
                     "parameters": params},
            "history": history,
            "related_events": events,
            "series": series,
        }

    def list_monitors(self) -> dict:
        from ftmon.definitions.manage import list_monitors as list_monitor_catalog

        return list_monitor_catalog(self._paths, now=self._clock.now())

    def get_monitor(self, name: str) -> dict:
        now = self._clock.now()
        for state, directory in (("enabled", self._paths.monitors_dir),
                                 ("draft", self._paths.drafts_dir)):
            path = directory / f"{name}.toml"
            if not path.exists():
                continue
            text = path.read_text()
            entry: dict = {"tz": _tz_name(now), "name": name, "path": str(path),
                           "toml": text}
            try:
                d = loader.load_text(text)
                entry["state"] = (state if d.enabled or state == "draft"
                                  else "disabled")
                entry["valid"] = True
                entry["trends"] = [
                    {"id": p.id, "kind": p.kind, "title": p.title}
                    for p in d.trends
                ]
            except loader.ValidationError as e:
                entry["state"] = state
                entry["valid"] = False
                entry["errors"] = e.errors
            q = self._query()
            if q is not None:
                entry["load_history"] = [
                    {"loaded_ts": r["loaded_ts"], "hash": r["hash"]}
                    for r in q._conn.execute(
                        "SELECT loaded_ts, hash FROM monitor_loads "
                        "WHERE monitor = ? ORDER BY loaded_ts DESC", (name,))
                ]  # PM-07
            return entry
        return _err("not_found",
                    f"no {name}.toml in monitors_dir or drafts_dir",
                    "list_monitors shows what exists; monitor_paths shows "
                    "where definitions live")

    def monitor_paths(self) -> dict:
        """MC-06: the JSON form of `ftmon paths` (CL-06) — paths only,
        never contents or credentials."""
        p = self._paths
        return {
            "config_dir": str(p.config_dir),
            "config_file": str(p.config_file),
            "monitors_dir": str(p.monitors_dir),
            "drafts_dir": str(p.drafts_dir),
            "actions_dir": str(p.actions_dir),
            "check_registry": str(p.check_registry_file),
            "data_dir": str(p.data_dir),
            "db_file": str(p.db_file),
            "state_dir": str(p.state_dir),
        }

    def diagnose_monitor(self, name: str) -> dict:
        """MC-06: answer "why isn't this monitor running?" in one call:
        location, validity, load state, and (external) alias trust — as
        booleans and categories, never registry argv (SE-07)."""
        now = self._clock.now()
        out: dict = {"tz": _tz_name(now), "name": name}
        path = None
        for found, directory in (("enabled", self._paths.monitors_dir),
                                 ("draft", self._paths.drafts_dir)):
            candidate = directory / f"{name}.toml"
            if candidate.exists():
                path, out["found"] = candidate, found
                break
        if path is None:
            out["found"] = "missing"
            out["hint"] = (f"no {name}.toml in monitors_dir or drafts_dir — "
                           "see monitor_paths for the layout")
            return out
        out["path"] = str(path)
        try:
            d = loader.load_text(path.read_text())
            out["valid"] = True
            if out["found"] == "enabled" and not d.enabled:
                out["found"] = "disabled"
        except loader.ValidationError as e:
            out["valid"] = False
            out["errors"] = e.errors
            d = None
        q = self._query()
        if q is not None:
            row = q._conn.execute(
                "SELECT loaded_ts, hash FROM monitor_loads WHERE monitor = ? "
                "ORDER BY loaded_ts DESC LIMIT 1", (name,)).fetchone()
            out["last_load"] = (
                {"hash": row["hash"], "age_s": round(now - row["loaded_ts"])}
                if row else None)  # never loaded (PM-07)
        if d is not None and d.source == "external":
            alias = d.source_options.get("check")
            check: dict = {"alias": alias}
            try:
                from ftmon.checks.registry import load as load_registry
                from ftmon.checks.trust import trusted_executable_path

                registry = load_registry(self._paths.check_registry_file,
                                         paths=self._paths)
                spec = registry.get(alias)
                check["registered"] = spec is not None
                if spec is not None:
                    check["executable_trusted"] = trusted_executable_path(
                        spec.argv[0])
            except (OSError, ValueError) as e:
                # Registry errors expose only a stable category (SE-07).
                check["registered"] = False
                check["registry_error"] = str(e)[:120]
            out["check"] = check
        return out

    def validate_monitor(self, toml_text: str) -> dict:
        try:
            d = loader.load_text(toml_text)
        except loader.ValidationError as e:
            return {"ok": False, "errors": e.errors}
        return {"ok": True, "name": d.name, "normalized": d.normalized_toml}

    def define_monitor(self, toml_text: str) -> dict:
        try:
            draft = manage.write_draft(self._paths, toml_text)
        except manage.ManageError as e:
            return _manage_err(e)
        return {
            "draft_path": str(draft),
            "approval_hint": ("pending approval: run `ftmon monitor approve "
                              f"{draft.stem}` or use the web UI — drafts are "
                              "never loaded by the daemon (MD-05)"),
            "next_steps": [
                {"via": "cli",
                 "action": f"ftmon monitor approve {draft.stem}"},
                {"via": "web",
                 "action": f"approve draft {draft.stem!r} on the "
                           "Monitors page"},
            ],
        }

    def ack_incident(self, id: int, note=None) -> dict:  # noqa: A002
        if not self._paths.db_file.exists():
            return self._no_db()
        conn = connect(self._paths.db_file)
        try:
            ok = SmallWrites(conn).ack(int(id), by="mcp",
                                       ts=self._clock.now(), note=note)
            row = conn.execute("SELECT * FROM incidents WHERE id = ?",
                               (int(id),)).fetchone()
        finally:
            conn.close()
        if row is None:
            return _err("not_found", f"no incident #{id}",
                        "list_incidents shows what exists")
        if not ok:
            return _err("invalid_params",
                        f"incident #{id} is {row['state']}, not open",
                        "only open incidents can be acked")
        return {"ok": True, "incident": dict(row)}


TOOL_NAMES = (  # MC-01: frozen; test_mcp asserts the server exposes exactly these
    "get_status", "query_metrics", "top_consumers", "get_process_history",
    "list_events", "list_incidents", "explain_incident", "list_monitors",
    "get_monitor", "monitor_paths", "diagnose_monitor",  # MC-06 (SPEC v0.18)
    "validate_monitor", "define_monitor", "ack_incident",
)


def _guide_text() -> str:
    guide = Path(__file__).resolve().parents[2] / "docs" / "definitions.md"
    try:
        return guide.read_text()
    except OSError:
        return ("definitions guide not found at " + str(guide) +
                " — see the ftmon repository's docs/definitions.md")


def build_server(paths: Paths):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("ftmon")
    api = McpApi(paths)

    server.tool(name="get_status",
                description="Daemon liveness, monitors, open incidents, "
                "self metrics")(api.get_status)
    server.tool(name="query_metrics",
                description="Time-series data; resolution auto-chosen; range "
                'like "90m" or [iso, iso]')(api.query_metrics)
    server.tool(name="top_consumers",
                description="Ranked cpu|rss|io consumers over a range")(
                api.top_consumers)
    server.tool(name="get_process_history",
                description="Metrics + lifecycle for processes matching a "
                "name or pid")(api.get_process_history)
    server.tool(name="list_events",
                description="Stored journal/system events")(api.list_events)
    server.tool(name="list_incidents",
                description="Incidents and episodes")(api.list_incidents)
    server.tool(name="explain_incident",
                description="Full story of one incident: rule, data, events, "
                "history")(api.explain_incident)
    server.tool(name="list_monitors",
                description="All monitor definitions incl. drafts")(
                api.list_monitors)
    server.tool(name="get_monitor",
                description="One definition with validation status and load "
                "history")(api.get_monitor)
    server.tool(name="monitor_paths",
                description="Resolved filesystem layout for authoring: "
                "monitors, drafts, actions, check registry, database "
                "(MC-06)")(api.monitor_paths)
    server.tool(name="diagnose_monitor",
                description="Why isn't this monitor running? Location, "
                "validation, load state, and external-alias trust in one "
                "call")(api.diagnose_monitor)
    server.tool(name="validate_monitor",
                description="Validate a monitor TOML without writing "
                "anything")(api.validate_monitor)
    server.tool(name="define_monitor",
                description="Validate and save a monitor TOML as a draft. "
                "Drafts are never loaded by the daemon: a human must approve "
                "via `ftmon monitor approve <name>` or the web UI Monitors "
                "page — the response's next_steps repeats both")(
                api.define_monitor)
    server.tool(name="ack_incident",
                description="Acknowledge an incident (stops re-notifying, "
                "keeps watching)")(api.ack_incident)

    @server.resource("ftmon://docs/definitions",
                     description="The monitor-definition reference (DO-01)")
    def definitions_guide() -> str:
        return _guide_text()

    return server


def run(args) -> int:
    """`ftmon mcp` — stdio server (SPEC section 11)."""
    build_server(get_paths()).run()
    return 0

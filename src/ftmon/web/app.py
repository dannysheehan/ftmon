"""Server-rendered, loopback-only web dashboard (UI-01..17, SE-02)."""

from __future__ import annotations

import json
import math
import mimetypes
import sqlite3
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from ftmon import __version__, glance
from ftmon.clock import SystemClock
from ftmon.definitions import loader
from ftmon.expr import ExprError, parse_duration
from ftmon.model import severity_name
from ftmon.paths import Paths, get_paths
from ftmon.sources.base import SOURCE_DECLS
from ftmon.store.db import connect
from ftmon.store.doctor import catalog_report, dispatch_health
from ftmon.store.query import Query

# macOS 12's stdlib maps .ico to image/x-icon while Linux maps it to the
# registered IANA spelling. Keep the offline response contract deterministic.
mimetypes.add_type("image/vnd.microsoft.icon", ".ico", strict=True)

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)
_jinja = Environment(loader=PackageLoader("ftmon.web", "templates"),
                     autoescape=select_autoescape(("html", "xml")))


@dataclass(frozen=True)
class MonitorTile:
    """Fully composed tile state; Jinja must not reimplement UI-14 policy."""

    name: str
    description: str
    enabled: bool
    state: str
    icon: str
    label: str
    incident_count: int
    max_severity: int | None
    trends: tuple
    glance: TileGlance | None


@dataclass(frozen=True)
class TileGlanceThreshold:
    label: str
    value: str
    raw: float
    mark_pct: float | None  # 0..100 along the meter; None when no meter


@dataclass(frozen=True)
class TileGlanceMeter:
    """SVG meter geometry (SE-02: no inline CSS). Percent = 0..100; else 0..limit."""

    value: float
    scale_max: float  # 100 for percent; otherwise max declared threshold
    fill_pct: float  # 0..100 along the SVG axis (clamped)
    show_full_mark: bool  # percent meters mark 100%; limit meters use threshold ticks
    fill_tone: str  # ok|warn|error from value vs thresholds (not UI-14 tile state)


@dataclass(frozen=True)
class TileGlance:
    entity_id: str
    value: str
    thresholds: tuple[TileGlanceThreshold, ...]
    meter: TileGlanceMeter | None


# Icon + visible text per UI-14 state; color alone never carries health (UI-09).
_STATE_PRESENTATION = {
    "clear": ("✓", "clear"),
    "warning": ("▲", "warning"),
    "error": ("✖", "error"),
    "disabled": ("●", "disabled"),
    "unknown": ("?", "unknown"),
}


# Attention-first scan order; UI-14 state itself stays computed above (DESIGN §15.3).
_TILE_STATE_ORDER = {
    "config-error": 0,
    "error": 1,
    "warning": 2,
    "unknown": 3,
    "disabled": 4,
    "clear": 5,
}


def _sort_tiles(tiles: list[MonitorTile]) -> list[MonitorTile]:
    return sorted(
        tiles, key=lambda tile: (_TILE_STATE_ORDER.get(tile.state, 9), tile.name)
    )


def _needs_attention(tile: MonitorTile) -> bool:
    """Keep intentionally inactive monitors quiet unless they retain an incident."""
    return tile.state not in {"clear", "disabled"} or tile.incident_count > 0


def _tile_summary(tiles: list[MonitorTile]) -> dict:
    """Dashboard strip aggregates derived from composed tiles, not a second policy."""
    worst = None
    for tile in tiles:
        if tile.max_severity is not None:
            worst = tile.max_severity if worst is None else max(worst, tile.max_severity)
    return {
        "attention_count": sum(1 for tile in tiles if _needs_attention(tile)),
        "clear_count": sum(1 for tile in tiles if tile.state == "clear"),
        "disabled_count": sum(
            1 for tile in tiles if tile.state == "disabled" and not tile.incident_count
        ),
        "worst_severity": worst,
    }


class SecurityMiddleware(BaseHTTPMiddleware):
    """DNS-rebinding, CSRF and response hardening for the loopback UI (UI-08)."""

    def __init__(self, app, port: int):
        super().__init__(app)
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    async def dispatch(self, request: Request, call_next):
        if request.headers.get("host") not in self.allowed_hosts:
            response = Response("Bad Host", status_code=400)
        elif request.method == "POST" and request.headers.get("origin") not in self.allowed_origins:
            response = Response("Bad Origin", status_code=403)
        else:
            response = await call_next(request)
        return _apply_security_headers(response)


def _apply_security_headers(response: Response) -> Response:
    """Keep operational and synthetic middleware hardening identical (UI-08)."""
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return response


def _render(name: str, request: Request, **context) -> HTMLResponse:
    context.update(
        request=request,
        severity_name=severity_name,
        utc_iso=lambda ts: datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC"),
        demo=getattr(request.app.state, "demo", False),
    )
    return HTMLResponse(_jinja.get_template(name).render(**context))


@contextmanager
def _query(request: Request, *, writable: bool = False):
    paths = request.app.state.paths
    if not paths.db_file.exists():
        yield None
        return
    if getattr(request.app.state, "demo", False):
        # immutable=1 tells SQLite never to create WAL/SHM sidecars. This is a
        # second write barrier beneath the GET-only route construction.
        conn = sqlite3.connect(f"file:{paths.db_file}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = connect(paths.db_file, readonly=not writable)
    try:
        yield Query(conn) if not writable else conn
    finally:
        conn.close()


def _status(request: Request, q: Query | None) -> dict:
    now = request.app.state.clock.now()
    if q is None:
        return {"daemon_stale": True, "last_tick_age_s": None, "db_bytes": 0,
                "db_allocated_bytes": 0, "db_used_bytes": 0,
                "db_freelist_bytes": 0, "db_freelist_pages": 0,
                "open_incidents": 0}
    out = q.status(now=now)
    out["daemon_stale"] = glance.daemon_stale(out["last_tick_age_s"])
    return out


async def dashboard(request: Request):
    paths = request.app.state.paths
    with _query(request) as q:
        if getattr(request.app.state, "demo", False):
            defs, errors = _demo_definitions(q)
        else:
            defs, errors = loader.load_dir(
                paths.monitors_dir,
                actions_dir=paths.actions_dir,
                require_actions=True,
                check_aliases=glance.check_aliases(paths),
                require_checks=True,
            )
        status = _status(request, q)
        incidents = [] if q is None else q.incidents(state=None)
        incidents = [r for r in incidents if r["state"] != "cleared"][:10]
        tiles = (
            _demo_monitor_tiles(defs, q, request.app.state.clock.now())
            if getattr(request.app.state, "demo", False)
            else _monitor_tiles(defs, errors, q, status, request.app.state.clock.now())
        )
        summary = _tile_summary(tiles)
        attention = [tile for tile in tiles if _needs_attention(tile)]
        clear = [tile for tile in tiles if tile.state == "clear"]
        disabled = [
            tile for tile in tiles
            if tile.state == "disabled" and not tile.incident_count
        ]
        # Same composer as /self, so the strip and the panel cannot disagree
        # about whether the daemon is inside its own budgets (#105).
        self_metrics = {} if q is None else {
            r["metric"]: r["value"] for r in q._conn.execute(
                "SELECT se.metric, s.value FROM series se "
                "JOIN samples s ON s.series_id=se.id "
                "WHERE se.monitor='self' AND s.ts="
                "(SELECT MAX(x.ts) FROM samples x WHERE x.series_id=se.id)"
            )
        }
        self_params = next(
            (dict(d.parameters) for d in defs if d.name == "self"), {}
        )
        db_warn_mb = self_params.get("db_warn_mb") or self_params.get("db_budget_mb")
        self_budget = _self_budget_stats(
            _self_panel(self_metrics, self_params, None),
            db_used_bytes=status.get("db_used_bytes"),
            db_warn_bytes=(db_warn_mb or 0) * 1024 * 1024 or None,
        )
    return _render(
        "dashboard.html", request, title="Dashboard", status=status,
        tiles=tiles, attention_tiles=attention, clear_tiles=clear,
        disabled_tiles=disabled, self_budget=self_budget,
        summary=summary, config_errors=errors, incidents=incidents,
        refresh_ms=5000,
    )


def _demo_definitions(q: Query | None):
    """Recover synthetic presentation metadata without reading host config."""
    if q is None:
        return [], []
    rows = q._conn.execute(
        "SELECT normalized FROM monitor_loads ml WHERE loaded_ts=("
        "SELECT MAX(loaded_ts) FROM monitor_loads WHERE monitor=ml.monitor) "
        "ORDER BY monitor"
    ).fetchall()
    definitions = []
    for row in rows:
        try:
            definitions.append(loader.load_text(row["normalized"], "<synthetic-demo>"))
        except loader.ValidationError:
            # The seeded builder stores only a JSON presentation summary, not
            # user configuration. Load matching packaged metadata so public
            # Trends acquire declared meaning without consulting host files.
            try:
                summary = json.loads(row["normalized"])
                resource = Path(__file__).parents[1] / "definitions/builtins" / (
                    f"{summary['name']}.toml"
                )
                mdef = loader.load_text(resource.read_text(), "<packaged-demo-metadata>")
                definitions.append(replace(mdef, enabled=bool(summary["enabled"])))
            except (KeyError, OSError, TypeError, ValueError, loader.ValidationError):
                # The builder verifies coverage; malformed synthetic metadata
                # is omitted rather than exposing filesystem diagnostics.
                continue
    return definitions, []


def _demo_monitor_tiles(definitions, q: Query | None, now: float) -> list[MonitorTile]:
    """Render seeded examples independently of real daemon precedence (UI-16)."""
    if q is None:
        return []
    row = q._conn.execute(
        "SELECT value FROM meta WHERE key='demo_monitor_states'"
    ).fetchone()
    summaries = json.loads(row["value"]) if row else {}
    live = {
        row["monitor"]: (row["count"], row["max_severity"])
        for row in q._conn.execute(
            "SELECT monitor,COUNT(*) count,MAX(severity) max_severity "
            "FROM incidents WHERE state!='cleared' GROUP BY monitor"
        )
    }
    tiles = []
    for mdef in definitions:
        state = summaries.get(mdef.name, "unknown")
        icon, label = _STATE_PRESENTATION.get(state, ("?", "unknown"))
        readout = _tile_glance(glance.reading(mdef, q, state, now))
        incident_count, maximum = live.get(mdef.name, (0, None))
        tiles.append(MonitorTile(
            mdef.name, mdef.description, mdef.enabled,
            state, icon, label, incident_count, maximum, mdef.trends, readout,
        ))
    return _sort_tiles(tiles)


def _monitor_tiles(
    defs, errors, q: Query | None, status: dict, now: float
) -> list[MonitorTile]:
    """Apply fixed health precedence to evidence and live incidents (UI-14)."""
    live_by_monitor = glance.open_incidents_by_monitor(q)

    tiles = []
    for mdef in defs:
        live = live_by_monitor.get(mdef.name, [])
        maximum = max((row["severity"] for row in live), default=None)
        state = glance.health_state(
            stale=status["daemon_stale"],
            has_evidence=glance.has_evidence(q, mdef),
            enabled=mdef.enabled,
            max_severity=maximum,
        )
        icon, label = _STATE_PRESENTATION[state]
        readout = _tile_glance(glance.reading(mdef, q, state, now))
        tiles.append(MonitorTile(
            mdef.name, mdef.description, mdef.enabled, state, icon, label,
            len(live), maximum, mdef.trends, readout,
        ))

    # Invalid files have no MonitorDef; omitting them would hide the highest
    # precedence configuration state from the at-a-glance surface.
    for path, error in errors:
        tiles.append(MonitorTile(
            path.stem, str(error)[:200], False, "config-error", "?",
            "config error", 0, None, (), None,
        ))
    return _sort_tiles(tiles)


def _format_glance_value(value: float, unit: str) -> str:
    """Format bounded numeric metadata without accepting a template (MD-12)."""
    number = format(value, ".3g")
    return f"{number}%" if unit == "percent" else f"{number} {unit}"


_WARN_LABELS = frozenset({"warn", "warning", "sustained"})
_ERROR_LABELS = frozenset({"error", "crit", "critical"})


def _meter_fill_tone(
    value: float, thresholds: tuple[tuple[str, float], ...]
) -> str:
    """Color the fill from the reading vs declared limits (higher-is-worse)."""
    if not math.isfinite(value):
        return "ok"
    warn_at = None
    error_at = None
    for label, raw in thresholds:
        if not math.isfinite(raw):
            continue
        key = label.lower()
        if key in _WARN_LABELS:
            warn_at = raw if warn_at is None else min(warn_at, raw)
        elif key in _ERROR_LABELS:
            error_at = raw if error_at is None else min(error_at, raw)
    if error_at is not None and value >= error_at:
        return "error"
    if warn_at is not None and value >= warn_at:
        return "warn"
    return "ok"


def _glance_meter(
    value: float,
    unit: str,
    thresholds: tuple[tuple[str, float], ...],
) -> TileGlanceMeter | None:
    """Build meter geometry: percent → 0..100; other units → 0..highest threshold."""
    if not math.isfinite(value):
        return None
    # CSP (SE-02) blocks inline style=; fill/marks are drawn via SVG attributes.
    threshold_raws = tuple(raw for _, raw in thresholds)
    tone = _meter_fill_tone(value, thresholds)
    if unit == "percent":
        return TileGlanceMeter(
            value=value,
            scale_max=100.0,
            fill_pct=min(100.0, max(0.0, value)),
            show_full_mark=True,
            fill_tone=tone,
        )
    finite = tuple(raw for raw in threshold_raws if math.isfinite(raw) and raw > 0)
    if not finite:
        return None
    scale_max = max(finite)
    return TileGlanceMeter(
        value=value,
        scale_max=scale_max,
        fill_pct=min(100.0, max(0.0, (value / scale_max) * 100.0)),
        show_full_mark=False,
        fill_tone=tone,
    )


def _tile_glance(readout: glance.GlanceReading | None) -> TileGlance | None:
    """Format a shared reading; selection policy stays in `ftmon.glance` (UI-17)."""
    if readout is None:
        return None
    threshold_pairs = tuple(
        (threshold.label, threshold.value) for threshold in readout.thresholds
    )
    meter = _glance_meter(readout.value, readout.unit, threshold_pairs)
    thresholds = tuple(
        TileGlanceThreshold(
            label=threshold.label,
            value=_format_glance_value(threshold.value, readout.unit),
            raw=threshold.value,
            mark_pct=(
                None
                if (
                    meter is None
                    or not math.isfinite(threshold.value)
                    or meter.scale_max <= 0
                )
                else min(100.0, max(0.0, (threshold.value / meter.scale_max) * 100.0))
            ),
        )
        for threshold in readout.thresholds
    )
    return TileGlance(
        entity_id=readout.entity_id,
        value=_format_glance_value(readout.value, readout.unit),
        thresholds=thresholds,
        meter=meter,
    )


async def incidents(request: Request):
    state = request.query_params.get("state") or None
    monitor = request.query_params.get("monitor")
    with _query(request) as q:
        rows = [] if q is None else q.incidents(state=state, monitor=monitor)
        status = _status(request, q)
    return _render("incidents.html", request, title="Incidents", rows=rows, status=status,
                   selected_monitor=monitor, refresh_ms=5000)


async def incident_detail(request: Request):
    iid = int(request.path_params["id"])
    with _query(request) as q:
        row = None if q is None else q._conn.execute(
            "SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
        history = [] if q is None else q._conn.execute(
            "SELECT * FROM incident_history WHERE incident_id=? ORDER BY seq", (iid,)).fetchall()
        entity_row = None if row is None else q._conn.execute(
            "SELECT attrs FROM entities WHERE monitor=? AND entity_id=? LIMIT 1",
            (row["monitor"], row["entity_id"])).fetchone()
        status = _status(request, q)
    if row is None:
        return Response("Incident not found", status_code=404)
    # SA-09 SHOULD: this is a loopback, single-user surface (NG-05/SE-04), so
    # showing the sampled attrs (including cmdline) here is in-posture; only
    # notification content stays governed by SE-04's raw-cmdline ban.
    entity_attrs = json.loads(entity_row["attrs"]) if entity_row and entity_row["attrs"] else {}
    trend_profile = next((
        profile for mdef, profile in _trend_catalog(request)
        if mdef.name == row["monitor"]
        and (profile.incident_group is None or profile.incident_group == row["grp"])
    ), None)
    return _render("incident.html", request, title=f"Incident #{iid}", row=row,
                   history=history, status=status, trend_profile=trend_profile,
                   entity_attrs=entity_attrs, refresh_ms=5000)


async def ack(request: Request):
    from ftmon.store.query import SmallWrites

    iid = int(request.path_params["id"])
    form = parse_qs((await request.body()).decode(errors="replace"))
    with _query(request, writable=True) as conn:
        if conn is None:
            return Response("Database not found", status_code=404)
        note = form.get("note", [None])[0]
        ok = SmallWrites(conn).ack(iid, "web", request.app.state.clock.now(), note)
    if not ok:
        return Response("Incident is not open", status_code=409)
    return RedirectResponse(f"/incidents/{iid}", status_code=303)


async def metrics(request: Request):
    """Explore any persisted series with cascading, URL-backed choices (UI-02).

    The database is the catalog: definitions can change while history remains.
    Selectors describe series with observations in this range so stale metadata
    cannot lead the default path to an empty graph.
    """
    p = request.query_params
    monitor, entity, metric = p.get("monitor"), p.get("entity"), p.get("metric")
    statistic = p.get("statistic", "avg")
    if statistic not in {"avg", "min", "max", "last"}:
        return Response("Statistic must be avg, min, max, or last", status_code=400)
    try:
        if "hours" in p:  # preserve M5 bookmarks while range becomes canonical
            seconds = float(p["hours"]) * 3600
            range_text = f"{p['hours']}h"
        else:
            # Fresh installs have raw samples immediately but no completed
            # five-minute rollups yet. Six hours selects the raw tier, so the
            # default explorer never looks empty while data is arriving.
            range_text = p.get("range", "6h")
            seconds = parse_duration(range_text)
    except (ValueError, ExprError):
        return Response("Invalid range; use values such as 6h, 7d, or 30d", status_code=400)
    if not 900 <= seconds <= 400 * 86400:
        return Response("Range must be between 15m and 400d", status_code=400)
    now = request.app.state.clock.now()
    with _query(request) as q:
        choices = [] if q is None else list(
            q.series_catalog(now=now, start=now-seconds, end=now)
        )
        # Preserve an exact bookmark after its observations expire; it gets an
        # honest empty state rather than silently changing the selected series.
        requested = (monitor, entity, metric)
        if q is not None and all(requested) and not any(
            (row["monitor"], row["entity_id"], row["metric"]) == requested
            for row in choices
        ):
            exact = q._conn.execute(
                "SELECT monitor, entity_id, metric FROM series "
                "WHERE monitor=? AND entity_id=? AND metric=?",
                requested,
            ).fetchone()
            if exact is not None:
                choices.append(exact)
        monitors = sorted({row["monitor"] for row in choices})
        monitor = monitor if monitor in monitors else (monitors[0] if monitors else None)
        entities = sorted({row["entity_id"] for row in choices if row["monitor"] == monitor})
        entity = entity if entity in entities else (entities[0] if entities else None)
        metrics = sorted({row["metric"] for row in choices
                          if row["monitor"] == monitor and row["entity_id"] == entity})
        metric = metric if metric in metrics else (metrics[0] if metrics else None)
        rows = [] if q is None or not monitor or not metric else q.series(
            monitor, metric, now=now, start=now-seconds, end=now,
            entity_id=entity, max_points=2000, statistic=statistic,
            include_envelope=True,
        )
        payload = None if q is None or not rows else _series_payload(
            request.app.state.paths, q, rows[0], monitor, entity, metric,
            statistic, now-seconds, now,
        )
    selected = {"monitor": monitor, "entity": entity, "metric": metric,
                "range": range_text, "statistic": statistic}
    return _render(
        "metrics.html", request, title="Metrics", choices=choices,
        monitors=monitors, entities=entities, metrics=metrics,
        payload=payload, selected=selected,
    )


def _points_with_gaps(
    points, resolution: str, *, downsampled: bool = False
) -> list[list[float | None]]:
    """Insert explicit nulls where absence is knowable (UI-13).

    uPlot otherwise joins the surrounding values and visually claims a
    measurement through suspend/missing buckets. Raw cadence is inferred from
    observed deltas; fixed rollup tiers use their normative bucket width.
    """
    pairs = [[p.ts, p.value] for p in points]
    # Once LTTB intentionally removes observations, cadence gaps in the result
    # no longer prove missing collection. Adding nulls there would turn normal
    # downsampling into a false outage (TS-11).
    if downsampled:
        return pairs
    if len(points) < 3:
        return pairs
    if resolution == "5m":
        step = 300
    elif resolution == "1h":
        step = 3600
    else:
        deltas = [
            b.ts - a.ts for a, b in zip(points, points[1:], strict=False) if b.ts > a.ts
        ]
        step = min(deltas, default=0)
    if step <= 0:
        return pairs
    out: list[list[float | None]] = [pairs[0]]
    for previous, current in zip(points, points[1:], strict=False):
        if current.ts - previous.ts > step * 1.5:
            out.append([previous.ts + step, None])
        out.append([current.ts, current.value])
    return out


def _metric_metadata(paths: Paths, monitor: str, metric: str) -> tuple[str, list[dict]]:
    """Resolve units conservatively and find declared interpretations.

    Raw units are source contracts. Derived units only exist when a profile
    explicitly declares them; guessing from names would violate UI-13.
    """
    defs, _errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    mdef = next((item for item in defs if item.name == monitor), None)
    unit = "value"
    matching = []
    if mdef:
        decl = SOURCE_DECLS.get(mdef.source)
        if decl:
            raw = next((item for item in decl.metrics if item.name == metric), None)
            if raw:
                unit = raw.unit
        for profile in mdef.trends:
            panel = None
            if metric == profile.value_metric:
                panel, unit = "value", profile.value_unit
            elif metric == profile.rate_metric:
                panel, unit = "rate", profile.rate_unit
            elif metric == profile.confidence_metric:
                panel, unit = "confidence", "fraction"
            elif metric == profile.remaining_metric:
                panel = "remaining"
            if panel:
                matching.append({"id": profile.id, "title": profile.title, "panel": panel})
    return unit, matching


def _baseline_runs(points) -> list[list[list[float]]]:
    """Partition native five-minute evidence so the renderer cannot bridge gaps."""
    runs: list[list[list[float]]] = []
    for point in points:
        pair = [point.ts, point.value]
        if not runs or point.ts - runs[-1][-1][0] != 300:
            runs.append([pair])
        else:
            runs[-1].append(pair)
    return runs


def _chart_y_domain(unit: str, *point_sets) -> list[float] | None:
    """Return deterministic visible bounds including plugin-painted values (UI-13)."""
    values = [
        float(point[1])
        for points in point_sets
        for point in points
        if point[1] is not None and math.isfinite(point[1])
    ]
    if not values:
        return None
    lower, upper = min(values), max(values)
    if unit == "percent":
        return [min(0.0, lower), max(100.0, upper)]
    if lower == upper:
        padding = max(abs(lower) * 0.05, 1.0)
    else:
        padding = (upper - lower) * 0.05
    return [lower - padding, upper + padding]


def _series_payload(
    paths: Paths, q: Query, result, monitor: str, entity: str, metric: str,
    statistic: str, start: float, end: float,
) -> dict:
    """Build the shared Metrics chart/data contract (UI-13/TS-11)."""
    unit, matching = _metric_metadata(paths, monitor, metric)
    incidents = [dict(row) for row in q._conn.execute(
        "SELECT id,state,severity,opened_ts,last_change_ts,cleared_ts,grp "
        "FROM incidents WHERE monitor=? AND entity_id=? "
        "AND (cleared_ts IS NULL OR cleared_ts>=?) AND opened_ts<=? ORDER BY opened_ts",
        (monitor, entity, round(start), round(end)),
    )]
    values = [point.value for point in result.points]
    summary = {
        "current": values[-1] if values else None,
        "change": values[-1] - values[0] if len(values) >= 2 else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "trend": ("rising" if len(values) >= 2 and values[-1] > values[0]
                  else "falling" if len(values) >= 2 and values[-1] < values[0]
                  else "steady" if len(values) >= 2 else "unavailable"),
    }
    panel_points = _points_with_gaps(
        result.points, result.resolution, downsampled=result.downsampled
    )
    panel_lower = _points_with_gaps(
        result.lower or [], result.resolution, downsampled=result.downsampled
    )
    panel_upper = _points_with_gaps(
        result.upper or [], result.resolution, downsampled=result.downsampled
    )
    baseline_history = q.baseline_history(
        monitor, entity, metric, start=start, end=end,
    )
    baseline = None
    baseline_points = []
    if baseline_history is not None:
        current = baseline_history.baseline
        baseline_points = [
            [point.ts, point.value] for point in baseline_history.points
        ]
        baseline = {
            "level": current.level,
            "updates": current.updates,
            "required_updates": current.required_updates,
            "coverage": current.coverage,
            "ready": current.ready,
            "updated_at": current.updated_at,
            "half_life_s": current.half_life_s,
            "points": baseline_points,
            "runs": _baseline_runs(baseline_history.points),
            "history_truncated": baseline_history.history_truncated,
        }
    return {
        "monitor": monitor, "entity": entity, "metric": metric, "unit": unit,
        "statistic": statistic, "resolution": result.resolution,
        "range": {"start": start, "end": end},
        "panel": {
            "points": panel_points,
            "lower": panel_lower,
            "upper": panel_upper,
            "y_domain": _chart_y_domain(
                unit, panel_points, panel_lower, panel_upper, baseline_points
            ),
        },
        "incidents": incidents, "summary": summary, "baseline": baseline,
        "matching_trends": matching,
    }


async def series_api(request: Request):
    """Return one selected series for non-template consumers (TS-11)."""
    p = request.query_params
    monitor, entity, metric = p.get("monitor"), p.get("entity"), p.get("metric")
    statistic = p.get("statistic", "avg")
    if not monitor or not entity or not metric:
        return Response("monitor, entity, and metric are required", status_code=400)
    if statistic not in {"avg", "min", "max", "last"}:
        return Response("Statistic must be avg, min, max, or last", status_code=400)
    range_text = p.get("range", "24h")
    try:
        seconds = parse_duration(range_text)
    except ExprError:
        return Response("Invalid range", status_code=400)
    if not 900 <= seconds <= 400 * 86400:
        return Response("Range must be between 15m and 400d", status_code=400)
    now = request.app.state.clock.now()
    with _query(request) as q:
        if q is None:
            return Response("Database not found", status_code=404)
        rows = q.series(
            monitor, metric, now=now, start=now-seconds, end=now,
            entity_id=entity, max_points=2000, statistic=statistic,
            include_envelope=True,
        )
        if not rows:
            return Response("Series not found", status_code=404)
        payload = _series_payload(
            request.app.state.paths, q, rows[0], monitor, entity, metric,
            statistic, now-seconds, now,
        )
    return JSONResponse(payload)


def _baseline_params(request: Request) -> tuple[dict, int, str | None] | Response:
    """Validate the shared, bounded baseline-list query contract (UI-02)."""
    p = request.query_params
    filters = {
        name: p.get(name) or None for name in ("monitor", "entity", "metric")
    }
    ready_text = p.get("ready")
    if ready_text is None or ready_text == "":
        filters["ready"] = None
    elif ready_text in {"true", "false"}:
        filters["ready"] = ready_text == "true"
    else:
        return Response("ready must be true or false", status_code=400)
    try:
        limit = int(p.get("limit", "100"))
    except ValueError:
        return Response("limit must be an integer from 1 to 500", status_code=400)
    if not 1 <= limit <= 500:
        return Response("limit must be an integer from 1 to 500", status_code=400)
    return filters, limit, p.get("cursor") or None


async def baselines(request: Request):
    """List every stored learned baseline without adding reset authority (UI-02)."""
    parsed = _baseline_params(request)
    if isinstance(parsed, Response):
        return parsed
    filters, limit, cursor = parsed
    with _query(request) as q:
        database_available = q is not None
        try:
            page = (
                None
                if q is None
                else q.list_baselines(
                    monitor=filters["monitor"], entity_id=filters["entity"],
                    metric=filters["metric"], ready=filters["ready"],
                    limit=limit, cursor=cursor,
                )
            )
        except ValueError as exc:
            return Response(str(exc), status_code=400)
    next_url = None
    if page is not None and page.next_cursor is not None:
        params = {
            key: value for key, value in filters.items() if value is not None
        }
        if "ready" in params:
            params["ready"] = str(params["ready"]).lower()
        params.update({"limit": limit, "cursor": page.next_cursor})
        next_url = "/baselines?" + urlencode(params)
    rows = [] if page is None else [
        {
            "monitor": row.monitor, "entity_id": row.entity_id, "metric": row.metric,
            "level": row.level, "updates": row.updates,
            "required_updates": row.required_updates, "coverage": row.coverage,
            "ready": row.ready, "updated_at": row.updated_at,
            "metrics_url": "/metrics?" + urlencode({
                "monitor": row.monitor, "entity": row.entity_id, "metric": row.metric,
            }),
        }
        for row in page.baselines
    ]
    return _render(
        "baselines.html", request, title="Baselines",
        rows=rows,
        next_url=next_url, selected=filters, limit=limit,
        database_available=database_available,
    )


def _trend_request(request: Request) -> tuple[str | None, float, float, str] | Response:
    """Normalize shareable trend range state in one place (UI-10/UI-12)."""
    entity = request.query_params.get("entity")
    range_text = request.query_params.get("range", "24h")
    try:
        seconds = parse_duration(range_text)
    except ExprError:
        return Response("Invalid range; use values such as 6h, 7d, or 30d", status_code=400)
    if not 900 <= seconds <= 400 * 86400:
        return Response("Range must be between 15m and 400d", status_code=400)
    now = request.app.state.clock.now()
    return entity, now - seconds, now, range_text


def _trend_catalog(request: Request):
    """Load profile owners together so cross-monitor IDs never collide."""
    if getattr(request.app.state, "demo", False):
        return [
            (mdef, profile)
            for mdef in request.app.state.demo_definitions
            for profile in mdef.trends
        ]
    paths = request.app.state.paths
    defs, _errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    return [(mdef, profile) for mdef in defs for profile in mdef.trends]


def _selected_profile(request: Request, monitor: str | None, profile_id: str | None):
    catalog = _trend_catalog(request)
    if monitor and profile_id:
        return next(((m, p) for m, p in catalog
                     if m.name == monitor and p.id == profile_id), None)
    return catalog[0] if catalog else None


async def disk_trend_api(request: Request):
    """JSON contract for synchronized historical disk panels (UI-10/DM-17)."""
    parsed = _trend_request(request)
    if isinstance(parsed, Response):
        return parsed
    entity, start, end, range_text = parsed
    if not entity:
        return Response("entity is required", status_code=400)
    selected = _selected_profile(request, "disk", "space-growth")
    mdef = selected[0] if selected else None
    filling_frac = mdef.parameters.get("filling_frac", 0.85) if mdef else 0.85
    with _query(request) as q:
        if q is None:
            return Response("Database not found", status_code=404)
        trend = q.disk_trend(
            entity, now=end, start=start, end=end, filling_frac=filling_frac,
        )
    trend["range"]["label"] = range_text
    trend["thresholds"] = {
        key: value for key, value in (mdef.parameters.items() if mdef else ())
        if key.startswith("space_") or key == "filling_frac"
    }
    return JSONResponse(trend)


async def trend_api(request: Request):
    """Generic declared-panel JSON contract (CA-10/UI-12)."""
    parsed = _trend_request(request)
    if isinstance(parsed, Response):
        return parsed
    entity, start, end, range_text = parsed
    monitor = request.query_params.get("monitor")
    profile_id = request.query_params.get("profile")
    selected = _selected_profile(request, monitor, profile_id)
    if not entity or selected is None:
        return Response("monitor, profile, and entity are required", status_code=400)
    mdef, profile = selected
    with _query(request) as q:
        if q is None:
            return Response("Database not found", status_code=404)
        trend = q.trend(
            mdef.name, entity, profile, now=end, start=start, end=end,
            parameters=mdef.parameters,
        )
    trend["range"]["label"] = range_text
    return JSONResponse(trend)


async def trends(request: Request):
    """Explore any declared trend profile through one SSR path (UI-12)."""
    parsed = _trend_request(request)
    if isinstance(parsed, Response):
        return parsed
    entity, start, end, range_text = parsed
    monitor = request.path_params.get("monitor") or request.query_params.get("monitor")
    profile_id = request.path_params.get("profile") or request.query_params.get("profile")
    selection = request.query_params.get("selection", "")
    if "/" in selection:
        monitor, profile_id = selection.split("/", 1)
    catalog = _trend_catalog(request)
    selected = _selected_profile(request, monitor, profile_id)
    mdef, profile = selected if selected else (None, None)
    with _query(request) as q:
        # `gone_ts` is the lifecycle authority, while recent last_seen also
        # protects discovery from legacy/null rows left by an earlier daemon
        # lifetime. Two intervals handles normal scheduling jitter; five
        # minutes matches CA-08's default disappearance grace.
        seen_since = end - max(300.0, 2 * mdef.interval_s) if mdef else None
        entities = [] if q is None or mdef is None else [
            row["entity_id"]
            for row in q.entities(mdef.name, alive_only=True, seen_since=seen_since)
        ]
        # Process history intentionally outlives the process, but putting every
        # exited identity in the primary selector makes a busy host unusable.
        # Preserve an explicit incident/bookmark target without advertising the
        # rest of the historical catalogue; Metrics remains the full-history
        # diagnostic surface (UI-12/UI-13).
        if entity and entity not in entities:
            entities.append(entity)
        entity = entity or (entities[0] if entities else None)
        trend = None if q is None or entity is None or profile is None else q.trend(
            mdef.name, entity, profile, now=end, start=start, end=end,
            parameters=mdef.parameters,
        )
    if trend:
        trend["range"]["label"] = range_text
    return _render(
        "trends.html", request, title="Trends", catalog=catalog,
        selected_monitor=mdef, selected_profile=profile, entities=entities,
        entity=entity, range_text=range_text, trend=trend,
    )


async def disks_redirect(request: Request):
    """Preserve M7 bookmarks while making Trends canonical (UI-12)."""
    entity = request.path_params.get("entity") or request.query_params.get("entity")
    params = {"range": request.query_params.get("range", "24h")}
    if entity:
        params["entity"] = entity
    return RedirectResponse(
        "/trends/disk/space-growth?" + urlencode(params), status_code=307
    )


async def events(request: Request):
    now = request.app.state.clock.now()
    p = request.query_params
    try:
        severity = min(4, max(0, int(p.get("severity", "0"))))
    except ValueError:
        severity = 0
    with _query(request) as q:
        rows = [] if q is None else q.events(start=now-86400, end=now,
            min_severity=severity, provider=p.get("provider"), limit=200)
    return _render(
        "events.html", request, title="Events", rows=rows, severity=severity,
        refresh_ms=5000,
    )


async def monitors(request: Request):
    paths = request.app.state.paths
    check_aliases = glance.check_aliases(paths)
    defs, errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True,
        check_aliases=check_aliases, require_checks=True,
    )
    drafts = []
    for path in sorted(paths.drafts_dir.glob("*.toml")) if paths.drafts_dir.exists() else []:
        try:
            drafts.append((path.stem, path.read_text(), loader.load_file(path), None))
        except Exception as exc:
            drafts.append((path.stem, path.read_text(), None, exc))
    return _render(
        "monitors.html", request, title="Monitors", monitors=defs,
        errors=errors, drafts=drafts, refresh_ms=15000,
    )


async def monitor_action(request: Request):
    # Keep definition writers outside module import time: the public demo
    # imports shared read handlers but must never import a mutation capability.
    from ftmon.definitions import manage

    name, action = request.path_params["name"], request.path_params["action"]
    paths = request.app.state.paths
    check_aliases = glance.check_aliases(paths)
    try:
        if action == "approve":
            manage.approve_draft(paths, name, check_aliases=check_aliases)
        elif action == "delete-draft":
            manage.delete_draft(paths, name)
        elif action in {"enable", "disable"}:
            manage.set_enabled(
                paths, name, action == "enable", check_aliases=check_aliases
            )
        else:
            return Response("Unknown action", status_code=404)
    except manage.ManageError as exc:
        return Response(str(exc), status_code=409)
    return RedirectResponse("/monitors", status_code=303)


# RB-01's normative targets. The self definition may carry a looser calibrated
# threshold (the Windows profile runs at 30% for measured sampler overhead), so
# the panel shows both: what this host alarms at, and what the spec asks for.
_RB01_CPU_PCT = 1.0
_RB01_RSS_BYTES = 100 * 1024 * 1024


def _budget_row(row_id, label, value, limit, unit, *, spec=None,
                higher_is_worse=True):
    """One panel row: the number, what it is measured against, and how close.

    `pct` is the true utilization and may exceed 100 — a CPU average of 6%
    against a 1.5% threshold is 400% of budget, and clamping that for display
    would under-report the breach it exists to show. `fill_pct` is clamped
    because it is SVG geometry, not a reading.

    Every row also carries text (UI-09): colour and bar length are never the
    only carriers.
    """
    if value is None:
        return {"id": row_id, "label": label, "value": None, "limit": limit,
                "unit": unit, "pct": None, "fill_pct": None,
                "tone": "unknown", "spec": spec}
    if not limit:
        # A missing *threshold* must not erase a known *measurement*: the
        # reading is reported without a comparison rather than as absent.
        return {"id": row_id, "label": label, "value": value, "limit": None,
                "unit": unit, "pct": None, "fill_pct": None,
                "tone": "unknown", "spec": spec}
    pct = 100.0 * value / limit
    tone = "ok"
    if higher_is_worse:
        tone = "error" if value >= limit else "warn" if pct >= 80 else "ok"
    return {"id": row_id, "label": label, "value": value, "limit": limit,
            "unit": unit, "pct": pct, "fill_pct": max(0.0, min(100.0, pct)),
            "tone": tone, "spec": spec}


def _self_panel(metrics: dict, params: dict, catalog: dict | None) -> dict:
    """Compose the operational Self panel (#105) from already-read values.

    Deliberately consumes metrics and thresholds rather than recomputing
    anything: the DM-05 arithmetic has one implementation in store.db and the
    catalog figures one in store.doctor, and a view that re-derived either
    would be the drift issue #104 removed.

    Missing metrics render as unavailable rather than zero. The stage timings
    #106 will add do not exist yet, and a panel that showed them as 0 would be
    asserting a measurement nobody made.
    """
    def m(name):
        value = metrics.get(name)
        return None if value is None else float(value)

    cpu_budget = params.get("cpu_budget_pct")
    rss_budget_mb = params.get("rss_budget_mb")
    db_warn_mb = params.get("db_warn_mb") or params.get("db_budget_mb")
    dm16 = (catalog or {}).get("dm16", {})
    return {
        "cpu": {
            "current": m("cpu_pct"),
            "avg10m": m("cpu_10m"),
            "row": _budget_row("cpu", "CPU", m("cpu_10m") if m("cpu_10m") is not None
                               else m("cpu_pct"), cpu_budget, "%",
                               spec=_RB01_CPU_PCT),
        },
        "memory": {
            "row": _budget_row(
                "memory", "Memory", m("rss_bytes"),
                (rss_budget_mb or 0) * 1024 * 1024 or None, "bytes",
                spec=_RB01_RSS_BYTES),
        },
        "database": {
            "used": m("db_used_bytes"),
            "allocated": m("db_allocated_bytes"),
            "freelist": m("db_freelist_bytes"),
            "headroom": m("db_headroom_bytes"),
            "row": _budget_row(
                "database", "Database (used pages)", m("db_used_bytes"),
                (db_warn_mb or 0) * 1024 * 1024 or None, "bytes"),
        },
        "catalog": {
            "entities_persisted": dm16.get("entities_persisted"),
            "max_entities_persisted": dm16.get("max_entities_persisted"),
            "series_persisted": dm16.get("series_persisted"),
            "max_series_persisted": dm16.get("max_series_persisted"),
            "entities_not_gone": dm16.get("entities_not_gone"),
            "series_not_gone": dm16.get("series_not_gone"),
            "reap_age_s": (catalog or {}).get("last_reap_age_s"),
            "reap_count": (catalog or {}).get("last_reap_count"),
            "degradation_age_s": (catalog or {}).get("last_degradation_age_s"),
        },
        # #106 supplies these; absent until then, and shown as such.
        "stages": [
            {"name": name, "seconds": m(name)}
            for name in ("sample_process_s", "pipeline_s", "commit_s",
                         "actions_outbox_s", "retention_s", "prune_s", "reap_s")
        ],
    }


def _self_budget_stats(
    panel: dict | None, *, db_used_bytes: float | None = None,
    db_warn_bytes: float | None = None,
) -> list[dict]:
    """Compact strip figures for the dashboard (#105).

    The same readings /self expands on, reduced to a value and a tone.
    Thresholds are not repeated here: the strip answers "is FTMON itself
    healthy" and links onward for "against what".

    The database figure comes from the live status read rather than the
    sampled metric — it is always available and a sample fresher — but it is
    toned by the same `_budget_row` rule the panel uses. Sharing the *rule*
    is what stops the two surfaces disagreeing about whether the daemon is
    inside its database budget; sharing the input would trade a fresher
    number for nothing.
    """
    if not panel:
        return []
    out = []
    for key, label, fmt in (
        ("cpu", "Self CPU %", lambda v: f"{v:.1f}"),
        ("memory", "Self RSS MB", lambda v: f"{v / 1048576:.0f}"),
    ):
        row = panel[key]["row"]
        if row["value"] is None:
            continue
        limit = row["limit"]
        title = (
            f"{row['pct']:.0f}% of the threshold set on this host" if row["pct"] is not None
            else "no threshold configured"
        ) + (f" ({fmt(limit)})" if limit else "")
        out.append({"label": label, "value": fmt(row["value"]),
                    "tone": row["tone"], "title": title})
    if db_used_bytes is not None:
        db_row = _budget_row("database", "Database MB (used)", db_used_bytes,
                             db_warn_bytes, "bytes")
        allocated = (panel.get("database") or {}).get("allocated")
        title = (
            f"{db_row['pct']:.0f}% of the threshold set on this host "
            f"({db_warn_bytes / 1048576:.0f} MB)"
            if db_row["pct"] is not None else "no threshold configured"
        )
        if allocated:
            title += f"; file allocation {allocated / 1048576:.1f} MB"
        out.append({"label": "Database MB (used)",
                    "value": f"{db_used_bytes / 1048576:.1f}",
                    "tone": db_row["tone"], "title": title})
    return out


async def self_page(request: Request):
    paths = request.app.state.paths
    try:
        log_tail = "\n".join(paths.log_file.read_text(errors="replace").splitlines()[-200:])
    except OSError:
        log_tail = "No daemon log yet."
    dispatch = None
    with _query(request) as q:
        status = _status(request, q)
        metrics_rows = [] if q is None else q._conn.execute(
            "SELECT se.metric, s.value, s.ts FROM series se JOIN samples s ON s.series_id=se.id "
            "WHERE se.monitor='self' AND s.ts="
            "(SELECT MAX(x.ts) FROM samples x WHERE x.series_id=se.id)"
        ).fetchall()
        catalog = None
        if q is not None:
            # Read-side, so the per-channel split costs no persisted series
            # against the DM-16 catalog budget (DESIGN 10.7).
            dispatch = dispatch_health(q._conn)
            catalog = catalog_report(q._conn, now=request.app.state.clock.now())
    defs, errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    # Thresholds come from the loaded self definition, not from constants
    # here: an operator who retunes cpu_budget_pct must see the panel compare
    # against what their daemon actually alarms at (MD-01).
    self_params = next(
        (dict(d.parameters) for d in defs if d.name == "self"), {}
    )
    panel = _self_panel(
        {r["metric"]: r["value"] for r in metrics_rows}, self_params, catalog
    )
    return _render(
        "self.html", request, title="Self", status=status, dispatch=dispatch,
        metrics=metrics_rows, config_errors=errors, log_tail=log_tail,
        panel=panel, web_version=__version__, refresh_ms=15000,
    )


def create_app(paths: Paths | None = None, clock=None, port: int = 8420) -> Starlette:
    """Create the optional ASGI application with no daemon dependency (UI-07)."""
    routes = [
        Route("/", dashboard), Route("/incidents", incidents),
        Route("/incidents/{id:int}", incident_detail),
        Route("/incidents/{id:int}/ack", ack, methods=["POST"]),
        Route("/metrics", metrics), Route("/baselines", baselines), Route("/events", events),
        Route("/trends", trends), Route("/trends/{monitor:str}/{profile:str}", trends),
        Route("/api/trend", trend_api), Route("/api/series", series_api),
        Route("/disks", disks_redirect), Route("/disks/{entity:path}", disks_redirect),
        Route("/api/disk-trend", disk_trend_api),
        Route("/monitors", monitors),
        Route("/monitors/{name:str}/{action:str}", monitor_action, methods=["POST"]),
        Route("/self", self_page),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(SecurityMiddleware, port=port)])
    app.mount("/static", StaticFiles(packages=[("ftmon.web", "static")]), name="static")
    app.state.paths = paths or get_paths()
    app.state.clock = clock or SystemClock()
    return app


def configured_port(paths: Paths) -> int:
    try:
        value = tomllib.loads(paths.config_file.read_text()).get("web", {}).get("port", 8420)
        port = int(value)
        return port if 1 <= port <= 65535 else 8420
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return 8420


def run(_args=None) -> int:
    """Serve only on loopback (UI-01)."""
    import uvicorn
    paths = get_paths()
    if _args is not None and getattr(_args, "demo", False):
        from ftmon.web.demo_app import create_demo_app

        port = _args.port or 8420
        uvicorn.run(
            create_demo_app(Path(_args.demo_db), _args.demo_host),
            host="127.0.0.1",
            port=port,
        )
        return 0
    port = (_args.port if _args is not None else None) or configured_port(paths)
    uvicorn.run(create_app(paths, port=port), host="127.0.0.1", port=port)
    return 0

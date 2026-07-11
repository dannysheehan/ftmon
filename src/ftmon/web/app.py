"""Server-rendered, loopback-only web dashboard (UI-01..09, SE-02)."""

from __future__ import annotations

import json
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode

from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from ftmon.clock import SystemClock
from ftmon.definitions import loader, manage
from ftmon.expr import ExprError, parse_duration
from ftmon.model import severity_name
from ftmon.paths import Paths, get_paths
from ftmon.store.db import connect
from ftmon.store.query import Query, SmallWrites

_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:"
_jinja = Environment(loader=PackageLoader("ftmon.web", "templates"),
                     autoescape=select_autoescape(("html", "xml")))


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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def _render(name: str, request: Request, **context) -> HTMLResponse:
    context.update(
        request=request,
        severity_name=severity_name,
        utc_iso=lambda ts: datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    return HTMLResponse(_jinja.get_template(name).render(**context))


@contextmanager
def _query(request: Request, *, writable: bool = False):
    paths = request.app.state.paths
    if not paths.db_file.exists():
        yield None
        return
    conn = connect(paths.db_file, readonly=not writable)
    try:
        yield Query(conn) if not writable else conn
    finally:
        conn.close()


def _status(request: Request, q: Query | None) -> dict:
    now = request.app.state.clock.now()
    if q is None:
        return {"daemon_stale": True, "last_tick_age_s": None, "db_bytes": 0,
                "open_incidents": 0}
    out = q.status(now=now)
    out["daemon_stale"] = out["last_tick_age_s"] is None or out["last_tick_age_s"] > 15
    return out


async def dashboard(request: Request):
    paths = request.app.state.paths
    defs, errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    with _query(request) as q:
        status = _status(request, q)
        incidents = [] if q is None else q.incidents(state=None)
        incidents = [r for r in incidents if r["state"] != "cleared"][:10]
    return _render("dashboard.html", request, title="Dashboard", status=status,
                   monitors=defs, config_errors=errors, incidents=incidents,
                   trend_profiles={d.name: d.trends for d in defs},
                   refresh_ms=5000)


async def incidents(request: Request):
    state = request.query_params.get("state")
    with _query(request) as q:
        rows = [] if q is None else q.incidents(state=state)
        status = _status(request, q)
    return _render("incidents.html", request, title="Incidents", rows=rows, status=status,
                   refresh_ms=5000)


async def incident_detail(request: Request):
    iid = int(request.path_params["id"])
    with _query(request) as q:
        row = None if q is None else q._conn.execute(
            "SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
        history = [] if q is None else q._conn.execute(
            "SELECT * FROM incident_history WHERE incident_id=? ORDER BY seq", (iid,)).fetchall()
        status = _status(request, q)
    if row is None:
        return Response("Incident not found", status_code=404)
    trend_profile = next((
        profile for mdef, profile in _trend_catalog(request.app.state.paths)
        if mdef.name == row["monitor"]
        and (profile.incident_group is None or profile.incident_group == row["grp"])
    ), None)
    return _render("incident.html", request, title=f"Incident #{iid}", row=row,
                   history=history, status=status, trend_profile=trend_profile,
                   refresh_ms=5000)


async def ack(request: Request):
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

    The database is the catalog: definitions can change while history remains,
    so selectors must describe queryable series rather than only current TOML.
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
            range_text = p.get("range", "24h")
            seconds = parse_duration(range_text)
    except (ValueError, ExprError):
        return Response("Invalid range; use values such as 6h, 7d, or 30d", status_code=400)
    if not 900 <= seconds <= 400 * 86400:
        return Response("Range must be between 15m and 400d", status_code=400)
    now = request.app.state.clock.now()
    with _query(request) as q:
        choices = [] if q is None else q._conn.execute(
            "SELECT DISTINCT monitor, entity_id, metric FROM series "
            "ORDER BY monitor, entity_id, metric"
        ).fetchall()
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
    series = [{"entity": r.entity_id, "resolution": r.resolution,
               "points": [[x.ts, x.value] for x in r.points]} for r in rows]
    selected = {"monitor": monitor, "entity": entity, "metric": metric,
                "range": range_text, "statistic": statistic}
    return _render(
        "metrics.html", request, title="Metrics", choices=choices,
        monitors=monitors, entities=entities, metrics=metrics,
        series=series, series_json=json.dumps(series), selected=selected,
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


def _trend_catalog(paths: Paths):
    """Load profile owners together so cross-monitor IDs never collide."""
    defs, _errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    return [(mdef, profile) for mdef in defs for profile in mdef.trends]


def _selected_profile(paths: Paths, monitor: str | None, profile_id: str | None):
    catalog = _trend_catalog(paths)
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
    selected = _selected_profile(request.app.state.paths, "disk", "space-growth")
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
    selected = _selected_profile(request.app.state.paths, monitor, profile_id)
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
    catalog = _trend_catalog(request.app.state.paths)
    selected = _selected_profile(request.app.state.paths, monitor, profile_id)
    mdef, profile = selected if selected else (None, None)
    with _query(request) as q:
        entities = [] if q is None or mdef is None else [
            row["entity_id"] for row in q.entities(mdef.name)
        ]
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
    return _render("events.html", request, title="Events", rows=rows, severity=severity)


async def monitors(request: Request):
    paths = request.app.state.paths
    defs, errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    drafts = []
    for path in sorted(paths.drafts_dir.glob("*.toml")) if paths.drafts_dir.exists() else []:
        try:
            drafts.append((path.stem, path.read_text(), loader.load_file(path), None))
        except Exception as exc:
            drafts.append((path.stem, path.read_text(), None, exc))
    return _render("monitors.html", request, title="Monitors", monitors=defs,
                   errors=errors, drafts=drafts)


async def monitor_action(request: Request):
    name, action = request.path_params["name"], request.path_params["action"]
    try:
        if action == "approve":
            manage.approve_draft(request.app.state.paths, name)
        elif action == "delete-draft":
            manage.delete_draft(request.app.state.paths, name)
        elif action in {"enable", "disable"}:
            manage.set_enabled(request.app.state.paths, name, action == "enable")
        else:
            return Response("Unknown action", status_code=404)
    except manage.ManageError as exc:
        return Response(str(exc), status_code=409)
    return RedirectResponse("/monitors", status_code=303)


async def self_page(request: Request):
    paths = request.app.state.paths
    try:
        log_tail = "\n".join(paths.log_file.read_text(errors="replace").splitlines()[-200:])
    except OSError:
        log_tail = "No daemon log yet."
    with _query(request) as q:
        status = _status(request, q)
        metrics_rows = [] if q is None else q._conn.execute(
            "SELECT se.metric, s.value, s.ts FROM series se JOIN samples s ON s.series_id=se.id "
            "WHERE se.monitor='self' AND s.ts="
            "(SELECT MAX(x.ts) FROM samples x WHERE x.series_id=se.id)"
        ).fetchall()
    _defs, errors = loader.load_dir(
        paths.monitors_dir, actions_dir=paths.actions_dir, require_actions=True
    )
    return _render("self.html", request, title="Self", status=status,
                   metrics=metrics_rows, config_errors=errors, log_tail=log_tail)


def create_app(paths: Paths | None = None, clock=None, port: int = 8420) -> Starlette:
    """Create the optional ASGI application with no daemon dependency (UI-07)."""
    routes = [
        Route("/", dashboard), Route("/incidents", incidents),
        Route("/incidents/{id:int}", incident_detail),
        Route("/incidents/{id:int}/ack", ack, methods=["POST"]),
        Route("/metrics", metrics), Route("/events", events),
        Route("/trends", trends), Route("/trends/{monitor:str}/{profile:str}", trends),
        Route("/api/trend", trend_api),
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
    port = configured_port(paths)
    uvicorn.run(create_app(paths, port=port), host="127.0.0.1", port=port)
    return 0

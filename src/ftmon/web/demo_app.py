"""Immutable, GET-only public demonstration ASGI application (UI-15/16)."""

from __future__ import annotations

import ipaddress
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from ftmon.paths import get_paths
from ftmon.store.query import Query
from ftmon.web.app import (
    _CSP,
    _demo_definitions,
    dashboard,
    disk_trend_api,
    disks_redirect,
    events,
    incident_detail,
    incidents,
    metrics,
    series_api,
    trend_api,
    trends,
)

DEMO_SCENARIO_NAME = "demo-v1"
DEMO_SCENARIO_VERSION = "1"


class DemoSecurityMiddleware(BaseHTTPMiddleware):
    """Enforce the public demo's narrow hostname and request boundary (SE-06)."""

    def __init__(self, app, hostname: str):
        super().__init__(app)
        self.allowed_hosts = {hostname, f"{hostname}:443"}

    async def dispatch(self, request: Request, call_next):
        # Bound query state here even when a reverse proxy has a different cap.
        target_size = len(request.scope.get("raw_path", b"")) + len(
            request.scope.get("query_string", b"")
        )
        if target_size > 4096:
            response = Response("Request target too long", status_code=414)
        elif request.headers.get("host", "").lower() not in self.allowed_hosts:
            response = Response("Bad Host", status_code=400)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


@dataclass(frozen=True)
class _DemoClock:
    value: float

    def now(self) -> float:
        return self.value


def _validated_demo_host(hostname: str) -> str:
    host = hostname.strip().lower().rstrip(".")
    if not host or len(host) > 253 or "*" in host or ":" in host or "/" in host:
        raise ValueError("--demo-host must be one public DNS hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("--demo-host must not be an IP address")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("--demo-host must not be localhost")
    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
        host,
    ):
        raise ValueError("--demo-host must be one public DNS hostname")
    return host


def _open_demo_database(db_path: Path) -> tuple[sqlite3.Connection, float]:
    path = db_path.expanduser().resolve()
    try:
        stat = db_path.expanduser().lstat()
    except OSError as exc:
        raise ValueError(f"demo database is unavailable: {exc}") from exc
    if not os.path.isfile(path) or db_path.expanduser().is_symlink():
        raise ValueError("demo database must be a regular, non-symlink file")
    if stat.st_mode & 0o022:
        raise ValueError("demo database must not be group/world writable")
    if path == get_paths().db_file.expanduser().resolve():
        raise ValueError("the operational FTMON database cannot be used as a demo")
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        if meta.get("demo_dataset") != "1":
            raise ValueError("database is not marked as synthetic demo data")
        if meta.get("demo_scenario") != DEMO_SCENARIO_NAME:
            raise ValueError(f"demo database must use scenario {DEMO_SCENARIO_NAME}")
        if meta.get("demo_scenario_version") != DEMO_SCENARIO_VERSION:
            raise ValueError(
                f"demo database must use scenario version {DEMO_SCENARIO_VERSION}"
            )
        now = float(meta.get("demo_now_ts", meta.get("last_tick_ts", "0")))
        return conn, now
    except (sqlite3.Error, TypeError, ValueError) as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"invalid demo database: {exc}") from exc


def create_demo_app(db_path: Path, hostname: str) -> Starlette:
    """Create a route allowlist with no imported write capability (UI-15)."""
    host = _validated_demo_host(hostname)
    conn, now = _open_demo_database(Path(db_path))
    try:
        definitions, _errors = _demo_definitions(Query(conn))
    finally:
        conn.close()
    dead = Path(db_path).resolve().parent / ".no-operational-state"
    paths = replace(
        get_paths({
            "FTMON_CONFIG_DIR": str(dead / "config"),
            "FTMON_DATA_DIR": str(Path(db_path).resolve().parent),
            "FTMON_STATE_DIR": str(dead / "state"),
            "FTMON_RUNTIME_DIR": str(dead / "runtime"),
        }),
        db_file=Path(db_path).resolve(),
    )
    routes = [
        Route("/", dashboard), Route("/incidents", incidents),
        Route("/incidents/{id:int}", incident_detail), Route("/metrics", metrics),
        Route("/events", events), Route("/trends", trends),
        Route("/trends/{monitor:str}/{profile:str}", trends),
        Route("/api/trend", trend_api), Route("/api/series", series_api),
        Route("/disks", disks_redirect),
        Route("/disks/{entity:path}", disks_redirect),
        Route("/api/disk-trend", disk_trend_api),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(DemoSecurityMiddleware, hostname=host)])
    app.mount("/static", StaticFiles(packages=[("ftmon.web", "static")]), name="static")
    app.state.paths, app.state.clock, app.state.demo = paths, _DemoClock(now), True
    app.state.demo_definitions, app.state.demo_host = definitions, host
    return app

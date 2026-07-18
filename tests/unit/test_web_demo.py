"""[UI-15][SE-06][TS-14] Public demo application security contracts."""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import ftmon.web.demo_app as demo_app_module
from ftmon.cli import main
from ftmon.demo import build
from ftmon.store.db import connect, migrate
from ftmon.web.demo_app import DEMO_SCENARIO_NAME, DEMO_SCENARIO_VERSION, create_demo_app


def _demo_db(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    conn = connect(path)
    migrate(conn)
    conn.executemany(
        "INSERT INTO meta(key,value) VALUES(?,?)",
        [
            ("demo_dataset", "1"),
            ("demo_scenario", DEMO_SCENARIO_NAME),
            ("demo_scenario_version", DEMO_SCENARIO_VERSION),
            ("demo_now_ts", "1000"),
            ("last_tick_ts", "900"),
        ],
    )
    normalized = (
        Path(__file__).parents[2] / "src/ftmon/definitions/builtins/disk.toml"
    ).read_text()
    conn.execute(
        "INSERT INTO monitor_loads(monitor,loaded_ts,hash,normalized) "
        "VALUES('disk',900,'synthetic',?)",
        (normalized,),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    os.chmod(path, 0o600)
    return path


def test_demo_is_visibly_synthetic_get_only_and_immutable_ui_15_ts_14(tmp_path):
    """[UI-15][TS-14] Mutating routes do not exist and visits change no bytes."""
    db = _demo_db(tmp_path)
    before = (hashlib.sha256(db.read_bytes()).digest(), db.stat().st_mtime_ns)
    app = create_demo_app(db, "demo.ftmon.org")
    client = TestClient(app)
    headers = {"host": "demo.ftmon.org"}

    page = client.get("/", headers=headers)
    assert page.status_code == 200
    assert "Synthetic demonstration data" in page.text
    assert '<meta name="robots" content="noindex,nofollow">' in page.text
    assert "/monitors" not in page.text and "/self" not in page.text
    assert client.head("/metrics", headers=headers).status_code == 200

    for path in (
        "/incidents/1/ack",
        "/monitors/disk/disable",
        "/drafts/example/approve",
        "/drafts/example/delete",
    ):
        assert client.post(path, headers=headers).status_code in {404, 405}
    assert all(
        route.methods is None or route.methods <= {"GET", "HEAD"}
        for route in app.routes
        if hasattr(route, "methods")
    )
    after = (hashlib.sha256(db.read_bytes()).digest(), db.stat().st_mtime_ns)
    assert after == before
    assert not db.with_name(f"{db.name}-wal").exists()
    assert not db.with_name(f"{db.name}-shm").exists()


def test_demo_factory_accepts_the_real_seeded_builder_contract_ui_15_ui_16(tmp_path):
    """[UI-15][UI-16] Web startup and WP29 share one marker/version contract."""
    db = build(tmp_path / "built-demo.db")
    response = TestClient(create_demo_app(db, "demo.ftmon.org")).get(
        "/", headers={"host": "demo.ftmon.org"}
    )
    assert response.status_code == 200
    assert "Synthetic demonstration data" in response.text
    for monitor, state in (
        ("load", "clear"), ("disk", "warning"),
        ("leak", "error"), ("service", "disabled"),
    ):
        assert f'data-monitor="{monitor}" data-state="{state}"' in response.text
    assert "Daemon data is stale" in response.text
    assert "3600 seconds old" not in response.text
    baselines = TestClient(create_demo_app(db, "demo.ftmon.org")).get(
        "/baselines", headers={"host": "demo.ftmon.org"}
    )
    assert baselines.status_code == 200
    assert "learning" in baselines.text and "ready" in baselines.text
    for monitor, profile, entity in (
        ("disk", "space-growth", "mount:/srv/demo"),
        ("leak", "rss-growth", "process:demo-worker"),
    ):
        trend = TestClient(create_demo_app(db, "demo.ftmon.org")).get(
            "/api/trend",
            params={
                "monitor": monitor, "profile": profile,
                "entity": entity, "range": "7d",
            },
            headers={"host": "demo.ftmon.org"},
        )
        assert trend.status_code == 200
        panels = trend.json()["panels"]
        assert panels["value"]["points"] and panels["rate"]["points"]
        assert panels["confidence"]["points"]


def test_demo_factory_source_has_no_write_side_imports_ui_15():
    """[UI-15] The public factory cannot accidentally register write helpers."""
    source = inspect.getsource(demo_app_module)
    for forbidden in ("SmallWrites", "definitions.manage", "actions", "daemon", "mcp"):
        assert forbidden not in source


def test_demo_cli_requires_complete_explicit_boundary_ui_15(tmp_path, monkeypatch):
    """[UI-15][SE-06] CLI cannot silently infer public authority or real data."""
    for args in (
        ["web", "--demo"],
        ["web", "--demo-db", str(tmp_path / "demo.db")],
        ["web", "--demo-host", "demo.ftmon.org"],
        ["web", "--port", "0"],
    ):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2

    received = {}

    def fake_run(args):
        received.update(vars(args))
        return 0

    monkeypatch.setattr("ftmon.web.app.run", fake_run)
    db = tmp_path / "demo.db"
    assert main([
        "web", "--demo", "--demo-db", str(db),
        "--demo-host", "demo.ftmon.org", "--port", "9000",
    ]) == 0
    assert received == {
        "command": "web", "demo": True, "demo_db": db,
        "demo_host": "demo.ftmon.org", "port": 9000,
    }


def test_demo_exact_host_headers_forwarding_and_target_cap_se_06(tmp_path):
    """[SE-06][TS-14] Proxy headers grant no authority and targets are bounded."""
    client = TestClient(create_demo_app(_demo_db(tmp_path), "demo.ftmon.org"))
    for host in ("demo.ftmon.org", "demo.ftmon.org:443"):
        response = client.get("/events", headers={"host": host})
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert "access-control-allow-origin" not in response.headers
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400
    assert client.get(
        "/",
        headers={"host": "attacker.example", "x-forwarded-host": "demo.ftmon.org"},
    ).status_code == 400
    assert client.get(
        "/metrics?q=" + "x" * 4096, headers={"host": "demo.ftmon.org"}
    ).status_code == 414


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "127.0.0.1", "*.ftmon.org", "https://demo.ftmon.org", "example"],
)
def test_demo_rejects_non_public_or_ambiguous_hostname_se_06(tmp_path, hostname):
    """[SE-06] Startup requires one unambiguous public DNS authority."""
    with pytest.raises(ValueError, match="demo-host"):
        create_demo_app(_demo_db(tmp_path), hostname)


def test_demo_rejects_unmarked_unsafe_and_symlink_databases_ui_15(tmp_path):
    """[UI-15][TS-14] Startup fails closed before serving non-demo telemetry."""
    unmarked = tmp_path / "unmarked.db"
    conn = connect(unmarked)
    migrate(conn)
    conn.close()
    with pytest.raises(ValueError, match="not marked"):
        create_demo_app(unmarked, "demo.ftmon.org")

    unsafe = _demo_db(tmp_path / "unsafe")
    os.chmod(unsafe, 0o620)
    with pytest.raises(ValueError, match="group/world writable"):
        create_demo_app(unsafe, "demo.ftmon.org")

    target = _demo_db(tmp_path / "linked")
    link = tmp_path / "linked.db"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="non-symlink"):
        create_demo_app(link, "demo.ftmon.org")

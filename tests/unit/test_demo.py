"""Deterministic public-demo dataset contracts (UI-15/16)."""

from __future__ import annotations

import json
import sqlite3

from ftmon.definitions import loader
from ftmon.demo import POINT_COUNT, build


def _meta(conn, key):
    return conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]


def test_builder_marks_and_covers_the_synthetic_dataset_ui_15_ui_16(tmp_path):
    """[UI-15][UI-16][TS-14] Markers and data prove every demo condition."""
    output = build(tmp_path / "demo.db")
    conn = sqlite3.connect(f"file:{output}?mode=ro&immutable=1", uri=True)
    try:
        assert _meta(conn, "demo_dataset") == "1"
        assert _meta(conn, "demo_scenario_version") == "1"
        assert set(json.loads(_meta(conn, "demo_coverage"))) == {
            "clear", "warning", "error", "disabled",
        }
        assert _meta(conn, "last_tick_ts") == _meta(conn, "demo_now_ts")
        assert {row[0] for row in conn.execute("SELECT DISTINCT state FROM incidents")} == {
            "open", "cleared",
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM series WHERE monitor='disk' AND metric='fill_rate_bph'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM series WHERE monitor='leak' AND metric='rss_slope_mbph'"
        ).fetchone()[0] == 1
        metrics = {
            (row[0], row[1]) for row in conn.execute("SELECT monitor,metric FROM series")
        }
        assert {
            ("disk", "used_pct"), ("disk", "fill_rate_bph"), ("disk", "filling"),
            ("leak", "rss_mb"), ("leak", "rss_slope_mbph"),
            ("leak", "rss_growth_confidence"),
        } <= metrics
        definitions = {
            row[0]: loader.load_text(row[1], "<demo-test>")
            for row in conn.execute("SELECT monitor,normalized FROM monitor_loads")
        }
        assert definitions["disk"].trends[0].value_metric == "used_pct"
        assert definitions["leak"].trends[0].rate_metric == "rss_slope_mbph"
        assert definitions["service"].enabled is False
        assert conn.execute(
            "SELECT MIN(n) FROM (SELECT COUNT(*) n FROM samples GROUP BY series_id)"
        ).fetchone()[0] < POINT_COUNT
        assert conn.execute("SELECT COUNT(*) FROM rollup5m").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM rollup1h").fetchone()[0] > 0
        updates = [row[0] for row in conn.execute("SELECT updates FROM baselines")]
        assert any(value < 240 for value in updates)
        assert any(value >= 240 for value in updates)
    finally:
        conn.close()
    assert output.stat().st_mode & 0o222 == 0


def test_builder_is_byte_deterministic_and_atomically_replaceable_ui_16(tmp_path):
    """[UI-16][TS-14] Rebuilds have no wall-clock or visitor-state drift."""
    first = build(tmp_path / "first.db").read_bytes()
    target = tmp_path / "demo.db"
    target.write_text("old visitor state must not survive")
    second = build(target).read_bytes()
    assert first == second

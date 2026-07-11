"""[NO-03] Quiet hours: config parsing, the active-window math, holding
warning-and-below, error+ pass-through, and the end-of-quiet digest."""

from __future__ import annotations

from datetime import UTC

import pytest

from ftmon.config import AppConfig, QuietHours, load_config, parse_hhmm
from ftmon.store.outbox import Outbox
from tests.unit.test_m2_integration import ListNotifier, _outbox_db

MIDNIGHT = 1_700_000_000 - (1_700_000_000 % 86400)  # 00:00 UTC of a fixed day
NIGHT = MIDNIGHT + 23 * 3600  # 23:00, inside a 22:00-08:00 window
MORNING = MIDNIGHT + 86400 + 9 * 3600  # 09:00 next day, outside it
OVERNIGHT = QuietHours(start_min=22 * 60, end_min=8 * 60, tz=UTC)


class TestConfig:
    def test_parse_hhmm(self):
        assert parse_hhmm("22:00") == 1320
        assert parse_hhmm("08:05") == 485
        for bad in ("24:00", "9:60", "night", ""):
            with pytest.raises(ValueError):
                parse_hhmm(bad)

    def test_active_window_same_day_and_overnight(self):
        """[NO-03] both window shapes; boundaries are start-inclusive,
        end-exclusive."""
        lunch = QuietHours(start_min=12 * 60, end_min=13 * 60, tz=UTC)
        assert lunch.active(MIDNIGHT + 12 * 3600)
        assert not lunch.active(MIDNIGHT + 13 * 3600)
        assert OVERNIGHT.active(MIDNIGHT + 22 * 3600)
        assert OVERNIGHT.active(MIDNIGHT + 86400 + 7 * 3600 + 3540)  # 07:59
        assert not OVERNIGHT.active(MIDNIGHT + 86400 + 8 * 3600)  # 08:00 sharp
        assert not OVERNIGHT.active(MIDNIGHT + 12 * 3600)
        # zero-length window means disabled, not always-quiet
        assert not QuietHours(start_min=0, end_min=0, tz=UTC).active(MIDNIGHT)

    def test_load_config_defaults_and_quiet(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg, warnings = load_config(cfg_file)  # missing file: defaults, no noise
        assert cfg == AppConfig() and warnings == []

        cfg_file.write_text(
            "[daemon]\ntick_seconds = 5\n"
            "[quiet_hours]\nenabled = true\nstart = \"22:00\"\nend = \"08:00\"\n"
        )
        cfg, warnings = load_config(cfg_file, tz=UTC)
        assert warnings == []
        assert cfg.quiet == OVERNIGHT

    def test_load_config_degrades_with_warnings(self, tmp_path):
        """A bad edit yields defaults + warnings, never a refusal to start
        (same posture as PM-04 for monitor files)."""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            "[daemon]\ntick_seconds = -3\n"
            "[quiet_hours]\nenabled = true\nstart = \"25:99\"\n"
        )
        cfg, warnings = load_config(cfg_file)
        assert cfg.tick_seconds == 5.0 and cfg.quiet is None
        assert len(warnings) == 2

        cfg_file.write_text("not toml [[[")
        cfg, warnings = load_config(cfg_file)
        assert cfg == AppConfig() and len(warnings) == 1


class TestQuietOutbox:
    def test_warning_held_error_through(self, tmp_path):
        """[NO-03] during quiet: warning rows held (undelivered, not stale);
        error rows deliver immediately."""
        conn = _outbox_db(tmp_path, [(1, "open", 2, NIGHT), (2, "open", 3, NIGHT)])
        ok = ListNotifier()
        ob = Outbox(conn, [ok], quiet=OVERNIGHT)
        assert ob.flush(now=NIGHT + 60) == 1
        assert [n.severity for n in ok.delivered] == [3]
        held = conn.execute(
            "SELECT state FROM notification_deliveries WHERE state='pending'"
        ).fetchall()
        assert len(held) == 1 and held[0]["state"] == "pending"

    def test_digest_at_quiet_end(self, tmp_path):
        """[NO-03] held rows become exactly one digest when quiet ends, and
        are stamped delivered so they never fire individually."""
        conn = _outbox_db(
            tmp_path,
            [(1, "open", 2, NIGHT), (1, "renotify", 2, NIGHT + 1800),
             (2, "recover", 0, NIGHT + 3600)],
        )
        ok = ListNotifier()
        ob = Outbox(conn, [ok], quiet=OVERNIGHT)
        assert ob.flush(now=NIGHT + 3700) == 0  # still quiet: everything held
        assert ob.flush(now=MORNING) == 1
        (digest,) = ok.delivered
        assert digest.kind == "digest"
        assert "3 notification(s)" in digest.title
        assert digest.severity == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries WHERE state='pending'"
        ).fetchone()[0] == 0
        assert ob.flush(now=MORNING + 60) == 0  # digest is not repeated

    def test_recover_does_not_stale_quiet_held_rows(self, tmp_path):
        """[NO-03]/[NO-04] a restart during (or after) quiet hours must not
        mark held rows stale — they still owe the user a digest."""
        conn = _outbox_db(tmp_path, [(1, "open", 2, NIGHT)])
        ok = ListNotifier()
        ob = Outbox(conn, [ok], quiet=OVERNIGHT)
        delivered, stale = ob.recover(now=NIGHT + 7200)  # 2h later, still quiet
        assert (delivered, stale) == (0, 0)
        assert ob.flush(now=MORNING) == 1  # the digest still happens
        assert ok.delivered[0].kind == "digest"

    def test_no_quiet_config_means_no_holding(self, tmp_path):
        """Without [quiet_hours] the outbox behaves exactly as before."""
        conn = _outbox_db(tmp_path, [(1, "open", 2, NIGHT)])
        ok = ListNotifier()
        assert Outbox(conn, [ok], quiet=None).flush(now=NIGHT + 60) == 1
        assert ok.delivered[0].kind == "open"

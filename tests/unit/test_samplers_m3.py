"""[SA-04][PL-03] unit and net samplers: watchlist-driven synthetic entities,
canned systemctl output (no systemd needed in CI), real sockets for net."""

from __future__ import annotations

import socket

import psutil

from ftmon.clock import FakeClock
from ftmon.sources.net import NetSampler
from ftmon.sources.unit import UnitSampler

NOW = 1_700_000_000.0


def sample(sampler, options):
    return sampler.sample(NOW, 10_000.0, options)


class TestUnitSampler:
    def make(self, output: str) -> UnitSampler:
        return UnitSampler(FakeClock(), run_cmd=lambda unit: output)

    def test_active_unit_present_with_restarts(self):
        """[SA-04] ActiveState/NRestarts from systemctl show."""
        s = self.make("ActiveState=active\nNRestarts=2\n")
        snap = sample(s, {"watchlist": [{"unit": "sshd.service"}]})
        (ent,) = snap.entities
        assert ent.entity_id == "unit:sshd.service"
        assert ent.metrics == {"present": 1.0, "restarts": 2.0}
        assert ent.attrs["kind"] == "unit"

    def test_inactive_or_unknown_unit_is_down_not_missing(self):
        """[SA-04] watchlist entities are synthetic (CA-08): a down unit is
        present=0 every cycle, never absent — absence is the signal."""
        for output in ("ActiveState=inactive\nNRestarts=0\n", ""):
            snap = sample(self.make(output), {"watchlist": [{"unit": "x.service"}]})
            (ent,) = snap.entities
            assert ent.metrics["present"] == 0.0

    def test_missing_nrestarts_omitted_not_zeroed(self):
        """[SA-04] no NRestarts (older systemd) -> metric absent, so delta()
        goes UNKNOWN instead of faking a counter reset."""
        snap = sample(self.make("ActiveState=active\n"),
                      {"watchlist": [{"unit": "x.service"}]})
        assert "restarts" not in snap.entities[0].metrics

    def test_process_pattern_matches_own_process(self):
        s = UnitSampler(FakeClock())  # real psutil scan
        me = psutil.Process().name()
        snap = sample(s, {"watchlist": [
            {"process": f"^{me}$"},
            {"process": "^no-such-process-zzz$"},
        ]})
        by_id = {e.entity_id: e for e in snap.entities}
        assert by_id[f"proc:^{me}$"].metrics["present"] == 1.0
        assert by_id["proc:^no-such-process-zzz$"].metrics["present"] == 0.0

    def test_during_window_out_of_scope_reports_healthy(self):
        """[SA-04] outside `during` the target is supposed to be down:
        present=1 keeps the rule FALSE so an open incident can clear."""
        s = self.make("ActiveState=inactive\n")
        # NOW is some fixed local time; a window that surely excludes it is
        # impossible to pin without tz control, so use the inverse trick:
        # a zero-width window means always-in-scope per _in_window contract.
        from ftmon.sources.unit import _in_window
        assert _in_window(None, NOW) is True
        assert _in_window("00:00-00:00", NOW) is True  # zero-width: in scope
        # craft a window that excludes NOW using the parsed local minute
        from datetime import datetime
        m = datetime.fromtimestamp(NOW)
        excl_start = (m.hour * 60 + m.minute + 120) % 1440
        excl_end = (excl_start + 60) % 1440
        window = f"{excl_start // 60:02d}:{excl_start % 60:02d}-" \
                 f"{excl_end // 60:02d}:{excl_end % 60:02d}"
        assert _in_window(window, NOW) is False
        snap = s.sample(NOW, 10_000.0, {"watchlist": [
            {"unit": "backup.service", "during": window}]})
        assert snap.entities[0].metrics["present"] == 1.0

    def test_garbage_watchlist_entries_ignored(self):
        """[PL-03] junk entries must not kill the pass."""
        snap = sample(self.make("ActiveState=active\n"),
                      {"watchlist": ["notadict", {"neither": 1},
                                     {"unit": "ok.service"}]})
        assert [e.entity_id for e in snap.entities] == ["unit:ok.service"]


class TestNetSampler:
    def test_totals_and_watchlist_listener(self):
        """[SA-04] totals entity always present; a real listening socket is
        present=1, a (hopefully) unused port present=0."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            snap = sample(NetSampler(FakeClock()), {"watchlist": [
                {"listen": f"tcp:{port}"},
                {"listen": "tcp:1"},  # reserved, never listening in CI
                {"listen": "bogus"},  # PL-03: skipped, not fatal
            ]})
        finally:
            srv.close()
        by_id = {e.entity_id: e for e in snap.entities}
        assert by_id["totals"].metrics["conn_total"] >= 1
        assert by_id["totals"].metrics["conn_listen"] >= 1
        assert by_id[f"tcp:{port}"].metrics["present"] == 1.0
        assert by_id["tcp:1"].metrics["present"] == 0.0
        assert len(by_id) == 3  # bogus entry produced nothing

"""[SA-04][PL-03] unit and net samplers: watchlist-driven synthetic entities,
canned systemctl output (no systemd needed in CI), real sockets for net."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import psutil
import pytest

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


@pytest.mark.skipif(sys.platform != "win32", reason="win32serviceutil is Windows-only")
class TestWindowsServiceQuery:
    """[SA-04][PL-01][PL-03] unit.py's {unit=...} kind against the real
    Windows Service Control Manager -- no NRestarts equivalent exists there
    (see unit.py's module docstring), only ActiveState."""

    def test_real_running_service_reports_active(self):
        """[SA-04] Task Scheduler ('Schedule') runs by default on every
        Windows install -- a real, always-available positive case."""
        from ftmon.sources.unit import _query_windows_service

        assert _query_windows_service("Schedule") == "ActiveState=active"

    def test_unknown_service_reports_inactive_not_an_exception(self):
        """[PL-03] a typo'd watchlist entry alerts as down, never crashes."""
        from ftmon.sources.unit import _query_windows_service

        assert _query_windows_service("NoSuchServiceXYZ") == "ActiveState=inactive"

    def test_default_run_cmd_dispatches_to_windows_query(self):
        from ftmon.sources.unit import _default_run_cmd, _query_windows_service

        assert _default_run_cmd("Schedule") == _query_windows_service("Schedule")

    def test_unit_sampler_end_to_end_against_a_real_service(self):
        """[SA-04] UnitSampler's default run_cmd (no override) against the
        real Service Control Manager, not a canned string."""
        s = UnitSampler(FakeClock())
        snap = sample(s, {"watchlist": [{"unit": "Schedule"}]})
        (ent,) = snap.entities
        assert ent.metrics["present"] == 1.0
        assert "restarts" not in ent.metrics


class TestNetSampler:
    def test_totals_and_watchlist_listener(self, monkeypatch):
        """[SA-04] totals and watchlist use one deterministic connection
        snapshot; OS visibility rules must not make the unit test flaky."""
        port = 43210
        monkeypatch.setattr(psutil, "net_connections", lambda **_kwargs: [
            SimpleNamespace(
                status=psutil.CONN_LISTEN,
                laddr=SimpleNamespace(port=port),
                type=1,  # SOCK_STREAM
            ),
        ])
        snap = sample(NetSampler(FakeClock()), {"watchlist": [
            {"listen": f"tcp:{port}"},
            {"listen": "tcp:1"},
            {"listen": "bogus"},  # PL-03: skipped, not fatal
        ]})
        by_id = {e.entity_id: e for e in snap.entities}
        assert by_id["totals"].metrics["conn_total"] >= 1
        assert by_id["totals"].metrics["conn_listen"] >= 1
        assert by_id[f"tcp:{port}"].metrics["present"] == 1.0
        assert by_id["tcp:1"].metrics["present"] == 0.0
        assert len(by_id) == 3  # bogus entry produced nothing


class TestWatchlistSyntheticFlag:
    """[DM-04][CA-08] Sources mark the entities they synthesize from a
    watchlist. DM-04 promises those the durable window, and only the source
    knows which of its entities the watchlist produced -- `net` also emits a
    discovered `totals` entity that must not inherit it."""

    def test_unit_watchlist_entities_are_synthetic(self):
        sampler = UnitSampler(FakeClock(), run_cmd=lambda unit: "ActiveState=active\n")
        snap = sampler.sample(1000.0, 1e9, {"watchlist": [{"unit": "ssh.service"}]})
        assert [e.entity_id for e in snap.entities] == ["unit:ssh.service"]
        assert all(e.synthetic for e in snap.entities)

    def test_net_listener_watchlist_entity_is_synthetic(self):
        snap = NetSampler(FakeClock()).sample(
            1000.0, 1e9, {"watchlist": [{"listen": "tcp:22"}]})
        by_id = {e.entity_id: e for e in snap.entities}
        assert "tcp:22" in by_id
        assert by_id["tcp:22"].synthetic is True

    def test_net_totals_entity_is_not_synthetic(self):
        snap = NetSampler(FakeClock()).sample(
            1000.0, 1e9, {"watchlist": [{"listen": "tcp:22"}]})
        by_id = {e.entity_id: e for e in snap.entities}
        assert "totals" in by_id
        assert by_id["totals"].synthetic is False

    def test_malformed_watchlist_entries_synthesize_nothing(self):
        """[DM-04][PL-03] A broken entry must not classify an unrelated entity
        as durable: it produces no entity at all, so `totals` stays alone."""
        snap = NetSampler(FakeClock()).sample(
            1000.0, 1e9,
            {"watchlist": [{"listen": "tcp:not-a-port"}, {"nope": 1}, "garbage"]})
        assert [e.entity_id for e in snap.entities] == ["totals"]
        assert snap.entities[0].synthetic is False

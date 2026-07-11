"""[DM-08][DM-09][DM-10][DM-15][SA-03][SA-08][IN-08] journald parsing and the
event engine: severity mapping, store-filter, storm collapse, cursor
persistence, episode incidents end-to-end through DaemonCore."""

from __future__ import annotations

import json

import pytest

from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.model import EventRecord
from ftmon.sources.fixtures import FixtureEventSource, scenario
from ftmon.sources.journald import (
    PRIORITY_TO_SEVERITY,
    JournaldEventSource,
    parse_line,
)
from ftmon.store.db import connect
from tests.unit.test_m2_integration import core_env, notifications, tick_n  # noqa: F401

T = 1_700_000_000.0


def jline(**kw) -> bytes:
    d = {"__CURSOR": "c1", "__REALTIME_TIMESTAMP": str(int(T * 1e6)),
         "PRIORITY": "3", "SYSLOG_IDENTIFIER": "kernel",
         "MESSAGE": "Out of memory: Killed process 4001"}
    d.update(kw)
    return json.dumps(d).encode()


class TestParseLine:
    def test_full_line_golden(self):
        """[DM-07][DM-08] canonical fields from a realistic journal entry."""
        fields, cursor = parse_line(jline())
        assert cursor == "c1"
        assert fields["provider"] == "kernel"
        assert fields["severity"] == 3  # PRIORITY 3 (err) -> error
        assert fields["ts"] == pytest.approx(T)
        assert fields["event_id"] is None  # journald has no ids (PL-02)
        assert fields["source"] == "journald"

    def test_priority_mapping_table(self):
        """[DM-08] the full documented journald PRIORITY -> severity map."""
        expected = {0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 1, 6: 0, 7: 0}
        assert PRIORITY_TO_SEVERITY == expected
        for prio, sev in expected.items():
            fields, _ = parse_line(jline(PRIORITY=str(prio)))
            assert fields["severity"] == sev, f"PRIORITY={prio}"

    def test_provider_fallback_to_unit(self):
        fields, _ = parse_line(jline(SYSLOG_IDENTIFIER=None,
                                     _SYSTEMD_UNIT="cron.service"))
        assert fields["provider"] == "cron.service"

    def test_malformed_lines_are_none_never_raise(self):
        """[SA-08] malformed input skipped, not fatal."""
        for bad in (b"not json", b"[1,2]", b"{}",
                    json.dumps({"MESSAGE": "no cursor"}).encode()):
            assert parse_line(bad) is None

    def test_byte_array_message_and_2kb_truncation(self):
        """[DM-13] journald byte-array messages decode; 2KB cap applies."""
        fields, _ = parse_line(jline(MESSAGE=[104, 105]))
        assert fields["message"] == "hi"
        fields, _ = parse_line(jline(MESSAGE="x" * 5000))
        assert len(fields["message"]) == 2048


class TestQueueOverflow:
    def test_oldest_dropped_and_counted(self):
        """[SA-08] bounded queue drops oldest; drops are counted."""
        src = JournaldEventSource()
        src._queue = type(src._queue)(maxlen=3)  # shrink for the test
        for i in range(5):
            parsed = parse_line(jline(__CURSOR=f"c{i}", MESSAGE=f"m{i}"))
            with src._lock:
                if len(src._queue) == src._queue.maxlen:
                    src.dropped += 1
                src._queue.append(parsed)
        assert src.dropped == 2
        records, cursor = src.drain(now=T, max_items=10)
        assert [r.message for r in records] == ["m2", "m3", "m4"]
        assert cursor == "c4"


@pytest.fixture
def events_env(core_env):  # noqa: F811 - core_env is the imported fixture
    """core_env plus the events builtin and the oom-burst event source."""
    paths = core_env
    (paths.monitors_dir / "leak.toml").unlink()  # events only: focused runs
    builtin = (
        'schema = 1\n[monitor]\nname = "events"\ndescription = "d"\n'
        'version = 1\nenabled = true\nplatforms = ["linux"]\nsource = "events"\n'
        '[[rule]]\nid = "oom"\n'
        "when = 'provider == \"kernel\" and contains(message, \"Out of memory\")'\n"
        'severity = "critical"\ncooldown = "5m"\nclear_after = "30m"\n'
        'message = "OOM killer fired: {message}"\n'
    )
    (paths.monitors_dir / "events.toml").write_text(builtin)
    return paths


def make_core(paths, source):
    clock = FakeClock(wall=T, mono=1000.0)
    core = DaemonCore(paths=paths, clock=clock, event_source=source)
    return core, clock


class TestEpisodeEndToEnd:
    def test_oom_burst_opens_renotifies_and_quiet_clears(self, events_env):
        """[IN-08][TS-04] the oom-event-burst scenario through the real
        daemon core: one open, cooldown-limited renotifies with counts, one
        silent quiet-period clear; occurrences == 12 in the DB."""
        source = FixtureEventSource(scenario("oom-event-burst"))
        core, clock = make_core(events_env, source)
        tick_n(core, clock, 45)  # 45 sim-minutes: burst 6m + clear_after 30m

        kinds = [n["kind"] for n in notifications(events_env)]
        assert kinds[0] == "open"
        assert kinds.count("open") == 1
        assert set(kinds[1:]) == {"renotify"}  # quiet clear sends nothing
        assert any("x since open" in n["body"] for n in notifications(events_env))

        conn = connect(events_env.db_file, readonly=True)
        row = conn.execute("SELECT * FROM incidents").fetchone()
        assert row["state"] == "cleared"
        assert row["clear_reason"] == "quiet_period"
        assert row["occurrences"] == 12
        assert row["monitor"] == "events" and row["grp"] == "oom"
        # DM-15: cursor advanced to the last delivered line
        cur = conn.execute("SELECT cursor FROM cursors WHERE source='journald'"
                           ).fetchone()
        assert cur["cursor"] == "12"

    def test_restart_resumes_cursor_and_rebuilds_episode(self, events_env):
        """[DM-15][IN-08] restart mid-burst: the rebuilt daemon must not
        re-open (re-notify) the live episode nor replay delivered events."""
        source = FixtureEventSource(scenario("oom-event-burst"))
        core, clock = make_core(events_env, source)
        tick_n(core, clock, 4)  # a few events in, episode open
        opens = [n for n in notifications(events_env) if n["kind"] == "open"]
        assert len(opens) == 1

        core2, clock2 = make_core(events_env, FixtureEventSource(
            scenario("oom-event-burst")))
        # the fixture cursor (line index) must have been passed to start()
        assert core2.events_engine._last_cursor not in (None, "0")
        tick_n(core2, clock2, 3)
        opens = [n for n in notifications(events_env) if n["kind"] == "open"]
        assert len(opens) == 1  # rebuilt, not re-fired

    def test_store_filter_and_forced_storage(self, events_env):
        """[DM-09] info-level non-matching events are counted, not stored;
        rule-matching events are stored regardless of severity."""
        class ListSource(FixtureEventSource):
            def __init__(self, records):
                self._records = records
                self._alive = False

            def start(self, cursor):
                self._alive = True

            def drain(self, now, max_items):
                out, self._records = self._records, []
                return out, ("x" if out else None)

        def rec(sev, message, provider="kernel"):
            return EventRecord(ts=T, ingest_ts=T, source="journald",
                               provider=provider, event_id=None,
                               severity=sev, message=message)

        source = ListSource([
            rec(0, "chatter"),                      # info, no match -> unstored
            rec(1, "notice-level thing"),           # notice -> stored
            rec(0, "Out of memory: Killed process 1"),  # info BUT matches -> stored
        ])
        core, clock = make_core(events_env, source)
        core.on_tick(clock.now(), clock.monotonic(), 0.0)

        conn = connect(events_env.db_file, readonly=True)
        stored = [r["message"] for r in conn.execute(
            "SELECT message FROM events WHERE source='journald'")]
        assert "chatter" not in stored
        assert "notice-level thing" in stored
        assert any("Out of memory" in m for m in stored)
        assert core.stats.counters.get("events_unstored", 0) == 1

    def test_storm_collapses_into_self_event(self, events_env):
        """[DM-10] >100 stored/min from one provider collapses; a storm
        self-event records it; the flood does not reach the events table."""
        class FloodSource(FixtureEventSource):
            def __init__(self):
                self._sent = False
                self._alive = False

            def start(self, cursor):
                self._alive = True

            def drain(self, now, max_items):
                if self._sent:
                    return [], None
                self._sent = True
                return [EventRecord(
                    ts=T, ingest_ts=T, source="journald", provider="spammy",
                    event_id=None, severity=2, message=f"spam {i}")
                    for i in range(500)], "x"

        core, clock = make_core(events_env, FloodSource())
        core.on_tick(clock.now(), clock.monotonic(), 0.0)

        conn = connect(events_env.db_file, readonly=True)
        n_spam = conn.execute(
            "SELECT COUNT(*) FROM events WHERE provider='spammy'").fetchone()[0]
        assert n_spam == 100  # the cap, not the flood
        storm = conn.execute(
            "SELECT COUNT(*) FROM events WHERE message LIKE 'event_storm:%'"
        ).fetchone()[0]
        assert storm == 1

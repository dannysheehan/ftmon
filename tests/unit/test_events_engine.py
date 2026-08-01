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


class _OverflowSource:
    decl = JournaldEventSource.decl
    dropped = 0
    depth = 0

    def start(self, cursor):
        self.started_with = cursor

    def drain(self, now, max_items):
        return [], None

    def queue_depth(self):
        return self.depth

    def queue_capacity(self):
        return 10_000

    def alive(self):
        return True

    def stop(self):
        pass


class _EventWriter:
    def __init__(self):
        self.events = []

    def add_event(self, event):
        self.events.append(event)


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
    def test_journald_adjacent_run_uses_last_cursor_dm_20_dm_15(self):
        """[DM-20][DM-15] Linux coalescing retains the run's final cursor."""
        src = JournaldEventSource()
        with src._lock:
            src._offer_locked(parse_line(jline(__CURSOR="c1")))
            src._offer_locked(parse_line(jline(__CURSOR="c2")))
        records, cursor = src.drain(now=T, max_items=10)
        assert cursor == "c2"
        assert len(records) == 1
        assert records[0].attrs["repeat_count"] == "2"
        assert src.received == 2 and src.repeated == 1

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

    def test_overflow_episode_is_summarized_and_records_recovery(self):
        """[SA-08] A sustained source overflow emits one start and one final
        count rather than one self-event for every dropped record or tick."""
        from ftmon.engine.events import EventEngine

        source = _OverflowSource()
        writer = _EventWriter()
        engine = EventEngine(source, executor=object(), counter=lambda _name: None)
        engine.start(None)

        source.depth = source.queue_capacity()
        source.dropped = 7
        engine.tick([], T, 1.0, writer)
        source.dropped = 19
        engine.tick([], T + 5, 6.0, writer)
        assert len(writer.events) == 1
        assert writer.events[0].event_id == "event-overflow"
        assert "dropped 7" in writer.events[0].message

        source.depth = 0
        engine.tick([], T + 10, 11.0, writer)
        assert len(writer.events) == 2
        assert writer.events[1].event_id == "event-overflow-clear"
        assert "19 events dropped" in writer.events[1].message

    def test_raw_event_rate_includes_coalesced_repeats(self):
        """[DM-20] The rolling rate reports arrivals, not aggregate rows."""
        from ftmon.engine.events import EventEngine

        source = _OverflowSource()
        source.received = 10
        source.repeated = 8
        writer = _EventWriter()
        engine = EventEngine(source, executor=object(), counter=lambda _name: None)
        engine.start(None)
        engine.tick([], T, 10.0, writer)
        source.received = 40
        source.repeated = 35
        engine.tick([], T + 30, 40.0, writer)
        assert engine.received == 40
        assert engine.repeated == 35
        assert engine.event_rate_per_min == pytest.approx(60.0)


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
    core = DaemonCore(paths=paths, clock=clock, event_source=source, platform="linux")
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


class TestChannelSubscribeErrors:
    def test_subscribe_error_surfaces_once_as_self_event_sa_10(self, core_env):  # noqa: F811
        """[SA-10] A channel that failed to subscribe (bad name/query,
        win_evtlog.py isolates it per channel) is surfaced as a self-event
        once, not silently -- and not repeated on later ticks since nothing
        about the failure changes on its own.

        Builds its own minimal events monitor (cross-platform, unlike
        events_env's ["linux"]-only fixture builtin) since self-events
        force storage regardless of any [[rule]] -- no oom-style rule is
        needed to observe this behavior."""
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        (paths.monitors_dir / "events.toml").write_text(
            'schema = 1\n[monitor]\nname = "events"\ndescription = "d"\n'
            'version = 1\nenabled = true\nplatforms = ["linux", "windows"]\n'
            'source = "events"\n'
        )

        class BadChannelSource(FixtureEventSource):
            def __init__(self):
                self._alive = False
                self.subscribe_errors = {
                    "BadChannel": "(-1, 'EvtSubscribe', 'channel not found')",
                }

            def start(self, cursor):
                self._alive = True

            def drain(self, now, max_items):
                return [], None

        source = BadChannelSource()
        core, clock = make_core(paths, source)
        core.on_tick(clock.now(), clock.monotonic(), 0.0)

        conn = connect(paths.db_file, readonly=True)
        rows = conn.execute(
            "SELECT message FROM events WHERE provider='ftmon.events'"
        ).fetchall()
        assert len(rows) == 1
        assert "BadChannel" in rows[0]["message"]
        assert "config problem" in rows[0]["message"]

        clock.advance(60)
        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE provider='ftmon.events'"
        ).fetchone()[0]
        assert count == 1  # once per channel per daemon lifetime, not every tick


class ConfigurableFixtureSource(FixtureEventSource):
    """A FixtureEventSource that also implements the duck-typed
    configure()/subscribe_errors/configured_paths() capabilities real
    WindowsEventSource has, so DaemonCore's channel-union wiring
    (_union_event_channels/_start_events/_warn_on_unapplied_event_channels)
    can be exercised without a live Windows box."""

    def __init__(self, scn):
        super().__init__(scn)
        self.subscribe_errors: dict[str, str] = {}
        self.configure_calls: list = []
        self._configured: tuple = ()

    def configure(self, channels):
        self.configure_calls.append(channels)
        self._configured = channels

    def configured_paths(self):
        return frozenset(c.path for c in self._configured)


def _events_toml(name: str, channels: str = "") -> str:
    return (
        f'schema = 1\n[monitor]\nname = "{name}"\ndescription = "d"\n'
        'version = 1\nenabled = true\nplatforms = ["linux", "windows"]\n'
        f'source = "events"\n\n{channels}'
    )


class TestEventChannelUnion:
    """[DM-19] DaemonCore._union_event_channels()/_start_events()/
    _warn_on_unapplied_event_channels(): channel config is unioned across
    every loaded event monitor (one shared EvtSubscribe pass for the whole
    daemon), conflicting queries for the same channel keep the first-seen
    one and get reported, and a channel requested only after the reader
    already started needs a restart to actually apply."""

    def test_union_across_monitors_calls_configure_once(self, core_env):  # noqa: F811
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        (paths.monitors_dir / "events.toml").write_text(_events_toml(
            "events", '[[source_options.channels]]\npath = "System"\n'))
        (paths.monitors_dir / "events_extra.toml").write_text(_events_toml(
            "events_extra",
            '[[source_options.channels]]\npath = "Security"\n'
            'query = "*[System[EventID=4688]]"\n'))

        source = ConfigurableFixtureSource(scenario("oom-event-burst"))
        DaemonCore(paths=paths, clock=FakeClock(wall=T, mono=1000.0), event_source=source)

        assert len(source.configure_calls) == 1
        got = {c.path: c.query for c in source.configure_calls[0]}
        assert got == {"System": None, "Security": "*[System[EventID=4688]]"}

    def test_no_channels_declared_never_calls_configure(self, core_env):  # noqa: F811
        """The generic/default events.toml (no [source_options] at all)
        must not override WindowsEventSource's own default channel list."""
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        (paths.monitors_dir / "events.toml").write_text(_events_toml("events"))

        source = ConfigurableFixtureSource(scenario("oom-event-burst"))
        DaemonCore(paths=paths, clock=FakeClock(wall=T, mono=1000.0), event_source=source)
        assert source.configure_calls == []

    def test_conflicting_queries_keep_first_seen_and_report_once(self, core_env):  # noqa: F811
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        # sorted(glob()) load order: events.toml before events_extra.toml
        (paths.monitors_dir / "events.toml").write_text(_events_toml(
            "events",
            '[[source_options.channels]]\npath = "Security"\n'
            'query = "*[System[EventID=4688]]"\n'))
        (paths.monitors_dir / "events_extra.toml").write_text(_events_toml(
            "events_extra",
            '[[source_options.channels]]\npath = "Security"\n'
            'query = "*[System[EventID=9999]]"\n'))

        source = ConfigurableFixtureSource(scenario("oom-event-burst"))
        clock = FakeClock(wall=T, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, event_source=source)

        got = {c.path: c.query for c in source.configure_calls[0]}
        assert got["Security"] == "*[System[EventID=4688]]"  # first-seen wins
        assert "Security" in source.subscribe_errors  # conflict recorded post-start

        core.on_tick(clock.now(), clock.monotonic(), 0.0)
        conn = connect(paths.db_file, readonly=True)
        rows = conn.execute(
            "SELECT message FROM events WHERE provider='ftmon.events'"
        ).fetchall()
        assert len(rows) == 1
        assert "conflicting queries" in rows[0]["message"]

    @pytest.mark.parametrize("filtered_loads_first", [True, False])
    def test_unfiltered_query_wins_regardless_of_monitor_load_order(
        self, core_env, filtered_loads_first,  # noqa: F811
    ):
        """An unfiltered (query=None) request is a superset of any filtered
        one, so it must win no matter which monitor loads first -- and this
        is not a reportable conflict, just resolving "everything" and "a
        subset of it" to "everything"."""
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        filtered = (
            '[[source_options.channels]]\npath = "Security"\n'
            'query = "*[System[EventID=4688]]"\n'
        )
        unfiltered = '[[source_options.channels]]\npath = "Security"\n'
        first_body, second_body = (
            (filtered, unfiltered) if filtered_loads_first else (unfiltered, filtered)
        )
        (paths.monitors_dir / "events.toml").write_text(_events_toml("events", first_body))
        (paths.monitors_dir / "events_extra.toml").write_text(
            _events_toml("events_extra", second_body))

        source = ConfigurableFixtureSource(scenario("oom-event-burst"))
        DaemonCore(paths=paths, clock=FakeClock(wall=T, mono=1000.0), event_source=source)

        got = {c.path: c.query for c in source.configure_calls[0]}
        assert got["Security"] is None  # unfiltered wins either way
        assert "Security" not in source.subscribe_errors  # not a conflict

    def test_new_monitor_channel_after_start_needs_restart_self_event(self, core_env):  # noqa: F811
        paths = core_env
        (paths.monitors_dir / "leak.toml").unlink()
        (paths.monitors_dir / "events.toml").write_text(_events_toml(
            "events", '[[source_options.channels]]\npath = "System"\n'))

        source = ConfigurableFixtureSource(scenario("oom-event-burst"))
        clock = FakeClock(wall=T, mono=1000.0)
        core = DaemonCore(paths=paths, clock=clock, event_source=source)
        assert core.events_engine._started is True
        assert source.configured_paths() == {"System"}

        # a new monitor requesting an unconfigured channel appears after boot
        (paths.monitors_dir / "events_extra.toml").write_text(_events_toml(
            "events_extra", '[[source_options.channels]]\npath = "Security"\n'))
        core.on_tick(clock.now(), clock.monotonic(), 0.0)  # rescan picks it up

        assert "Security" in source.subscribe_errors
        conn = connect(paths.db_file, readonly=True)
        rows = conn.execute(
            "SELECT message FROM events WHERE provider='ftmon.events'"
        ).fetchall()
        assert any("restart the daemon" in r["message"] for r in rows)
        # System was already configured before boot -- not flagged
        assert not any("System" in r["message"] for r in rows)


class TestStoreMinSeverityAcrossMonitors:
    """[DM-09] The store-filter is shared by the whole daemon -- one
    EventEngine, not one per monitor (same reasoning as
    _union_event_channels) -- so _store_min must combine every loaded event
    monitor's declared threshold rather than using whichever happened to
    load first."""

    def test_takes_minimum_across_all_monitors_not_first_found(self):
        from types import SimpleNamespace

        from ftmon.engine.events import EventEngine

        strict = SimpleNamespace(source_options={"store_min_severity": "error"})
        loose = SimpleNamespace(source_options={"store_min_severity": "info"})
        assert EventEngine._store_min([strict, loose]) == 0  # info, the looser one
        assert EventEngine._store_min([loose, strict]) == 0  # order-independent

    def test_mixed_int_and_name_forms(self):
        from types import SimpleNamespace

        from ftmon.engine.events import EventEngine

        as_int = SimpleNamespace(source_options={"store_min_severity": 3})
        as_name = SimpleNamespace(source_options={"store_min_severity": "notice"})
        assert EventEngine._store_min([as_int, as_name]) == 1  # notice(1) < error(3)

    def test_defaults_to_notice_when_nothing_declared(self):
        from types import SimpleNamespace

        from ftmon.engine.events import EventEngine

        assert EventEngine._store_min([SimpleNamespace(source_options={})]) == 1

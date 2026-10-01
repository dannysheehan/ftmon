"""[EC-11] Collection failures remain visible independently of monitor author rules."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ftmon.checks.health import collection_health_rules, runtime_rules
from ftmon.checks.model import RawCheckResult, unknown
from ftmon.clock import FakeClock
from ftmon.daemon import DaemonCore
from ftmon.definitions.loader import load_text
from ftmon.model import TriBool
from ftmon.paths import get_paths
from tests.platform_permissions import make_private, toml_path, trusted_python_executable

_DEFINITION = '''schema = 1
[monitor]
name = "probe"
description = "Collection health fixture"
version = 1
enabled = true
platforms = ["linux"]
interval = "15s"
source = "external"
[source_options]
check = "probe"
entity = "device"
[[source_options.perfdata]]
label = "temp"
metric = "temperature"
plugin_uom = "C"
unit = "celsius"
kind = "gauge"
[[source_options.perfdata]]
label = "optional"
metric = "fan"
plugin_uom = "%"
unit = "percent"
kind = "gauge"
'''


def _rule(when, *, rid="arbitrary", group="arbitrary", confirm=2):
    return f'''
[[rule]]
id = "{rid}"
group = "{group}"
when = '{when}'
severity = "warning"
confirm_cycles = {confirm}
clear_cycles = 2
message = "{{plugin_message}}"
'''


class Runner:
    result = RawCheckResult(0, "OK", 0.01, {"temp": (30, "C")})

    def run(self, spec, deadline_mono):
        return self.result


@pytest.fixture
def core_factory(tmp_path):
    cores = []

    def create(text=_DEFINITION, paths=None, clock=None):
        if paths is None:
            paths = get_paths({
                "FTMON_CONFIG_DIR": str(tmp_path / "cfg"),
                "FTMON_DATA_DIR": str(tmp_path / "data"),
                "FTMON_STATE_DIR": str(tmp_path / "state"),
                "FTMON_RUNTIME_DIR": str(tmp_path / "run"),
            })
            paths.ensure()
            paths.check_registry_file.write_text(
                f'[check.probe]\nargv=["{toml_path(trusted_python_executable())}"]\n'
                'protocol="ftmon-json"\ntimeout="2s"\n'
            )
            make_private(paths.check_registry_file, 0o600)
            (paths.monitors_dir / "probe.toml").write_text(text)
        core = DaemonCore(paths, clock or FakeClock(wall=1_700_000_000, mono=1000),
                          platform="linux", notifiers=[])
        core.external_sampler._runner = Runner()
        cores.append(core)
        return core

    yield create
    for core in cores:
        core.conn.close()


def _tick(core, result=None, *, seconds=15):
    if result is not None:
        core.external_sampler._runner.result = result
    core.on_tick(core.clock.now(), core.clock.monotonic(), 0)
    core.clock.advance(seconds)


def _incidents(core):
    return [dict(r) for r in core.conn.execute('SELECT * FROM incidents ORDER BY id')]


@pytest.mark.parametrize("result", [unknown(2, "timeout"), unknown(0, "json"),
                                     RawCheckResult(3, "generic dependency failure", 0.1, {})])
def test_core_health_confirms_and_recovers_without_authored_rule(core_factory, result):
    """[EC-11][IN-01] Invalid runs and valid UNKNOWN JSON share bounded health recovery."""
    core = core_factory()
    _tick(core, result)
    assert not _incidents(core)
    _tick(core)
    row, = _incidents(core)
    assert (row['grp'], row['owning_rule'], row['state'], row['severity']) == (
        '@check-health', '@check-health', 'open', 2,
    )
    # Warning/critical plugin states are valid results, not collection failure.
    _tick(core, RawCheckResult(1, "warm", 0.01, {"temp": (81, "C")}))
    assert _incidents(core)[0]['state'] == 'open'
    _tick(core, RawCheckResult(2, "hot", 0.01, {"temp": (91, "C")}))
    assert _incidents(core)[0]['clear_reason'] == 'recovered'
    assert [r[0] for r in core.conn.execute('SELECT kind FROM notifications ORDER BY id')] == [
        'open', 'recover',
    ]


@pytest.mark.parametrize('when', ['plugin_state == 3', '3 == plugin_state', 'plugin_ok == 0',
                                   'plugin_state > 0',
                                   'plugin_state == 3 or temperature > 80'])
def test_authored_status_owner_deduplicates_by_condition(core_factory, when):
    """[EC-11] Rule/group spelling has no part in collection health ownership."""
    core = core_factory(_DEFINITION + _rule(when, confirm=3))
    for _ in range(3):
        _tick(core, RawCheckResult(3, 'cannot collect', 0, {}))
    row, = _incidents(core)
    assert row['owning_rule'] == 'arbitrary'
    assert core.conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 1
    assert len(runtime_rules(core.monitors['probe'])) == 1


@pytest.mark.parametrize('when', ['plugin_state == 1', 'temperature > 80',
                                   'plugin_state == 3 and temperature > 80',
                                   'avg(plugin_state, "5m") == 3'])
def test_unreliable_conditions_do_not_replace_health_coverage(when):
    """[EC-11] A cold window or missing reading cannot suppress the core health rule."""
    mdef = load_text(_DEFINITION + _rule(when))
    assert collection_health_rules(mdef)[0].id == '@check-health'


def test_skip_does_not_confirm_recover_or_mark_synthetic_entity_gone(core_factory):
    """[EC-08][EC-11][IN-01] Budget starvation must not clear a real failure."""
    core = core_factory()
    _tick(core, unknown(2, 'timeout'))
    _tick(core)
    prepare = core.external_sampler.prepare
    core.external_sampler.prepare = lambda *_: core.external_sampler._results.clear()
    for _ in range(3):
        _tick(core, seconds=301)
    assert _incidents(core)[0]['state'] == 'open'
    row = core.conn.execute(
        'SELECT gone_ts FROM entities WHERE monitor="probe"',
    ).fetchone()
    assert row[0] is None
    core.external_sampler.prepare = prepare
    _tick(core, Runner.result)
    assert _incidents(core)[0]['state'] == 'open'
    _tick(core)
    assert _incidents(core)[0]['state'] == 'cleared'


@pytest.mark.parametrize("condition", [
    "average_temp > 80",
    'coverage(temperature, "60s") >= 0.8 and average_temp > 80',
])
def test_missing_input_cannot_reuse_old_window_or_clear_threshold(core_factory, condition):
    """[EC-11][IN-01] Current absence masks old raw and derived readings; siblings survive."""
    definition = _DEFINITION + '''
[[derived]]
name = "average_temp"
expr = 'avg(temperature, "60s")'
''' + _rule(condition, rid='hot', group='thermal')
    core = core_factory(definition)
    for _ in range(7):
        _tick(core, RawCheckResult(0, 'hot', 0, {'temp': (90, 'C')}))
    assert _incidents(core)[0]['state'] == 'open'
    _tick(core, RawCheckResult(3, 'sensor missing', 0, {'optional': (20, '%')}))
    _tick(core)
    report = json.loads(core.conn.execute(
        'SELECT value FROM meta WHERE key="external_observations"',
    ).fetchone()[0])['monitors']['probe']
    assert report['rules']['hot'] == 'UNKNOWN'
    assert 'fan' in report['available_metrics']
    assert 'temperature' not in report['available_metrics']
    assert 'average_temp' not in report['available_metrics']
    assert _incidents(core)[0]['state'] == 'open'
    for _ in range(7):
        _tick(core, RawCheckResult(0, 'cool', 0, {'temp': (30, 'C')}))
    assert all(r['state'] == 'cleared' for r in _incidents(core))


def test_health_restart_ack_and_definition_change_use_normal_incident_lifecycle(core_factory):
    """[EC-11][IN-02][MD-06] Core health survives restart, then supersedes on disable."""
    core = core_factory()
    _tick(core, unknown(2, 'timeout'))
    _tick(core)
    core.conn.execute("UPDATE incidents SET state='acked',ack_by='user',ack_ts=?",
                      (core.clock.now(),))
    core.conn.commit()
    restarted = core_factory(paths=core.paths, clock=core.clock)
    _tick(restarted, unknown(2, 'timeout'))
    assert _incidents(restarted)[0]['state'] == 'acked'
    assert restarted.conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 1
    path = core.paths.monitors_dir / 'probe.toml'
    path.write_text(_DEFINITION.replace('enabled = true', 'enabled = false'))
    restarted.clock.advance(31)
    _tick(restarted)
    assert _incidents(restarted)[0]['clear_reason'] == 'superseded'
    report = restarted.pipeline.external_report(
        restarted.monitors, restarted.clock.now(), daemon_pid=1,
    )
    assert not report['monitors']


def test_external_observation_report_bounds_and_definition_identity(core_factory):
    """[EC-11] Large fleets of aliases/rules cannot create unbounded metadata."""
    core = core_factory()
    _tick(core, unknown(2, 'timeout'))
    mdef = core.monitors['probe']
    record = core.pipeline._external_observations['probe']
    monitors = {f'probe{i}': replace(mdef, name=f'probe{i}') for i in range(80)}
    core.pipeline._external_observations = {
        name: {**record, 'plugin_message': '\N{SNOWMAN}' * 2048,
               'rules': {f'rule{i}': TriBool.UNKNOWN.name for i in range(128)}}
        for name in list(monitors)[:64]
    }
    report = core.pipeline.external_report(monitors, core.clock.now(), daemon_pid=9)
    assert report['truncated']
    assert len(json.dumps(report).encode()) <= 64 * 1024
    assert len(report['monitors']) <= 64
    assert core.pipeline.external_report({}, core.clock.now(), daemon_pid=9)['monitors'] == {}


def test_replacement_observation_gets_slot_on_first_run_at_capacity(core_factory):
    """[EC-11][MD-09] Reload frees old report slots before a replacement samples."""
    core = core_factory()
    _tick(core)
    previous = core.pipeline._external_observations['probe']
    core.pipeline._external_observations.update({
        f'old{i}': previous.copy() for i in range(63)
    })
    assert len(core.pipeline._external_observations) == 64

    (core.paths.monitors_dir / 'probe.toml').unlink()
    (core.paths.monitors_dir / 'replacement.toml').write_text(
        _DEFINITION.replace('name = "probe"', 'name = "replacement"')
    )
    core._load_definitions()
    assert core.pipeline._external_observations == {}

    _tick(core)
    assert set(core.pipeline._external_observations) == {'replacement'}
    report = json.loads(core.conn.execute(
        'SELECT value FROM meta WHERE key="external_observations"',
    ).fetchone()[0])
    assert set(report['monitors']) == {'replacement'}
    assert not report['truncated']


def test_resumed_check_waits_for_derived_warmup_and_normal_clear_cycles(core_factory):
    """[EC-11][IN-01] Healthy collection can recover before a cold slope is evaluable."""
    definition = _DEFINITION + '''
[[derived]]
name = "temperature_rate"
expr = 'slope(temperature, "60s")'
''' + _rule('temperature_rate > 0.1', rid='rise', group='thermal-rise')
    core = core_factory(definition)
    for temp in (40, 50, 60, 70):
        _tick(core, RawCheckResult(0, 'rising', 0, {'temp': (temp, 'C')}))
    assert _incidents(core)[0]['state'] == 'open'
    for _ in range(2):
        _tick(core, unknown(2, 'timeout'), seconds=90)
    for _ in range(2):
        _tick(core, RawCheckResult(0, 'steady', 0, {'temp': (40, 'C')}))
        assert _incidents(core)[0]['state'] == 'open'
        assert core.pipeline._external_observations['probe']['rules']['rise'] == 'UNKNOWN'
    assert _incidents(core)[1]['state'] == 'cleared'
    _tick(core)
    assert core.pipeline._external_observations['probe']['rules']['rise'] == 'FALSE'
    assert _incidents(core)[0]['state'] == 'open'
    _tick(core)
    assert _incidents(core)[0]['clear_reason'] == 'recovered'

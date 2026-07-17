"""[SA-04, SA-05, DM-02, SE-04, PL-03] Tests for Linux samplers.

Tests process, disk, and system samplers with monkeypatched psutil,
parse_psi parser, and real-system opt-in smoke tests.
"""

from __future__ import annotations

import types
from io import StringIO

import pytest

from ftmon.clock import FakeClock
from ftmon.sources.disk import DiskSampler
from ftmon.sources.process import ProcessSampler
from ftmon.sources.system import SystemSampler, parse_psi

# --- parse_psi golden tests [EX-06, EX-07] ---


def test_parse_psi_golden_some_avg60():
    """[SA-04] Parse PSI avg60 from a 'some' line."""
    line = "some avg10=1.50 avg60=2.75 avg300=0.90 total=123"
    assert parse_psi(line) == 2.75


def test_parse_psi_various_precisions():
    """[SA-04] PSI parsing works with various decimal precisions."""
    assert parse_psi("some avg10=0.00 avg60=0.00 avg300=0.00 total=0") == 0.0
    assert parse_psi("some avg10=99.99 avg60=100.00 avg300=99.99 total=999") == 100.0
    assert parse_psi("some avg10=1 avg60=2 avg300=3 total=4") == 2.0


def test_parse_psi_invalid_returns_none():
    """[SA-04] Unparseable PSI lines return None."""
    assert parse_psi("") is None
    # Note: parse_psi just looks for "avg60=" anywhere, doesn't validate
    # that the line starts with "some". That filtering is caller's job.
    # So this test verifies parse_psi extracts avg60 when present, None when not.
    assert parse_psi("some avg10=1.5 noavg60 avg300=3.0 total=100") is None
    assert parse_psi("some garbage text") is None
    assert parse_psi("no avg60 here") is None


# --- ProcessSampler tests ---


def test_process_iteration_does_not_prefetch_unused_attributes(monkeypatch):
    """[SA-04] Avoid psutil as_dict prefetch of costly, unused process data."""
    calls = []

    def process_iter(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr("psutil.process_iter", process_iter)

    ProcessSampler(FakeClock()).sample(now=1000.0, deadline_mono=2000.0, options={})

    # Passing attrs=[] is not equivalent: affected psutil versions prefetch
    # every attribute, including memory maps and network connections.
    assert calls == [((), {})]


def test_process_entity_id_format(monkeypatch):
    """[DM-02] entity_id is '{name}:{pid}:{create_time}' where create_time is int."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)

    fake_proc = types.SimpleNamespace(
        pid=1234, name=lambda: "firefox", create_time=lambda: 1609459200.5
    )

    procs_list = [fake_proc]
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: procs_list)

    # Add mock methods for attribute collection
    def add_mock_methods(obj):
        obj.memory_info = lambda: types.SimpleNamespace(rss=123456)
        obj.num_fds = lambda: 42
        obj.num_threads = lambda: 5
        obj.io_counters = lambda: types.SimpleNamespace(
            read_bytes=1000, write_bytes=2000
        )
        obj.cmdline = lambda: ["firefox"]
        obj.username = lambda: "user"
        obj.exe = lambda: "/usr/bin/firefox"
        obj.cpu_percent = lambda interval: 5.0

    add_mock_methods(fake_proc)

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    assert len(snapshot.entities) == 1
    # create_time 1609459200.5 should become int 1609459200
    assert snapshot.entities[0].entity_id == "firefox:1234:1609459200"


def test_process_cmdline_truncation_to_256(monkeypatch):
    """[SE-04] Process cmdline is truncated to 256 chars."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)

    long_cmdline = ["prog"] + ["arg"] * 100  # Long argument list
    fake_proc = types.SimpleNamespace(
        pid=5678,
        name=lambda: "longcmd",
        create_time=lambda: 1609459200.0,
    )

    def add_methods(obj):
        obj.cmdline = lambda: long_cmdline
        obj.memory_info = lambda: types.SimpleNamespace(rss=100000)
        obj.num_fds = lambda: 10
        obj.num_threads = lambda: 1
        obj.io_counters = lambda: types.SimpleNamespace(
            read_bytes=0, write_bytes=0
        )
        obj.username = lambda: "user"
        obj.exe = lambda: "/prog"
        obj.cpu_percent = lambda interval: 0.0

    add_methods(fake_proc)
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [fake_proc])

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    assert len(snapshot.entities) == 1
    cmdline = snapshot.entities[0].attrs.get("cmdline", "")
    assert len(cmdline) <= 256
    # With many "arg" words, the truncation is exact at 256
    assert cmdline == " ".join(long_cmdline)[:256]


def _fake_proc(pid, name, create_time=1609459200.0, exe=None, cmdline=None):
    """Bare SimpleNamespace process with the metric methods SA-09 tests
    don't care about stubbed to harmless constants."""
    proc = types.SimpleNamespace(
        pid=pid, name=lambda: name, create_time=lambda: create_time
    )
    proc.cmdline = lambda: list(cmdline) if cmdline is not None else []
    proc.username = lambda: "user"
    if exe is not None:
        proc.exe = lambda: exe
    else:
        proc.exe = lambda: (_ for _ in ()).throw(__import__("psutil").AccessDenied())
    proc.memory_info = lambda: types.SimpleNamespace(rss=1000)
    proc.num_fds = lambda: 1
    proc.num_threads = lambda: 1
    proc.io_counters = lambda: types.SimpleNamespace(read_bytes=0, write_bytes=0)
    proc.cpu_percent = lambda interval: 0.0
    return proc


def test_process_display_identity_from_exe_basename_sa_09(monkeypatch):
    """[SA-09] MainThread + exe agent -> exe_base/display/cmd_hint recover
    a recognizable identity from an interpreter-hosted process."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)
    proc = _fake_proc(
        1, "MainThread",
        exe="/home/u/.local/bin/agent",
        cmdline=[
            "/home/u/.local/bin/agent",
            "--use-system-ca",
            "/home/u/.local/share/cursor-agent/versions/1.2/index.js",
        ],
    )
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [proc])

    attrs = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={}).entities[0].attrs
    assert attrs["exe_base"] == "agent"
    assert attrs["display"] == "agent (MainThread)"
    assert attrs["cmd_hint"] == "agent index.js"


def test_process_display_falls_back_when_exe_basename_matches_name_sa_09(monkeypatch):
    """[SA-09] exe basename == name -> display is just the name, no noise."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)
    proc = _fake_proc(2, "firefox", exe="/usr/bin/firefox", cmdline=["firefox"])
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [proc])

    attrs = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={}).entities[0].attrs
    assert attrs["exe_base"] == "firefox"
    assert attrs["display"] == "firefox"


def test_process_display_falls_back_to_name_when_exe_unreadable_sa_09(monkeypatch):
    """[SA-09][PL-03] exe denied -> no exe_base/cmd_hint; display falls back
    to the plain kernel name."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)
    proc = _fake_proc(3, "restricted", exe=None, cmdline=["restricted", "/etc/x"])
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [proc])

    attrs = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={}).entities[0].attrs
    assert "exe_base" not in attrs
    assert "cmd_hint" not in attrs
    assert attrs["display"] == "restricted"


def test_process_cmd_hint_absent_without_path_like_argument_sa_09(monkeypatch):
    """[SA-09] no argument contains '/' -> cmd_hint omitted (module's
    omitted-not-empty pattern), even though display still resolves."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)
    proc = _fake_proc(
        4, "worker", exe="/usr/bin/python3", cmdline=["python3", "script.py"]
    )
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [proc])

    attrs = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={}).entities[0].attrs
    assert attrs["display"] == "python3 (worker)"
    assert "cmd_hint" not in attrs


def test_process_cmd_hint_capped_at_64_chars_sa_09(monkeypatch):
    """[SA-09] cmd_hint is capped to 64 chars total, derived basenames only."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)
    long_arg = "/very/long/path/" + "x" * 80 + ".js"
    proc = _fake_proc(
        5, "node", exe="/usr/bin/node", cmdline=["/usr/bin/node", long_arg]
    )
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [proc])

    attrs = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={}).entities[0].attrs
    expected = f"node {'x' * 80}.js"[:64]
    assert attrs["cmd_hint"] == expected
    assert len(attrs["cmd_hint"]) == 64


def test_process_access_denied_omits_metric_not_entity(monkeypatch):
    """[PL-03] AccessDenied on one metric -> metric omitted but entity present."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)

    fake_proc = types.SimpleNamespace(
        pid=9999, name=lambda: "restricted", create_time=lambda: 1609459200.0
    )

    def add_methods(obj):
        obj.cmdline = lambda: ["restricted"]
        obj.username = lambda: "user"
        obj.exe = lambda: "/restricted"
        obj.cpu_percent = lambda interval: 10.0
        obj.memory_info = lambda: types.SimpleNamespace(rss=50000)
        # num_fds raises AccessDenied
        obj.num_fds = lambda: (_ for _ in ()).throw(
            __import__("psutil").AccessDenied()
        )
        obj.num_threads = lambda: 3
        obj.io_counters = lambda: types.SimpleNamespace(
            read_bytes=100, write_bytes=200
        )

    add_methods(fake_proc)
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [fake_proc])

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    # Entity is present despite one metric failing
    assert entity.entity_id == "restricted:9999:1609459200"
    # num_fds should be absent
    assert "num_fds" not in entity.metrics
    # Other metrics present
    assert "cpu_pct" in entity.metrics
    assert "rss_bytes" in entity.metrics
    assert "num_threads" in entity.metrics


def test_process_no_such_process_skips_entity(monkeypatch):
    """[PL-03, SA-05] NoSuchProcess -> entity skipped."""
    clock = FakeClock()
    sampler = ProcessSampler(clock)

    fake_proc = types.SimpleNamespace(pid=7777)

    def create_time_gone():
        raise __import__("psutil").NoSuchProcess(7777)

    fake_proc.create_time = create_time_gone
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [fake_proc])

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    # Entity should be skipped because create_time() failed
    assert len(snapshot.entities) == 0


def test_process_deadline_stops_iteration(monkeypatch):
    """[SA-02] Deadline check stops iteration mid-stream."""
    clock = FakeClock(mono=1000.0)
    sampler = ProcessSampler(clock)

    # Three fake processes
    procs = [
        types.SimpleNamespace(
            pid=111, name=lambda: "proc1", create_time=lambda: 1609459200.0
        ),
        types.SimpleNamespace(
            pid=222, name=lambda: "proc2", create_time=lambda: 1609459200.0
        ),
        types.SimpleNamespace(
            pid=333, name=lambda: "proc3", create_time=lambda: 1609459200.0
        ),
    ]

    for proc in procs:

        def add_methods(p):
            p.memory_info = lambda: types.SimpleNamespace(rss=10000)
            p.num_fds = lambda: 5
            p.num_threads = lambda: 1
            p.io_counters = lambda: types.SimpleNamespace(
                read_bytes=0, write_bytes=0
            )
            p.cmdline = lambda: [p.name()]
            p.username = lambda: "user"
            p.exe = lambda: "/bin/proc"
            p.cpu_percent = lambda interval: 0.0

        add_methods(proc)

    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: procs)

    # Deadline is at 1002, clock starts at 1000. After 2 entities, we advance
    # the clock past the deadline.
    def side_effect(*args, **kwargs):
        # Return processes one by one, advancing clock after each
        for i, proc in enumerate(procs):
            if i == 2:  # After 2nd entity, jump past deadline
                clock.advance(5)  # Now at 1005, past deadline of 1002
            yield proc

    monkeypatch.setattr("psutil.process_iter", side_effect)

    snapshot = sampler.sample(
        now=1609459200.0, deadline_mono=1002.0, options={}
    )
    # Should have stopped after 2 entities
    assert len(snapshot.entities) == 2
    assert snapshot.entities[0].entity_id == "proc1:111:1609459200"
    assert snapshot.entities[1].entity_id == "proc2:222:1609459200"


# --- DiskSampler tests ---


def test_disk_inode_used_pct_computation(monkeypatch):
    """[SA-04, DM-02] Inode usage % computed from statvfs."""
    clock = FakeClock()
    sampler = DiskSampler(clock)

    fake_partition = types.SimpleNamespace(
        device="/dev/sda1",
        mountpoint="/mnt",
        fstype="ext4",
    )

    fake_usage = types.SimpleNamespace(
        total=1000000, used=600000, free=400000, percent=60.0
    )

    monkeypatch.setattr(
        "psutil.disk_partitions", lambda *a, **k: [fake_partition]
    )
    monkeypatch.setattr(
        "psutil.disk_usage", lambda *a, **k: fake_usage
    )

    # Fake statvfs: 10000 total inodes, 7000 used, 3000 free
    fake_statvfs = types.SimpleNamespace(f_files=10000, f_ffree=3000)
    monkeypatch.setattr(
        "os.statvfs", lambda *a, **k: fake_statvfs
    )

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    # Inode usage = (10000 - 3000) / 10000 * 100 = 70%
    assert entity.metrics["inode_used_pct"] == 70.0


def test_disk_inode_omitted_when_f_files_zero(monkeypatch):
    """[SA-04, DM-02] inode_used_pct omitted when f_files == 0."""
    clock = FakeClock()
    sampler = DiskSampler(clock)

    fake_partition = types.SimpleNamespace(
        device="/dev/loop0",
        mountpoint="/mnt/loop",
        fstype="tmpfs",
    )

    fake_usage = types.SimpleNamespace(
        total=1000000, used=100000, free=900000, percent=10.0
    )

    monkeypatch.setattr(
        "psutil.disk_partitions", lambda *a, **k: [fake_partition]
    )
    monkeypatch.setattr(
        "psutil.disk_usage", lambda *a, **k: fake_usage
    )

    # Fake statvfs with f_files == 0 (no inode support)
    fake_statvfs = types.SimpleNamespace(f_files=0, f_ffree=0)
    monkeypatch.setattr(
        "os.statvfs", lambda *a, **k: fake_statvfs
    )

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    # inode_used_pct should NOT be in metrics
    assert "inode_used_pct" not in entity.metrics
    # But other metrics should be present
    assert entity.metrics["total_bytes"] == 1000000.0


def test_disk_permission_error_skips_mount(monkeypatch):
    """[PL-03, DM-02] OSError/PermissionError -> skip mount."""
    clock = FakeClock()
    sampler = DiskSampler(clock)

    fake_partition = types.SimpleNamespace(
        device="/dev/sdb1",
        mountpoint="/restricted",
        fstype="ntfs",
    )

    monkeypatch.setattr(
        "psutil.disk_partitions", lambda *a, **k: [fake_partition]
    )

    def usage_raises(*args, **kwargs):
        raise PermissionError("Access denied")

    monkeypatch.setattr("psutil.disk_usage", usage_raises)

    snapshot = sampler.sample(now=1609459200.0, deadline_mono=2000.0, options={})
    # Mount should be skipped
    assert len(snapshot.entities) == 0


def test_disk_deadline_stops_iteration(monkeypatch):
    """[SA-02, DM-02] Deadline stops iteration."""
    clock = FakeClock(mono=1000.0)
    sampler = DiskSampler(clock)

    partitions = [
        types.SimpleNamespace(
            device="/dev/sda1", mountpoint="/", fstype="ext4"
        ),
        types.SimpleNamespace(
            device="/dev/sda2", mountpoint="/home", fstype="ext4"
        ),
        types.SimpleNamespace(
            device="/dev/sdb1", mountpoint="/data", fstype="ext4"
        ),
    ]

    fake_usage = types.SimpleNamespace(
        total=1000000, used=500000, free=500000, percent=50.0
    )

    def iter_side_effect():
        for i, p in enumerate(partitions):
            if i == 2:  # After 2nd mount, jump past deadline
                clock.advance(5)
            yield p

    monkeypatch.setattr(
        "psutil.disk_partitions", lambda *a, **k: iter_side_effect()
    )
    monkeypatch.setattr(
        "psutil.disk_usage", lambda *a, **k: fake_usage
    )

    fake_statvfs = types.SimpleNamespace(f_files=0, f_ffree=0)
    monkeypatch.setattr("os.statvfs", lambda *a, **k: fake_statvfs)

    snapshot = sampler.sample(
        now=1609459200.0, deadline_mono=1002.0, options={}
    )
    # Should stop after 2 mounts
    assert len(snapshot.entities) == 2


# --- SystemSampler tests ---


def test_system_single_entity_hostname(monkeypatch):
    """[SA-04, DM-02] SystemSampler produces single 'system' entity."""
    clock = FakeClock()
    sampler = SystemSampler(clock)

    monkeypatch.setattr("socket.gethostname", lambda: "testhost")
    monkeypatch.setattr("os.getloadavg", lambda: (1.0, 2.0, 3.0))
    monkeypatch.setattr(
        "psutil.cpu_percent", lambda interval: 25.0
    )
    monkeypatch.setattr(
        "psutil.virtual_memory",
        lambda: types.SimpleNamespace(
            total=8000000000,
            available=6000000000,
            used=2000000000,
        ),
    )
    monkeypatch.setattr(
        "psutil.swap_memory",
        lambda: types.SimpleNamespace(percent=10.0),
    )

    # No PSI files - make open raise FileNotFoundError
    def mock_open_raises(path, *args, **kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", mock_open_raises)

    snapshot = sampler.sample(
        now=1609459200.0, deadline_mono=2000.0, options={}
    )
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    assert entity.entity_id == "system"
    assert entity.attrs["hostname"] == "testhost"
    assert entity.metrics["load1"] == 1.0
    assert entity.metrics["load5"] == 2.0
    assert entity.metrics["load15"] == 3.0
    assert entity.metrics["cpu_pct"] == 25.0
    assert entity.metrics["mem_total_bytes"] == 8000000000.0


def test_system_psi_metrics_parsed(monkeypatch):
    """[SA-04, EX-06] SystemSampler parses PSI from /proc/pressure."""
    clock = FakeClock()
    sampler = SystemSampler(clock)

    monkeypatch.setattr("socket.gethostname", lambda: "testhost")
    monkeypatch.setattr("os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("psutil.cpu_percent", lambda interval: 0.0)
    monkeypatch.setattr(
        "psutil.virtual_memory",
        lambda: types.SimpleNamespace(total=1000, available=500, used=500),
    )
    monkeypatch.setattr(
        "psutil.swap_memory",
        lambda: types.SimpleNamespace(percent=0.0),
    )

    # Mock file opens to return PSI data using StringIO
    psi_data = {
        "/proc/pressure/cpu": (
            "some avg10=1.50 avg60=2.75 avg300=0.90 total=123\n"
        ),
        "/proc/pressure/memory": (
            "some avg10=0.50 avg60=1.00 avg300=1.50 total=456\n"
        ),
        "/proc/pressure/io": (
            "some avg10=0.10 avg60=0.20 avg300=0.30 total=789\n"
        ),
    }

    def mock_open(path, *args, **kwargs):
        if path in psi_data:
            return StringIO(psi_data[path])
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", mock_open)

    snapshot = sampler.sample(
        now=1609459200.0, deadline_mono=2000.0, options={}
    )
    assert len(snapshot.entities) == 1
    entity = snapshot.entities[0]
    assert entity.metrics["psi_some_cpu"] == 2.75
    assert entity.metrics["psi_some_mem"] == 1.0
    assert entity.metrics["psi_some_io"] == 0.2


@pytest.mark.realsystem
def test_samplers_real_system():
    """[SA-04, SA-05, TS-08] Real-system smoke test: all samplers run."""
    clock = FakeClock()
    now = clock.now()
    deadline = clock.monotonic() + 30.0  # 30s budget

    # Test ProcessSampler
    proc_sampler = ProcessSampler(clock)
    proc_snap = proc_sampler.sample(now, deadline, {})
    assert proc_snap.source == "process"
    assert len(proc_snap.entities) >= 1, "Should find at least the current process"
    # Verify entity structure
    for entity in proc_snap.entities:
        assert ":" in entity.entity_id, "entity_id should contain colons"
        # Each entity should have a subset of declared metrics
        for metric_name in entity.metrics:
            assert metric_name in (
                "cpu_pct",
                "rss_bytes",
                "num_fds",
                "num_threads",
                "io_read_bytes",
                "io_write_bytes",
            )

    # Test DiskSampler
    disk_sampler = DiskSampler(clock)
    disk_snap = disk_sampler.sample(now, deadline, {})
    assert disk_snap.source == "disk"
    assert len(disk_snap.entities) >= 1, "Should find at least one mount"
    for entity in disk_snap.entities:
        # entity_id is the mount path
        assert isinstance(entity.entity_id, str)
        assert "fstype" in entity.attrs
        assert "device" in entity.attrs
        # Metrics should include at least the basic ones
        assert "total_bytes" in entity.metrics
        assert "used_bytes" in entity.metrics

    # Test SystemSampler
    sys_sampler = SystemSampler(clock)
    sys_snap = sys_sampler.sample(now, deadline, {})
    assert sys_snap.source == "system"
    assert len(sys_snap.entities) == 1
    entity = sys_snap.entities[0]
    assert entity.entity_id == "system"
    assert "hostname" in entity.attrs
    # System should have load metrics
    assert "load1" in entity.metrics
    assert "load5" in entity.metrics
    assert "load15" in entity.metrics
    assert "cpu_pct" in entity.metrics


# --- integrator regression tests (post-review fixes) ---


def test_process_midread_vanish_does_not_abort_pass(monkeypatch):
    """[PL-03] A process vanishing after create_time succeeds (name() raises)
    must skip only that entity - the rest of the pass still samples."""
    import psutil as _psutil

    def raise_gone():
        raise _psutil.NoSuchProcess(77)

    ghost = types.SimpleNamespace(pid=77, create_time=lambda: 100.0, name=raise_gone)
    ok = types.SimpleNamespace(
        pid=78,
        create_time=lambda: 200.0,
        name=lambda: "survivor",
        cmdline=lambda: ["survivor"],
        username=lambda: "u",
        exe=lambda: "/bin/survivor",
        memory_info=lambda: types.SimpleNamespace(rss=1),
        num_fds=lambda: 1,
        num_threads=lambda: 1,
        io_counters=lambda: types.SimpleNamespace(read_bytes=0, write_bytes=0),
        cpu_percent=lambda interval: 0.0,
    )
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [ghost, ok])
    snap = ProcessSampler(FakeClock()).sample(now=1000.0, deadline_mono=10_000.0, options={})
    assert [e.entity_id for e in snap.entities] == ["survivor:78:200"]


def test_process_pid_reuse_gets_fresh_cache_entry(monkeypatch):
    """[DM-02] A recycled PID with a new create_time must not reuse the old
    process's cached cpu_percent state (cache key includes create_time)."""

    def mk(pid, ct, name):
        return types.SimpleNamespace(
            pid=pid,
            create_time=lambda: ct,
            name=lambda: name,
            cmdline=lambda: [name],
            username=lambda: "u",
            exe=lambda: f"/bin/{name}",
            memory_info=lambda: types.SimpleNamespace(rss=1),
            num_fds=lambda: 1,
            num_threads=lambda: 1,
            io_counters=lambda: types.SimpleNamespace(read_bytes=0, write_bytes=0),
            cpu_percent=lambda interval: 1.0,
        )

    sampler = ProcessSampler(FakeClock())
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [mk(50, 100, "old")])
    sampler.sample(now=1.0, deadline_mono=10_000.0, options={})
    assert set(sampler._procs) == {(50, 100)}
    monkeypatch.setattr("psutil.process_iter", lambda *a, **k: [mk(50, 999, "new")])
    snap = sampler.sample(now=2.0, deadline_mono=10_000.0, options={})
    assert set(sampler._procs) == {(50, 999)}  # old entry evicted, new keyed by create_time
    assert snap.entities[0].entity_id == "new:50:999"

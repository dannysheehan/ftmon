"""Source protocols and the static source declarations (PL-01, PL-05).

The SOURCE_DECLS registry is FROZEN contract: the definitions validator
builds expression NameEnvs from it, and every Sampler/EventSource
implementation must produce exactly these metrics/attrs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol

from ftmon.model import AttrDecl, EventRecord, MetricDecl, Snapshot, SourceDecl

__all__ = ["Sampler", "EventSource", "SOURCE_DECLS", "get_decl"]


class Sampler(Protocol):
    decl: ClassVar[SourceDecl]

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        """`now` is the wall timestamp to stamp on the Snapshot (samplers must
        not read clocks themselves, TS-03); deadline is cooperative for
        in-process samplers (SA-02)."""
        ...


class EventSource(Protocol):
    decl: ClassVar[SourceDecl]

    def start(self, cursor: str | None) -> None:
        """Begin producing from `cursor` (DM-15); None = start at now, no
        historical backfill."""
        ...

    def drain(self, now: float, max_items: int) -> tuple[list[EventRecord], str | None]:
        """Up to max_items queued events (ingest order) and the cursor after
        the last one. `now` stamps ingest_ts — same deviation from the design
        signature as Sampler.sample: sources must not read clocks (TS-03)."""
        ...

    def alive(self) -> bool: ...
    def stop(self) -> None: ...


def _m(name: str, unit: str, kind: str, doc: str) -> MetricDecl:
    return MetricDecl(name=name, unit=unit, kind=kind, doc=doc)  # type: ignore[arg-type]


def _a(name: str, doc: str) -> AttrDecl:
    return AttrDecl(name=name, doc=doc)


SOURCE_DECLS: dict[str, SourceDecl] = {
    "process": SourceDecl(
        name="process",
        kind="sampler",
        entity_kind="process",
        metrics=(
            _m("cpu_pct", "%", "gauge", "CPU percent of one core since last sample"),
            _m("rss_bytes", "bytes", "gauge", "Resident set size"),
            _m("num_fds", "count", "gauge", "Open file descriptors (may be None unprivileged)"),
            _m("num_threads", "count", "gauge", "Thread count"),
            _m("io_read_bytes", "bytes", "counter", "Cumulative bytes read (may be None)"),
            _m("io_write_bytes", "bytes", "counter", "Cumulative bytes written (may be None)"),
        ),
        attrs=(
            _a("name", "Process name"),
            _a("cmdline", "Command line, truncated to 256 chars (SE-04)"),
            _a("username", "Owning user"),
            _a("exe", "Executable path"),
        ),
    ),
    "disk": SourceDecl(
        name="disk",
        kind="sampler",
        entity_kind="mount",
        metrics=(
            _m("total_bytes", "bytes", "gauge", "Filesystem size"),
            _m("used_bytes", "bytes", "gauge", "Used space"),
            _m("free_bytes", "bytes", "gauge", "Free space for unprivileged users"),
            _m("used_pct", "%", "gauge", "Percent used"),
            _m("inode_used_pct", "%", "gauge", "Percent inodes used (None where unsupported)"),
        ),
        attrs=(
            _a("fstype", "Filesystem type, e.g. ext4"),
            _a("device", "Backing device"),
        ),
    ),
    "system": SourceDecl(
        name="system",
        kind="sampler",
        entity_kind="system",
        metrics=(
            _m("load1", "load", "gauge", "1-minute load average"),
            _m("load5", "load", "gauge", "5-minute load average"),
            _m("load15", "load", "gauge", "15-minute load average"),
            _m("cpu_pct", "%", "gauge", "Total CPU percent"),
            _m("mem_total_bytes", "bytes", "gauge", "Total physical memory"),
            _m("mem_available_bytes", "bytes", "gauge", "Available memory"),
            _m("mem_used_bytes", "bytes", "gauge", "Used memory"),
            _m("swap_used_pct", "%", "gauge", "Swap used percent"),
            _m("psi_some_cpu", "%", "gauge", "PSI some cpu avg60 (None without CONFIG_PSI)"),
            _m("psi_some_mem", "%", "gauge", "PSI some memory avg60 (None without CONFIG_PSI)"),
            _m("psi_some_io", "%", "gauge", "PSI some io avg60 (None without CONFIG_PSI)"),
        ),
        attrs=(_a("hostname", "Host name"),),
    ),
    "net": SourceDecl(
        name="net",
        kind="sampler",
        entity_kind="socket",
        metrics=(
            _m("present", "bool", "gauge", "1 if the expected listener is listening (watchlist)"),
            _m("conn_total", "count", "gauge", "Total connections (totals entity)"),
            _m("conn_established", "count", "gauge", "ESTABLISHED connections"),
            _m("conn_time_wait", "count", "gauge", "TIME_WAIT connections"),
            _m("conn_listen", "count", "gauge", "Listening sockets"),
        ),
        attrs=(
            _a("proto", "tcp or udp"),
            _a("port", "Port number as string"),
        ),
    ),
    "unit": SourceDecl(
        name="unit",
        kind="sampler",
        entity_kind="service",
        metrics=(
            _m("present", "bool", "gauge", "1 if unit active / process running (watchlist)"),
            _m("restarts", "count", "counter", "Unit NRestarts / observed restarts"),
        ),
        attrs=(
            _a("unit", "systemd unit name (unit targets)"),
            _a("kind", "unit or process"),
        ),
    ),
    "self": SourceDecl(
        name="self",
        kind="sampler",
        entity_kind="daemon",
        metrics=(
            _m("cpu_pct", "%", "gauge", "Daemon CPU percent"),
            _m("rss_bytes", "bytes", "gauge", "Daemon RSS"),
            _m("db_bytes", "bytes", "gauge", "Database file size"),
            _m("cycle_s", "s", "gauge", "Last full tick duration"),
            _m("tick_overruns", "count", "counter", "Cycles skipped due to overrun (SA-01)"),
            _m("event_queue_depth", "count", "gauge", "Queued undrained events"),
            _m("events_dropped", "count", "counter", "Events dropped on overflow (SA-08)"),
            _m("events_unstored", "count", "counter", "Events excluded by store-filter (DM-09)"),
            _m("ring_mem_bytes", "bytes", "gauge", "Ring buffer memory (CA-04)"),
            _m("source_activity_age_s", "s", "gauge", "Seconds since event reader produced data"),
            _m("eval_unknown_total", "count", "counter", "Rule evaluations returning unknown"),
            _m("samples_rejected", "count", "counter", "NaN/inf samples rejected (DM-01)"),
            _m("sqlite_lock_errors", "count", "counter",
               "Tick commits dropped after database lock timeout (PM-10)"),
            _m("external_checks_skipped", "count", "counter",
               "External aliases skipped when their source budget expired"),
            _m("external_check_failures", "count", "counter",
               "External execution/protocol failures across stable categories"),
            _m("external_perfdata_rejected", "count", "counter",
               "Declared external performance values rejected at projection"),
        ),
        attrs=(),
    ),
    # Mapped metrics are deliberately absent here. The definition validator
    # composes them from administrator-authored EC-04 mappings before building
    # its NameEnv, so plugin output can never grow the metric namespace.
    "external": SourceDecl(
        name="external",
        kind="sampler",
        entity_kind="external",
        metrics=(
            _m("plugin_state", "state", "gauge", "Plugin state 0..3"),
            _m("plugin_ok", "bool", "gauge", "1 only for plugin state OK"),
            _m("duration_s", "seconds", "gauge", "Check execution duration"),
        ),
        attrs=(_a("plugin_message", "Sanitized first-line check message"),),
    ),
    "events": SourceDecl(
        name="events",
        kind="events",
        entity_kind="episode",
        metrics=(
            # numeric namespace for event rules (EX-02): severity compares
            # against the info/notice/warning/error/critical constants
            _m("severity", "level", "gauge", "Normalized severity 0-4 (DM-08)"),
        ),
        attrs=(
            _a("provider", "Producer: unit / syslog identifier / EventLog provider"),
            _a("event_id", "Platform event id if any (string, PL-02)"),
            _a("message", "Event message, truncated 2KB (DM-13)"),
            _a("source", "journald | eventlog | oslog | file | self"),
        ),
    ),
}


def get_decl(name: str) -> SourceDecl | None:
    return SOURCE_DECLS.get(name)

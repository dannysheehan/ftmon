"""Per-cycle external alias scheduling and definition-specific projection."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import ClassVar, Protocol

from ftmon.checks.model import CheckSpec, RawCheckResult
from ftmon.checks.registry import CheckRegistry
from ftmon.clock import Clock, SystemClock
from ftmon.definitions.loader import MonitorDef
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


class Runner(Protocol):
    def run(self, spec: CheckSpec, deadline_mono: float) -> RawCheckResult: ...


class ExternalSampler:
    """Run each due alias once, then project its immutable result per monitor.

    ``prepare`` is deliberately separate from ``sample``: the scheduler knows
    the complete due-monitor set, while the ordinary pipeline asks for one
    monitor at a time. This boundary is what permits both fair alias ordering
    and definition-specific metric declarations.
    """

    decl: ClassVar[SourceDecl] = SOURCE_DECLS["external"]

    def __init__(
        self,
        registry: CheckRegistry,
        runner: Runner,
        counter: Callable[[str], None],
        clock: Clock | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._counter = counter
        self._clock = clock or SystemClock()
        self._next_alias: str | None = None
        self._results: dict[str, RawCheckResult] = {}

    def set_registry(self, registry: CheckRegistry) -> None:
        """Swap an already validated registry at a cycle boundary."""
        self._registry = registry
        if self._next_alias not in registry:
            self._next_alias = None

    def prepare(self, monitors: Iterable[MonitorDef], deadline_mono: float) -> None:
        """Execute unique aliases for one cycle within the shared deadline."""
        aliases = list(
            dict.fromkeys(
                monitor.source_options["check"]
                for monitor in monitors
                if monitor.source == "external"
                and monitor.source_options.get("check") in self._registry
            )
        )
        self._results = {}
        if not aliases:
            self._next_alias = None
            return

        start = aliases.index(self._next_alias) if self._next_alias in aliases else 0
        ordered = aliases[start:] + aliases[:start]
        for index, alias in enumerate(ordered):
            if self._clock.monotonic() >= deadline_mono:
                # Leave the first unstarted alias at the head next cycle; slow
                # aliases therefore cannot permanently starve later entries.
                self._next_alias = alias
                for _ in ordered[index:]:
                    self._counter("external_checks_skipped")
                return
            result = self._runner.run(self._registry[alias], deadline_mono)
            self._results[alias] = result
            if result.failure is not None:
                self._counter(f"external_check_failures:{result.failure}")

        self._next_alias = ordered[0]

    def project(self, monitor: MonitorDef, now: float) -> Snapshot:
        """Project a cached raw result through one monitor's declared mappings."""
        options = monitor.source_options
        alias = options["check"]
        raw = self._results.get(alias)
        if raw is None:
            # A budget skip is absence, not synthetic UNKNOWN evidence: an
            # absent entity cannot falsely clear or alter an incident.
            return Snapshot(source="external", ts=now, entities=())

        return self._project_options(options, raw, now)

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        """Sampler-compatible projection after ``prepare`` has run."""
        # Kept mapping-only so future pipeline wiring does not need to retain a
        # MonitorDef just to consume the cycle cache.
        raw = self._results.get(options["check"])
        if raw is None:
            return Snapshot(source="external", ts=now, entities=())
        return self._project_options(options, raw, now)

    def _project_options(self, options: Mapping, raw: RawCheckResult, now: float) -> Snapshot:
        metrics = {
            "plugin_state": float(raw.state),
            "plugin_ok": float(raw.state == 0),
            "duration_s": raw.duration_s,
        }
        for mapping in options.get("perfdata", ()):
            source_value = raw.values.get(mapping["label"])
            if source_value is None:
                continue
            value, uom = source_value
            if uom != mapping["plugin_uom"]:
                self._counter("external_perfdata_rejected:uom")
                continue
            scaled = value * mapping.get("scale", 1.0)
            if not math.isfinite(value) or not math.isfinite(scaled):
                self._counter("external_perfdata_rejected:non_finite")
                continue
            metrics[mapping["metric"]] = scaled
        entity = EntitySample(
            entity_id=options["entity"],
            attrs={"plugin_message": raw.message},
            metrics=metrics,
        )
        return Snapshot(source="external", ts=now, entities=(entity,))

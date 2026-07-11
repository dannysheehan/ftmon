"""Bounded external-check execution and strict protocol adapters."""

from ftmon.checks.model import CheckSpec, RawCheckResult
from ftmon.checks.runner import CheckRunner
from ftmon.checks.sampler import ExternalSampler

__all__ = ["CheckRunner", "CheckSpec", "ExternalSampler", "RawCheckResult"]

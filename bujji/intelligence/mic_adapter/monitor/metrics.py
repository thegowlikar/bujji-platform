"""Observation Metrics — BUJJI Options OS, Integration Series 2, Sprint 2.

Tracks OPERATIONAL metrics about the Intelligence Adapter's own
behaviour only -- load attempts/successes/failures, latency,
missing/stale snapshot events. Never inspects trading logic, broker
state, positions, or PnL: no such field exists anywhere in this module,
structurally, not merely by convention.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservationProvenance:
    source: str              # "bujji_intelligence_adapter" -- always this; never a broker or execution source.
    ruleset_version: str


@dataclass(frozen=True)
class ObservationMetrics:
    """A point-in-time, immutable snapshot of the monitor's own running
    counters -- returned by `latest_metrics()`, never mutated once
    constructed."""

    load_attempts: int
    load_successes: int
    load_failures: int
    missing_snapshot_count: int
    stale_snapshot_count: int
    last_latency_seconds: float | None
    mean_latency_seconds: float | None
    provenance: ObservationProvenance


class ObservationMetricsTracker:
    """A plain, append-only recorder -- every `record_*` method only
    updates this instance's own counters; it never calls back into the
    adapter, the orchestrator, or any decision-making code."""

    def __init__(self) -> None:
        self._load_attempts = 0
        self._load_successes = 0
        self._load_failures = 0
        self._missing_snapshot_count = 0
        self._stale_snapshot_count = 0
        self._latencies: list[float] = []

    def record_attempt(self) -> None:
        self._load_attempts += 1

    def record_success(self, latency_seconds: float) -> None:
        self._load_successes += 1
        self._latencies.append(latency_seconds)

    def record_failure(self, latency_seconds: float | None = None) -> None:
        self._load_failures += 1
        if latency_seconds is not None:
            self._latencies.append(latency_seconds)

    def record_missing(self) -> None:
        self._missing_snapshot_count += 1

    def record_stale(self) -> None:
        self._stale_snapshot_count += 1

    def snapshot(self, ruleset_version: str) -> ObservationMetrics:
        last_latency = self._latencies[-1] if self._latencies else None
        mean_latency = sum(self._latencies) / len(self._latencies) if self._latencies else None
        return ObservationMetrics(
            load_attempts=self._load_attempts,
            load_successes=self._load_successes,
            load_failures=self._load_failures,
            missing_snapshot_count=self._missing_snapshot_count,
            stale_snapshot_count=self._stale_snapshot_count,
            last_latency_seconds=last_latency,
            mean_latency_seconds=mean_latency,
            provenance=ObservationProvenance(source="bujji_intelligence_adapter", ruleset_version=ruleset_version),
        )

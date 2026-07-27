"""Observation Monitor — BUJJI Options OS, Integration Series 2, Sprint 2.

Wraps the Intelligence Adapter (Sprint 1, unmodified) to continuously
measure its own operational health -- never the trading logic, broker
state, positions, or PnL it might someday be asked about by something
else entirely. `observe()` is the single method the Decision Pipeline
calls; its return value is recorded to the Observation Journal and MUST
NEVER be read by any decision-making code.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Callable, Optional

from ..adapter import IntelligenceAdapter
from .config import ObservationMonitorConfig
from .health import ObservationHealth, classify_health
from .metrics import ObservationMetrics, ObservationMetricsTracker, ObservationProvenance

MONITOR_VERSION = "1.0.0"


def _health_id(timestamp: datetime, status: str) -> str:
    basis = f"{timestamp.isoformat()}|{status}"
    return "OBSHEALTH-" + hashlib.md5(basis.encode()).hexdigest()[:16]


class ObservationMonitor:
    def __init__(
        self,
        adapter: IntelligenceAdapter,
        config: Optional[ObservationMonitorConfig] = None,
        clock: Callable[[], datetime] = datetime.now,
        latency_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._adapter = adapter
        self._config = config or ObservationMonitorConfig()
        self._clock = clock
        self._latency_clock = latency_clock
        self._metrics = ObservationMetricsTracker()
        self._history: list[ObservationHealth] = []

    def observe(self, feature_flag_enabled: bool) -> ObservationHealth:
        """Loads the latest snapshot through the (unmodified) adapter,
        measures latency/age, classifies health, and records the result.
        Never raises -- any adapter exception is caught and classified
        as UNAVAILABLE, exactly like the orchestrator's own best-effort
        philosophy for this observation point.
        """
        if not feature_flag_enabled:
            health = self._build_health(False, load_succeeded=True, snapshot_age_seconds=None,
                                        latency_seconds=None, snapshot_found=False)
            self._history.append(health)
            return health

        self._metrics.record_attempt()
        start = self._latency_clock()
        try:
            snapshot = self._adapter.load_latest_snapshot()
            latency = self._latency_clock() - start
        except Exception:  # noqa: BLE001 - observational only, must never propagate.
            latency = self._latency_clock() - start
            self._metrics.record_failure(latency)
            health = self._build_health(True, load_succeeded=False, snapshot_age_seconds=None,
                                        latency_seconds=latency, snapshot_found=False)
            self._history.append(health)
            return health

        if snapshot is None:
            self._metrics.record_failure(latency)
            self._metrics.record_missing()
            health = self._build_health(True, load_succeeded=True, snapshot_age_seconds=None,
                                        latency_seconds=latency, snapshot_found=False)
            self._history.append(health)
            return health

        self._metrics.record_success(latency)
        age_seconds = (self._clock() - snapshot.timestamp).total_seconds()
        if age_seconds > self._config.stale_after_seconds:
            self._metrics.record_stale()

        health = self._build_health(True, load_succeeded=True, snapshot_age_seconds=age_seconds,
                                    latency_seconds=latency, snapshot_found=True)
        self._history.append(health)
        return health

    def _build_health(self, feature_flag_enabled, load_succeeded, snapshot_age_seconds,
                      latency_seconds, snapshot_found) -> ObservationHealth:
        status, reason = classify_health(
            feature_flag_enabled, load_succeeded, snapshot_found, snapshot_age_seconds, latency_seconds,
            self._config.stale_after_seconds, self._config.degraded_latency_seconds,
        )
        timestamp = self._clock()
        return ObservationHealth(
            health_id=_health_id(timestamp, status),
            timestamp=timestamp,
            health_status=status,
            feature_flag_enabled=feature_flag_enabled,
            snapshot_age_seconds=snapshot_age_seconds,
            latency_seconds=latency_seconds,
            reason=reason,
            provenance=ObservationProvenance(source="bujji_intelligence_adapter", ruleset_version=MONITOR_VERSION),
        )

    # ------------------------------------------------------------------ #
    # Query API -- deterministic, read-only, no mutation.
    # ------------------------------------------------------------------ #
    def latest_metrics(self) -> ObservationMetrics:
        return self._metrics.snapshot(MONITOR_VERSION)

    def latest_health(self) -> Optional[ObservationHealth]:
        return self._history[-1] if self._history else None

    def health_history(self) -> tuple[ObservationHealth, ...]:
        return tuple(self._history)

    def availability_summary(self) -> dict:
        by_status: dict[str, int] = {}
        for h in self._history:
            by_status[h.health_status] = by_status.get(h.health_status, 0) + 1
        return {"total_observations": len(self._history), "by_status": by_status}

    def adapter_statistics(self) -> dict:
        metrics = self.latest_metrics()
        return {
            "load_attempts": metrics.load_attempts,
            "load_successes": metrics.load_successes,
            "load_failures": metrics.load_failures,
            "missing_snapshot_count": metrics.missing_snapshot_count,
            "stale_snapshot_count": metrics.stale_snapshot_count,
            "mean_latency_seconds": metrics.mean_latency_seconds,
        }

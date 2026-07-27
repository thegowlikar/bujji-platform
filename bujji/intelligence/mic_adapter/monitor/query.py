"""Observation Monitor Query API — BUJJI Options OS, Integration Series
2, Sprint 2.

Deterministic, READ-ONLY functions over an already-populated
`ObservationMonitor`. No function in this module accepts a mutation --
there is no delete, no update, no overwrite anywhere on this surface.
Every function only ever reads the monitor's own accessors, never
writes to them.
"""
from __future__ import annotations

from typing import Optional

from .health import ObservationHealth
from .metrics import ObservationMetrics
from .monitor import ObservationMonitor


def latest_metrics(monitor: ObservationMonitor) -> ObservationMetrics:
    return monitor.latest_metrics()


def latest_health(monitor: ObservationMonitor) -> Optional[ObservationHealth]:
    return monitor.latest_health()


def health_history(monitor: ObservationMonitor) -> tuple[ObservationHealth, ...]:
    return monitor.health_history()


def availability_summary(monitor: ObservationMonitor) -> dict:
    return monitor.availability_summary()


def adapter_statistics(monitor: ObservationMonitor) -> dict:
    return monitor.adapter_statistics()

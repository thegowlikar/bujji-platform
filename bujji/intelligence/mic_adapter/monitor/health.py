"""Observation Health — BUJJI Options OS, Integration Series 2, Sprint 2.

A finite health taxonomy for the Intelligence Adapter's own operational
state, plus the pure derivation logic that classifies one observation
cycle into a health status. Never inspects trading logic, broker state,
positions, or PnL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .metrics import ObservationProvenance

HEALTH_TAXONOMY_VERSION = "1.0.0"

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

OBSERVATION_HEALTH_STATES = (HEALTHY, DEGRADED, STALE, UNAVAILABLE, UNKNOWN)


@dataclass(frozen=True)
class ObservationHealth:
    """One observation cycle's health classification -- immutable,
    never mutated after construction."""

    health_id: str
    timestamp: datetime
    health_status: str            # One of the finite taxonomy above.
    feature_flag_enabled: bool
    snapshot_age_seconds: Optional[float]
    latency_seconds: Optional[float]
    reason: str
    provenance: ObservationProvenance


def classify_health(
    feature_flag_enabled: bool,
    load_succeeded: bool,
    snapshot_found: bool,
    snapshot_age_seconds: Optional[float],
    latency_seconds: Optional[float],
    stale_after_seconds: float,
    degraded_latency_seconds: float,
) -> tuple[str, str]:
    """Pure classification -- no I/O, no side effects. Returns
    (health_status, reason)."""
    if not feature_flag_enabled:
        return UNKNOWN, "adapter_disabled"

    if not load_succeeded:
        return UNAVAILABLE, "adapter_load_exception"

    if not snapshot_found:
        return UNAVAILABLE, "no_snapshot_available"

    if snapshot_age_seconds is not None and snapshot_age_seconds > stale_after_seconds:
        return STALE, f"snapshot_age_seconds={snapshot_age_seconds:.1f} exceeds threshold {stale_after_seconds:.1f}"

    if latency_seconds is not None and latency_seconds > degraded_latency_seconds:
        return DEGRADED, f"latency_seconds={latency_seconds:.3f} exceeds threshold {degraded_latency_seconds:.3f}"

    return HEALTHY, "nominal"

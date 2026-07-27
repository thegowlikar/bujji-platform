"""Observation Monitor — Configuration — BUJJI Options OS, Integration
Series 2, Sprint 2.

A finite, statically-typed configuration object -- no dynamic loading,
no environment-variable magic. Carries no credential, no broker
endpoint, no order-routing detail of any kind.
"""
from __future__ import annotations

from dataclasses import dataclass

OBSERVATION_CONFIG_VERSION = "1.0.0"


@dataclass(frozen=True)
class ObservationMonitorConfig:
    # A snapshot older than this (against the monitor's own clock, naive
    # datetimes throughout -- matching MIC v2's Consumer API convention)
    # is STALE. Default is deliberately generous (7 days) since MIC v2
    # qualification campaigns are run periodically, not every trading
    # session -- this is an operational health threshold, not a
    # correctness requirement.
    stale_after_seconds: float = 7 * 24 * 3600.0
    # An adapter call slower than this is DEGRADED, even if it
    # eventually succeeds.
    degraded_latency_seconds: float = 0.5


DEFAULT_OBSERVATION_CONFIG = ObservationMonitorConfig()

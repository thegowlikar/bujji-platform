"""Live Observation Producer Framework configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import INTERVAL_ONE_MINUTE, LIVE_OBSERVATION_VERSION

SCHEMA_VERSION = LIVE_OBSERVATION_VERSION

DEFAULT_AGGREGATION_INTERVAL = INTERVAL_ONE_MINUTE

DEFAULT_SOURCE = "FYERS"


@dataclass(frozen=True)
class LiveObservationConfig:
    schema_version: str = SCHEMA_VERSION
    default_aggregation_interval: str = DEFAULT_AGGREGATION_INTERVAL
    default_source: str = DEFAULT_SOURCE

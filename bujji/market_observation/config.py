"""Market Observation Contract configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import MARKET_OBSERVATION_VERSION, RESOLUTION_DAILY

SCHEMA_VERSION = MARKET_OBSERVATION_VERSION

DEFAULT_RESOLUTION = RESOLUTION_DAILY


@dataclass(frozen=True)
class MarketObservationConfig:
    schema_version: str = SCHEMA_VERSION
    default_resolution: str = DEFAULT_RESOLUTION

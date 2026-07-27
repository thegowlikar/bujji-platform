"""Options Observation Domain configuration."""
from __future__ import annotations

from dataclasses import dataclass

from bujji.market_observation.taxonomy import RESOLUTION_DAILY

from .taxonomy import OPTIONS_OBSERVATION_VERSION

SCHEMA_VERSION = OPTIONS_OBSERVATION_VERSION

DEFAULT_RESOLUTION = RESOLUTION_DAILY

# The source label recorded on every Observation built by this
# domain's runner.py, per MOC's ObservationIdentity.source /
# ObservationProvenance.originating_source. Matches
# `bujji/futures_observation/config.py`'s convention.
DEFAULT_SOURCE = "NSE_BHAVCOPY_FO"


@dataclass(frozen=True)
class OptionsObservationConfig:
    schema_version: str = SCHEMA_VERSION
    default_resolution: str = DEFAULT_RESOLUTION
    default_source: str = DEFAULT_SOURCE

"""Market Observation Contract runner — public composition entrypoints.

This is the API surface other modules should import: `build_observation`,
`append_to_series`, `validate_observation`. Never re-implements
engine.py's logic -- only composes it, optionally with journaling.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

from . import config as _config
from . import engine
from .models import Observation, ObservationSeries, ValidationResult


def build_observation(
    observation_type: str,
    instrument: str,
    exchange: str,
    segment: str,
    timestamp: str,
    resolution: str,
    source: str,
    value_kind: str,
    payload: Any,
    completeness: float,
    freshness: float,
    confidence: Optional[float],
    missing_fields: Tuple[str, ...],
    validation_status: str,
    source_quality: str,
    originating_source: str,
    acquisition_timestamp: str,
    normalization_timestamp: str,
    origin: str,
    transformation_history: Tuple[str, ...] = (),
    schema_version: str = _config.SCHEMA_VERSION,
    journal=None,
) -> Observation:
    """Build an Observation from already-normalized inputs, optionally
    journaling it. `journal`, if supplied, must expose a
    `.record_observation(observation)` method (see journal.py).
    """
    observation = engine.build_observation(
        observation_type=observation_type,
        instrument=instrument,
        exchange=exchange,
        segment=segment,
        timestamp=timestamp,
        resolution=resolution,
        source=source,
        schema_version=schema_version,
        value_kind=value_kind,
        payload=payload,
        completeness=completeness,
        freshness=freshness,
        confidence=confidence,
        missing_fields=missing_fields,
        validation_status=validation_status,
        source_quality=source_quality,
        originating_source=originating_source,
        acquisition_timestamp=acquisition_timestamp,
        normalization_timestamp=normalization_timestamp,
        origin=origin,
        provenance_version=schema_version,
        transformation_history=transformation_history,
    )

    if journal is not None:
        journal.record_observation(observation)

    return observation


def append_to_series(series: ObservationSeries, observation: Observation, journal=None) -> ObservationSeries:
    """Append an Observation to an ObservationSeries, optionally
    journaling the resulting series. `journal`, if supplied, must
    expose a `.record_series(series)` method.
    """
    new_series = engine.append_observation(series, observation)

    if journal is not None:
        journal.record_series(new_series)

    return new_series


def validate_observation(observation: Observation, schema_version: str = _config.SCHEMA_VERSION) -> ValidationResult:
    """Run structural validation on an Observation. See
    engine.validate_observation for exactly what is (and is not) checked.
    """
    return engine.validate_observation(observation, expected_schema_version=schema_version)

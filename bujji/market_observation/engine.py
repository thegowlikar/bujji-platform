"""Market Observation Contract engine — pure functions, no state, no IO.

Implements MOF v1's Deliverable 4 (Observation Lifecycle) stages
"Observation" (construct from already-normalized input -- this module
never parses a raw feed) and "Validation" (structural checks only,
never a check on the market value itself -- per MOF's own boundary,
whether a price is "reasonable" is Derived Evidence/Intelligence work,
not Observation work).

Nothing here authenticates, connects to a broker, calls a network API,
or reads a raw feed file. Every function takes already-normalized
values and returns a new frozen object; nothing is mutated in place.
"""
from __future__ import annotations

import hashlib
from typing import Any, List, Optional, Tuple

from . import taxonomy
from .models import (
    Observation,
    ObservationIdentity,
    ObservationProvenance,
    ObservationQualityMetadata,
    ObservationSeries,
    ObservationValue,
    SeriesGap,
    ValidationResult,
)

# Resolution -> nominal interval in seconds. Used only for gap detection
# arithmetic; TICK and EVENT have no nominal interval and are excluded
# (their series are gap-checked only for strict ordering, never gap
# duration -- per MOF Deliverable 5, tick/event data has no defined
# time-series resolution model).
_RESOLUTION_SECONDS = {
    taxonomy.RESOLUTION_ONE_MINUTE: 60,
    taxonomy.RESOLUTION_FIVE_MINUTE: 300,
    taxonomy.RESOLUTION_FIFTEEN_MINUTE: 900,
    taxonomy.RESOLUTION_HOURLY: 3600,
    taxonomy.RESOLUTION_DAILY: 86400,
    taxonomy.RESOLUTION_WEEKLY: 604800,
}

# Tolerate this multiple of the nominal interval before a missing
# interval is flagged as a gap -- deliberately mirroring the tolerance
# discipline already established in bujji/core/market_observation.py's
# candle-admission classifier, applied here at the series-storage layer.
_GAP_TOLERANCE_MULTIPLIER = 1.5


def _observation_id(identity_fields: Tuple[str, ...], value: ObservationValue) -> str:
    """Deterministic content-identity hash over Identity fields (minus
    the id itself) plus Value. Never uuid4(), never wall-clock. Quality
    metadata and provenance are deliberately excluded -- see models.py's
    module docstring for the design rationale.
    """
    seed = "|".join(identity_fields) + "|" + value.value_kind + "|" + repr(value.payload)
    return "OBS-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def build_observation(
    observation_type: str,
    instrument: str,
    exchange: str,
    segment: str,
    timestamp: str,
    resolution: str,
    source: str,
    schema_version: str,
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
    provenance_version: str,
    transformation_history: Tuple[str, ...] = (),
) -> Observation:
    """Construct an Observation from already-normalized inputs.

    This is the ONLY place an observation_id is minted. It never
    parses a raw feed, never infers a missing field, and never
    fabricates a value -- every argument must already be normalized
    (per MOF Deliverable 4's Normalization stage) by the caller.
    """
    value = ObservationValue(value_kind=value_kind, payload=payload)

    identity_seed_fields = (
        observation_type,
        instrument,
        exchange,
        segment,
        timestamp,
        resolution,
        source,
        schema_version,
    )
    observation_id = _observation_id(identity_seed_fields, value)

    identity = ObservationIdentity(
        observation_id=observation_id,
        observation_type=observation_type,
        instrument=instrument,
        exchange=exchange,
        segment=segment,
        timestamp=timestamp,
        resolution=resolution,
        source=source,
        schema_version=schema_version,
    )

    quality = ObservationQualityMetadata(
        completeness=completeness,
        freshness=freshness,
        confidence=confidence,
        missing_fields=tuple(missing_fields),
        validation_status=validation_status,
        source_quality=source_quality,
    )

    provenance = ObservationProvenance(
        originating_source=originating_source,
        acquisition_timestamp=acquisition_timestamp,
        normalization_timestamp=normalization_timestamp,
        origin=origin,
        version=provenance_version,
        transformation_history=tuple(transformation_history),
    )

    return Observation(identity=identity, quality=quality, provenance=provenance, value=value)


def validate_observation(observation: Observation, expected_schema_version: str) -> ValidationResult:
    """Structural validation only -- identity fields present/well-formed,
    timestamp parseable, source non-empty, schema_version compatible,
    quality metadata internally consistent, per MOF Deliverable 4's
    Validation stage. Never validates the market value itself (that a
    price is "reasonable" is Derived Evidence/Intelligence work, out of
    MOC's scope).

    All checks run unconditionally -- never short-circuiting on the
    first failure -- so `reasons` is always complete.
    """
    reasons: List[str] = []
    identity = observation.identity
    quality = observation.quality

    if not identity.observation_id:
        reasons.append("MISSING_OBSERVATION_ID")
    if identity.observation_type not in taxonomy.ALL_OBSERVATION_TYPES:
        reasons.append("UNKNOWN_OBSERVATION_TYPE")
    if not identity.instrument:
        reasons.append("MISSING_INSTRUMENT")
    if not identity.exchange:
        reasons.append("MISSING_EXCHANGE")
    if not identity.segment:
        reasons.append("MISSING_SEGMENT")
    if not identity.source:
        reasons.append("MISSING_SOURCE")
    if identity.resolution not in taxonomy.ALL_RESOLUTIONS:
        reasons.append("UNKNOWN_RESOLUTION")

    if not _is_well_formed_timestamp(identity.timestamp):
        reasons.append("MALFORMED_TIMESTAMP")

    if identity.schema_version != expected_schema_version:
        reasons.append("SCHEMA_VERSION_MISMATCH")

    if not (0.0 <= quality.completeness <= 1.0):
        reasons.append("COMPLETENESS_OUT_OF_RANGE")
    if quality.freshness < 0.0:
        reasons.append("NEGATIVE_FRESHNESS")
    if quality.confidence is not None and not (0.0 <= quality.confidence <= 1.0):
        reasons.append("CONFIDENCE_OUT_OF_RANGE")
    if quality.validation_status not in taxonomy.ALL_VALIDATION_STATUSES:
        reasons.append("UNKNOWN_VALIDATION_STATUS")
    if quality.source_quality not in taxonomy.ALL_SOURCE_QUALITIES:
        reasons.append("UNKNOWN_SOURCE_QUALITY")

    if observation.provenance.origin not in taxonomy.ALL_ORIGINS:
        reasons.append("UNKNOWN_ORIGIN")

    if observation.value.value_kind not in taxonomy.ALL_VALUE_KINDS:
        reasons.append("UNKNOWN_VALUE_KIND")

    if reasons:
        return ValidationResult(is_valid=False, status=taxonomy.VALIDATION_INVALID, reasons=tuple(reasons))
    return ValidationResult(is_valid=True, status=taxonomy.VALIDATION_VALID, reasons=())


def _is_well_formed_timestamp(timestamp: str) -> bool:
    if not timestamp:
        return False
    try:
        # ISO-8601 is this project's established timestamp convention
        # (see RuntimeAuthorization.timestamp, clock().isoformat()).
        from datetime import datetime

        datetime.fromisoformat(timestamp)
        return True
    except (ValueError, TypeError):
        return False


def validate_series_ordering(series: ObservationSeries) -> ValidationResult:
    """Series ordering must be strictly monotonic non-decreasing by
    timestamp. Never validates values -- ordering only.
    """
    timestamps = [o.identity.timestamp for o in series.observations]
    reasons: List[str] = []
    for earlier, later in zip(timestamps, timestamps[1:]):
        if later < earlier:
            reasons.append(f"OUT_OF_ORDER: {later!r} follows {earlier!r}")
    if reasons:
        return ValidationResult(is_valid=False, status=taxonomy.VALIDATION_INVALID, reasons=tuple(reasons))
    return ValidationResult(is_valid=True, status=taxonomy.VALIDATION_VALID, reasons=())


def detect_gaps(observations: Tuple[Observation, ...], resolution: str) -> Tuple[SeriesGap, ...]:
    """Detect missing intervals between consecutive Observations in an
    already-ordered sequence. TICK/EVENT resolutions have no nominal
    interval and therefore never produce MISSING_INTERVAL gaps (per
    MOF Deliverable 5 -- tick/event data has no resolution model to
    measure a gap against).
    """
    nominal = _RESOLUTION_SECONDS.get(resolution)
    if nominal is None or len(observations) < 2:
        return ()

    from datetime import datetime

    gaps: List[SeriesGap] = []
    for earlier, later in zip(observations, observations[1:]):
        try:
            t1 = datetime.fromisoformat(earlier.identity.timestamp)
            t2 = datetime.fromisoformat(later.identity.timestamp)
        except (ValueError, TypeError):
            continue
        actual = (t2 - t1).total_seconds()
        if actual > nominal * _GAP_TOLERANCE_MULTIPLIER:
            gaps.append(
                SeriesGap(
                    after_timestamp=earlier.identity.timestamp,
                    before_timestamp=later.identity.timestamp,
                    reason=taxonomy.GAP_REASON_MISSING_INTERVAL,
                )
            )
    return tuple(gaps)


def append_observation(series: ObservationSeries, observation: Observation) -> ObservationSeries:
    """Append an Observation to an ObservationSeries, preserving
    ordering and continuity. Pure -- returns a new ObservationSeries,
    never mutates the input.

    Behavior for out-of-order append (defined explicitly, per this
    project's "no silent data gaps"/"never fabricate" discipline): the
    Observation is still appended (an ObservationSeries is a record of
    what was received, in receipt order, not a value-sorted index) but
    an OUT_OF_ORDER_SKIPPED gap marker is recorded immediately, making
    the disorder explicit and queryable rather than silently absorbed.
    Callers that require strict monotonic timestamp order should call
    `validate_series_ordering` after appending and reject/handle the
    result themselves -- this function never raises or silently drops
    data.
    """
    if observation.identity.observation_type != series.observation_type:
        raise ValueError(
            f"observation_type mismatch: series is {series.observation_type!r}, "
            f"observation is {observation.identity.observation_type!r}"
        )
    if observation.identity.instrument != series.instrument:
        raise ValueError(
            f"instrument mismatch: series is {series.instrument!r}, "
            f"observation is {observation.identity.instrument!r}"
        )

    new_observations = series.observations + (observation,)

    new_gaps = list(series.gaps)
    if series.observations:
        last = series.observations[-1]
        if observation.identity.timestamp < last.identity.timestamp:
            new_gaps.append(
                SeriesGap(
                    after_timestamp=last.identity.timestamp,
                    before_timestamp=observation.identity.timestamp,
                    reason=taxonomy.GAP_REASON_OUT_OF_ORDER_SKIPPED,
                )
            )
        else:
            interval_gaps = detect_gaps((last, observation), series.resolution)
            new_gaps.extend(interval_gaps)

    window_start = series.window_start or observation.identity.timestamp
    window_end = observation.identity.timestamp
    if series.observations and series.window_end > window_end:
        window_end = series.window_end
    if series.observations and series.window_start:
        window_start = series.window_start

    return ObservationSeries(
        observation_type=series.observation_type,
        instrument=series.instrument,
        resolution=series.resolution,
        window_start=window_start,
        window_end=window_end,
        observations=new_observations,
        gaps=tuple(new_gaps),
    )


def new_series(observation_type: str, instrument: str, resolution: str) -> ObservationSeries:
    """Construct an empty ObservationSeries ready to receive
    Observations via append_observation."""
    return ObservationSeries(
        observation_type=observation_type,
        instrument=instrument,
        resolution=resolution,
        window_start="",
        window_end="",
        observations=(),
        gaps=(),
    )

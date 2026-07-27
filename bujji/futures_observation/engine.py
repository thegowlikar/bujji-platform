"""Futures Observation Domain engine — pure functions, no state, no IO.

Builds `FuturesObservation` from already-normalized inputs (a single
already-parsed Bhavcopy futures row's fields, as plain values) via MOC's
own `bujji.market_observation.engine.build_observation` /
`bujji.market_observation.runner.build_observation` -- this module never
mints an `observation_id` itself and never re-implements MOC's identity
hashing (per `docs/MARKET_OBSERVATION_CONTRACT.md` Extension rule 3:
only `engine.build_observation` may mint an observation_id).

Nothing here reads a CSV, opens a file, or touches the network -- raw
Bhavcopy parsing lives in runner.py; this module only ever receives
already-normalized scalars.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import ValidationResult

from . import taxonomy
from .config import DEFAULT_SOURCE, SCHEMA_VERSION
from .models import FuturesObservation, FuturesObservationSeries


def compute_basis(settlement_price: Optional[float], underlying_price: Optional[float]) -> Optional[float]:
    """BASIS = SETTLEMENT_PRICE - UNDERLYING_PRICE, both already-raw
    fields from the same Bhavcopy row. Purely arithmetic -- never a
    premium/discount *classification* (that framing is MSI's
    interpretive job, out of scope here). Returns None whenever either
    input is None, never defaults to 0.0.
    """
    if settlement_price is None or underlying_price is None:
        return None
    return settlement_price - underlying_price


def build_futures_observation(
    underlying: str,
    instrument_symbol: str,
    expiry: str,
    exchange: str,
    segment: str,
    timestamp: str,
    resolution: str,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    volume: Optional[float],
    open_interest: Optional[float],
    change_in_open_interest: Optional[float],
    settlement_price: Optional[float],
    underlying_price: Optional[float],
    origin: str,
    acquisition_timestamp: str,
    normalization_timestamp: str,
    source: str = DEFAULT_SOURCE,
    schema_version: str = SCHEMA_VERSION,
    source_quality: str = moc_taxonomy.SOURCE_QUALITY_HIGH,
    confidence: Optional[float] = None,
) -> FuturesObservation:
    """Construct one FuturesObservation from already-normalized inputs
    (one Bhavcopy futures row's fields, as plain values). Any of the
    OHLC/volume/OI/settlement fields may genuinely be None -- these are
    recorded as disclosed gaps in `missing_fields`, never fabricated.
    """
    field_values: Dict[str, Optional[float]] = {
        taxonomy.FIELD_OPEN: open_,
        taxonomy.FIELD_HIGH: high,
        taxonomy.FIELD_LOW: low,
        taxonomy.FIELD_CLOSE: close,
        taxonomy.FIELD_VOLUME: volume,
        taxonomy.FIELD_OPEN_INTEREST: open_interest,
        taxonomy.FIELD_CHANGE_IN_OPEN_INTEREST: change_in_open_interest,
        taxonomy.FIELD_SETTLEMENT_PRICE: settlement_price,
    }
    basis = compute_basis(settlement_price, underlying_price)
    field_values[taxonomy.FIELD_BASIS] = basis

    missing_fields = tuple(
        f for f in taxonomy.MANDATORY_FUTURES_OBSERVATION_FIELDS if field_values.get(f) is None
    )
    present_mandatory = len(taxonomy.MANDATORY_FUTURES_OBSERVATION_FIELDS) - len(missing_fields)
    completeness = present_mandatory / len(taxonomy.MANDATORY_FUTURES_OBSERVATION_FIELDS)
    validation_status = (
        moc_taxonomy.VALIDATION_VALID if not missing_fields else moc_taxonomy.VALIDATION_INCOMPLETE
    )

    observation = moc_engine.build_observation(
        observation_type=moc_taxonomy.TYPE_FUTURES,
        instrument=instrument_symbol,
        exchange=exchange,
        segment=segment,
        timestamp=timestamp,
        resolution=resolution,
        source=source,
        schema_version=schema_version,
        value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
        payload=field_values,
        completeness=completeness,
        freshness=0.0,
        confidence=confidence,
        missing_fields=missing_fields,
        validation_status=validation_status,
        source_quality=source_quality,
        originating_source=source,
        acquisition_timestamp=acquisition_timestamp,
        normalization_timestamp=normalization_timestamp,
        origin=origin,
        provenance_version=schema_version,
        transformation_history=(),
    )

    return FuturesObservation(
        observation=observation,
        expiry=expiry,
        underlying=underlying,
    )


def new_futures_series(underlying: str, expiry: str, instrument_symbol: str, resolution: str) -> FuturesObservationSeries:
    series = moc_engine.new_series(
        observation_type=moc_taxonomy.TYPE_FUTURES,
        instrument=instrument_symbol,
        resolution=resolution,
    )
    return FuturesObservationSeries(series=series, expiry=expiry, underlying=underlying)


def append_futures_observation(
    futures_series: FuturesObservationSeries, futures_observation: FuturesObservation
) -> FuturesObservationSeries:
    """Append one FuturesObservation to a FuturesObservationSeries,
    delegating ordering/gap logic entirely to MOC's own
    `engine.append_observation` -- never re-implemented here.
    """
    new_series = moc_engine.append_observation(futures_series.series, futures_observation.observation)
    return FuturesObservationSeries(
        series=new_series,
        expiry=futures_series.expiry,
        underlying=futures_series.underlying,
    )


def validate_futures_observation(
    futures_observation: FuturesObservation, expected_schema_version: str = SCHEMA_VERSION
) -> ValidationResult:
    """Structural validation, delegated entirely to MOC's own
    `engine.validate_observation` plus futures-domain checks: expiry
    and instrument_symbol must be present (they are futures-domain
    identity concepts not covered by MOC's generic
    ObservationIdentity). Never validates the market values themselves.
    """
    base_result = moc_engine.validate_observation(futures_observation.observation, expected_schema_version)
    reasons = list(base_result.reasons)
    if not futures_observation.expiry:
        reasons.append("MISSING_EXPIRY")
    if not futures_observation.instrument_symbol:
        reasons.append("MISSING_INSTRUMENT_SYMBOL")

    if reasons:
        return ValidationResult(is_valid=False, status=moc_taxonomy.VALIDATION_INVALID, reasons=tuple(reasons))
    return ValidationResult(is_valid=True, status=moc_taxonomy.VALIDATION_VALID, reasons=())

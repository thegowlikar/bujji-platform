"""Options Observation Domain engine — pure functions, no state, no IO.

Builds `OptionObservation` from already-normalized inputs (a single
already-parsed Bhavcopy option row's fields, as plain values) via MOC's
own `bujji.market_observation.engine.build_observation` -- this module
never mints an `observation_id` itself and never re-implements MOC's
identity hashing (per `docs/MARKET_OBSERVATION_CONTRACT.md` Extension
rule 3: only `engine.build_observation` may mint an observation_id).
Mirrors `bujji/futures_observation/engine.py` exactly.

Nothing here reads a CSV, opens a file, or touches the network -- raw
Bhavcopy parsing lives in runner.py; this module only ever receives
already-normalized scalars.

Uses `taxonomy.TYPE_OPTION_CHAIN` as the `observation_type` for every
full-row option observation this domain builds, mirroring 73B's choice
of `TYPE_FUTURES` for every full-row futures observation -- MOC's
`TYPE_OPTION_OPEN_INTEREST` / `TYPE_OPTION_VOLUME` /
`TYPE_OPTION_LIQUIDITY` remain available in MOC's taxonomy for a future
narrower-slice domain but are not used by this full-row ingestion path.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import ValidationResult

from . import taxonomy
from .config import DEFAULT_SOURCE, SCHEMA_VERSION
from .models import OptionObservation, OptionObservationSeries


def build_option_observation(
    underlying: str,
    instrument_symbol: str,
    strike: float,
    expiry: str,
    option_type: str,
    exchange: str,
    segment: str,
    timestamp: str,
    resolution: str,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    settlement: Optional[float],
    volume: Optional[float],
    open_interest: Optional[float],
    change_in_open_interest: Optional[float],
    underlying_price: Optional[float],
    origin: str,
    acquisition_timestamp: str,
    normalization_timestamp: str,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    bid_quantity: Optional[float] = None,
    ask_quantity: Optional[float] = None,
    source: str = DEFAULT_SOURCE,
    schema_version: str = SCHEMA_VERSION,
    source_quality: str = moc_taxonomy.SOURCE_QUALITY_HIGH,
    confidence: Optional[float] = None,
    observation_type: str = moc_taxonomy.TYPE_OPTION_CHAIN,
) -> OptionObservation:
    """Construct one OptionObservation from already-normalized inputs
    (one Bhavcopy option row's fields, as plain values). Any of the
    OHLC/settlement/volume/OI/underlying-price fields may genuinely be
    None -- these are recorded as disclosed gaps in `missing_fields`,
    never fabricated. `bid`/`ask`/`bid_quantity`/`ask_quantity` default
    to None and, for the Bhavcopy ingestion path in runner.py, are
    always None -- see taxonomy.py's module docstring for the disclosed
    evidence that Bhavcopy carries no such columns.
    """
    field_values: Dict[str, Optional[float]] = {
        taxonomy.FIELD_OPEN: open_,
        taxonomy.FIELD_HIGH: high,
        taxonomy.FIELD_LOW: low,
        taxonomy.FIELD_CLOSE: close,
        taxonomy.FIELD_SETTLEMENT: settlement,
        taxonomy.FIELD_VOLUME: volume,
        taxonomy.FIELD_OPEN_INTEREST: open_interest,
        taxonomy.FIELD_CHANGE_IN_OPEN_INTEREST: change_in_open_interest,
        taxonomy.FIELD_UNDERLYING_PRICE: underlying_price,
        taxonomy.FIELD_BID: bid,
        taxonomy.FIELD_ASK: ask,
        taxonomy.FIELD_BID_QUANTITY: bid_quantity,
        taxonomy.FIELD_ASK_QUANTITY: ask_quantity,
    }

    # missing_fields records EVERY genuinely-None field (mandatory or
    # known-unavailable) -- disclosure is unconditional. Only the
    # mandatory subset counts against `completeness`, since
    # BID/ASK/BID_QUANTITY/ASK_QUANTITY are known, structurally,
    # never-available from Bhavcopy (see taxonomy.py); counting them
    # against completeness would mark every Bhavcopy-sourced
    # observation permanently INCOMPLETE for fields this source cannot
    # supply, which is not a genuine per-row data-quality signal.
    missing_mandatory = tuple(
        f for f in taxonomy.MANDATORY_OPTIONS_OBSERVATION_FIELDS if field_values.get(f) is None
    )
    missing_known_unavailable = tuple(
        f for f in taxonomy.KNOWN_UNAVAILABLE_FROM_BHAVCOPY if field_values.get(f) is None
    )
    missing_fields = missing_mandatory + missing_known_unavailable

    present_mandatory = len(taxonomy.MANDATORY_OPTIONS_OBSERVATION_FIELDS) - len(missing_mandatory)
    completeness = present_mandatory / len(taxonomy.MANDATORY_OPTIONS_OBSERVATION_FIELDS)
    validation_status = (
        moc_taxonomy.VALIDATION_VALID if not missing_mandatory else moc_taxonomy.VALIDATION_INCOMPLETE
    )

    observation = moc_engine.build_observation(
        observation_type=observation_type,
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

    return OptionObservation(
        observation=observation,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        underlying=underlying,
    )


def new_option_series(
    underlying: str,
    strike: float,
    expiry: str,
    option_type: str,
    instrument_symbol: str,
    resolution: str,
    observation_type: str = moc_taxonomy.TYPE_OPTION_CHAIN,
) -> OptionObservationSeries:
    series = moc_engine.new_series(
        observation_type=observation_type,
        instrument=instrument_symbol,
        resolution=resolution,
    )
    return OptionObservationSeries(
        series=series, strike=strike, expiry=expiry, option_type=option_type, underlying=underlying
    )


def append_option_observation(
    option_series: OptionObservationSeries, option_observation: OptionObservation
) -> OptionObservationSeries:
    """Append one OptionObservation to an OptionObservationSeries,
    delegating ordering/gap logic entirely to MOC's own
    `engine.append_observation` -- never re-implemented here.
    """
    new_series = moc_engine.append_observation(option_series.series, option_observation.observation)
    return OptionObservationSeries(
        series=new_series,
        strike=option_series.strike,
        expiry=option_series.expiry,
        option_type=option_series.option_type,
        underlying=option_series.underlying,
    )


def validate_option_observation(
    option_observation: OptionObservation, expected_schema_version: str = SCHEMA_VERSION
) -> ValidationResult:
    """Structural validation, delegated entirely to MOC's own
    `engine.validate_observation` plus options-domain checks: strike,
    expiry, option_type, and instrument_symbol must be present (they
    are options-domain identity concepts not covered by MOC's generic
    ObservationIdentity), and option_type must be one of
    taxonomy.ALL_OPTION_TYPES. Never validates the market values
    themselves.
    """
    base_result = moc_engine.validate_observation(option_observation.observation, expected_schema_version)
    reasons = list(base_result.reasons)
    if option_observation.strike is None:
        reasons.append("MISSING_STRIKE")
    if not option_observation.expiry:
        reasons.append("MISSING_EXPIRY")
    if not option_observation.option_type:
        reasons.append("MISSING_OPTION_TYPE")
    elif option_observation.option_type not in taxonomy.ALL_OPTION_TYPES:
        reasons.append("UNKNOWN_OPTION_TYPE")
    if not option_observation.underlying:
        reasons.append("MISSING_UNDERLYING")
    if not option_observation.instrument_symbol:
        reasons.append("MISSING_INSTRUMENT_SYMBOL")

    if reasons:
        return ValidationResult(is_valid=False, status=moc_taxonomy.VALIDATION_INVALID, reasons=tuple(reasons))
    return ValidationResult(is_valid=True, status=moc_taxonomy.VALIDATION_VALID, reasons=())

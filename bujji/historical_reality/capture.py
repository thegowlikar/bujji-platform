"""Historical observation construction — Phase 17H.3/17H.4.

The single entry point an ingestion script uses to turn an
already-normalized FYERS historical candle row into a
`HistoricalObservation`. Mirrors
`bujji.market_reality.capture.build_raw_observation()` deliberately --
same MOC-quality-fields-as-honest-placeholders pattern (real provenance
lives in the lineage block, not here; see PHASE_17H2_0_3's own finding
that this is the correct pattern, not a shortcut), same "no
interpretation, no fabrication" discipline. Performs NO validation --
that is the ingestion script's job (PHASE_17H2 Part 6 rules), applied
BEFORE this function is called.
"""
from __future__ import annotations

from typing import Any, Optional

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.engine import build_observation

from .models import HistoricalLineage, HistoricalObservation

# Distinct from market_reality.taxonomy.TRANSFORMATION_RAW_CAPTURE --
# that label specifically means live capture. This is a different fact
# about how the record entered Bujji. Free-form provenance metadata,
# not a closed/validated vocabulary anywhere in this codebase -- no
# existing taxonomy module needed a new entry for this.
TRANSFORMATION_HISTORICAL_INGESTION = "HISTORICAL_INGESTION"


def build_historical_observation(
    *,
    instrument_identity: str,
    instrument_type: str,
    resolution: str,
    timestamp: str,
    payload: Any,
    source: str,
    access_method: str,
    source_epoch: int,
    source_symbol: str,
    raw_artifact_ref: str,
    ingestion_run_id: str,
    retrieved_at: str,
    certification_status: str,
    certification_ref: Optional[str] = None,
    continuity_method: Optional[str] = None,
    exchange: str = "NSE",
    segment: str = "NSE_FO",
    schema_version: str = "1.0.0",
    value_kind: str = moc_taxonomy.VALUE_KIND_OHLC,
) -> HistoricalObservation:
    """Assemble a `HistoricalObservation` from an already-normalized,
    already-validated row.

    `timestamp` must already be the session-boundary-derived value
    (PHASE_17H2 Part 5 -- e.g. 09:15:00 IST for a daily bar's window
    start), never the raw UTC-midnight epoch rendered naively.

    `value_kind` defaults to `VALUE_KIND_OHLC` -- every caller that
    existed before PHASE_17I11 (spot/futures/VIX daily+intraday
    ingestion) passes a genuine OHLCV candle and is unaffected by this
    default. A non-candle payload (e.g. options chain state -- ltp/bid/
    ask/oi, not open/high/low/close) MUST pass its correct kind
    explicitly, e.g. `moc_taxonomy.VALUE_KIND_MAPPING` -- the same kind
    `market_reality.capture` and `options_observation.engine` already
    use for option/quote/depth payloads (PHASE_17I11).
    """
    observation = build_observation(
        observation_type=moc_taxonomy.TYPE_PRICE,
        instrument=instrument_identity,
        exchange=exchange,
        segment=segment,
        timestamp=timestamp,
        resolution=resolution,
        source=source,
        schema_version=schema_version,
        value_kind=value_kind,
        payload=payload,
        completeness=1.0,
        freshness=0.0,
        confidence=None,  # FYERS publishes no source-side confidence for historical candles.
        missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_UNKNOWN,
        source_quality=moc_taxonomy.SOURCE_QUALITY_UNKNOWN,
        originating_source=source,
        acquisition_timestamp=retrieved_at,
        normalization_timestamp=retrieved_at,
        origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION,
        provenance_version=schema_version,
        transformation_history=(TRANSFORMATION_HISTORICAL_INGESTION,),
    )

    lineage = HistoricalLineage(
        source=source,
        access_method=access_method,
        source_epoch=source_epoch,
        source_symbol=source_symbol,
        raw_artifact_ref=raw_artifact_ref,
        ingestion_run_id=ingestion_run_id,
        retrieved_at=retrieved_at,
        certification_status=certification_status,
        certification_ref=certification_ref,
        continuity_method=continuity_method,
        schema_version=schema_version,
    )

    return HistoricalObservation(
        observation=observation,
        instrument_type=instrument_type,
        lineage=lineage,
    )

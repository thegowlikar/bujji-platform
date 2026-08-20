"""Raw observation construction — Phase 17E Layer 0.

The single entry point a collector uses to turn an already-normalized
broker response into a `RawObservation`.

Delegates observation_id minting to `market_observation.engine.
build_observation()` -- the ONLY place in this codebase an observation_id
is minted. Layer 0 never re-derives it, so Layer 0 and MOC can never
disagree about what an observation's identity is, and the deterministic
content-hash property (same fact => same id => free duplicate detection)
is inherited rather than reimplemented.

This module performs NO interpretation: it does not infer a missing
field, fabricate a value, compute anything from the payload, or decide
whether an observation is any good. Whether it may be stored is the
validator's decision, made later.

No wall-clock is read. Every timestamp is supplied by the caller.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.engine import build_observation

from .observation_quality import assess_observation_quality

from . import taxonomy
from .models import Layer0Lineage, RawObservation, derive_confidence

# Layer 0 capture kind -> MOC observation domain type. Explicit, closed
# and one-directional: `kind` records HOW the fact was captured, the MOC
# type records WHAT domain it belongs to, and neither is ever inferred
# from the other at read time.
KIND_TO_MOC_TYPE = {
    taxonomy.KIND_MARKET_TICK: moc_taxonomy.TYPE_PRICE,
    taxonomy.KIND_QUOTE: moc_taxonomy.TYPE_PRICE,
    taxonomy.KIND_MARKET_DEPTH: "MARKET_DEPTH",
    taxonomy.KIND_OPTION_CHAIN: moc_taxonomy.TYPE_OPTION_CHAIN,
    taxonomy.KIND_CANDLE: moc_taxonomy.TYPE_PRICE,
}

KIND_TO_VALUE_KIND = {
    taxonomy.KIND_MARKET_TICK: moc_taxonomy.VALUE_KIND_MAPPING,
    taxonomy.KIND_QUOTE: moc_taxonomy.VALUE_KIND_MAPPING,
    taxonomy.KIND_MARKET_DEPTH: moc_taxonomy.VALUE_KIND_MAPPING,
    taxonomy.KIND_OPTION_CHAIN: moc_taxonomy.VALUE_KIND_MAPPING,
    taxonomy.KIND_CANDLE: moc_taxonomy.VALUE_KIND_OHLC,
}

KIND_TO_RESOLUTION = {
    taxonomy.KIND_MARKET_TICK: moc_taxonomy.RESOLUTION_TICK,
    taxonomy.KIND_QUOTE: moc_taxonomy.RESOLUTION_TICK,
    taxonomy.KIND_MARKET_DEPTH: moc_taxonomy.RESOLUTION_TICK,
    taxonomy.KIND_OPTION_CHAIN: moc_taxonomy.RESOLUTION_TICK,
    taxonomy.KIND_CANDLE: moc_taxonomy.RESOLUTION_ONE_MINUTE,
}


def build_raw_observation(
    *,
    kind: str,
    instrument: str,
    instrument_type: str,
    payload: Any,
    source: str,
    access_method: str,
    capture_timestamp: str,
    event_timestamp: Optional[str] = None,
    certification_status: str = taxonomy.CERTIFICATION_MISSING,
    certification_ref: Optional[str] = None,
    exchange: str = "NSE",
    segment: str = "NSE_FO",
    resolution: Optional[str] = None,
    origin: str = moc_taxonomy.ORIGIN_LIVE,
    identity_fields: Optional[Mapping[str, Any]] = None,
    integrity_ok: bool = True,
    schema_version: str = taxonomy.LAYER0_SCHEMA_VERSION,
) -> RawObservation:
    """Assemble a `RawObservation` from already-normalized inputs.

    `certification_status` defaults to CERTIFICATION_MISSING -- the
    fail-closed value. A caller that does not supply a real, resolved
    certification status gets a record the validator will reject, which
    is the correct default for a gate whose entire purpose is to keep
    uncertified data out.

    `confidence` is derived, never passed in: there is no argument here
    that lets a caller assert its own data is trustworthy.
    """
    moc_type = KIND_TO_MOC_TYPE.get(kind, moc_taxonomy.TYPE_UNKNOWN)
    value_kind = KIND_TO_VALUE_KIND.get(kind, moc_taxonomy.VALUE_KIND_MAPPING)
    effective_resolution = resolution or KIND_TO_RESOLUTION.get(
        kind, moc_taxonomy.RESOLUTION_EVENT
    )

    # Measure BEFORE building, so the observation carries real numbers from
    # the moment it exists rather than being stamped and corrected later.
    # Never raises: a quality assessment that could end a capture would cost
    # the very observation it exists to describe.
    quality = assess_observation_quality(
        kind=kind, payload=payload,
        capture_timestamp=capture_timestamp, event_timestamp=event_timestamp,
    )

    # The MOC identity timestamp is EVENT time when the source published
    # one, falling back to capture time only when it genuinely did not.
    # Both are preserved distinctly on the lineage block regardless, so
    # the fallback is never lossy.
    identity_timestamp = event_timestamp or capture_timestamp

    observation = build_observation(
        observation_type=moc_type,
        instrument=instrument,
        exchange=exchange,
        segment=segment,
        timestamp=identity_timestamp,
        resolution=effective_resolution,
        source=source,
        schema_version=schema_version,
        value_kind=value_kind,
        payload=payload,
        # MEASURED, not asserted (2026-08-21). This block previously read
        # `completeness=1.0, missing_fields=(), validation_status=UNKNOWN` on
        # EVERY observation unconditionally -- a record simultaneously
        # admitting it had never been validated and claiming to be 100%
        # complete. The number could not change, so it carried no
        # information, while reading to every consumer like a measurement.
        #
        # `quality` below is computed from the payload actually in hand
        # against a required-field set grounded in the real captured corpus.
        completeness=quality.completeness,
        freshness=0.0,  # Staleness is a property of the STREAM, not of one
                        # record; asserting it here is how a per-record field
                        # ends up lying about the feed. Left at the model's
                        # own zero and measured by the stream-level checks.
        confidence=None,  # MOC's source-published confidence: FYERS publishes none.
        missing_fields=quality.missing_fields,
        validation_status=quality.validation_status,
        # SOURCE quality describes the FEED, not this record. Nothing here
        # measures the feed, so it stays UNKNOWN rather than being inferred
        # from a single healthy-looking payload.
        source_quality=moc_taxonomy.SOURCE_QUALITY_UNKNOWN,
        originating_source=source,
        acquisition_timestamp=capture_timestamp,
        normalization_timestamp=capture_timestamp,
        origin=origin,
        provenance_version=schema_version,
        transformation_history=(taxonomy.TRANSFORMATION_RAW_CAPTURE,),
    )

    lineage = Layer0Lineage(
        source=source,
        access_method=access_method,
        event_timestamp=event_timestamp,
        capture_timestamp=capture_timestamp,
        certification_status=certification_status,
        certification_ref=certification_ref,
        confidence=derive_confidence(certification_status, integrity_ok),
        schema_version=schema_version,
        transformation_history=(taxonomy.TRANSFORMATION_RAW_CAPTURE,),
    )

    return RawObservation(
        observation=observation,
        kind=kind,
        instrument_type=instrument_type,
        lineage=lineage,
        identity_fields=dict(identity_fields or {}),
        quality_detail={
            "anomalies": list(quality.anomalies),
            "acquisition_latency_seconds": quality.acquisition_latency_seconds,
        },
    )

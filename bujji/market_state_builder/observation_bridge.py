"""Observation Bridge -- Shadow Campaign v2 Phase 3B.

Converts a MarketSnapshot's spot/VIX/futures facts into real
market_observation.models.Observation objects, using the EXISTING,
unmodified market_observation.engine.build_observation() -- the same
function futures_adapter.py (Phase 1) and options_observation already
reuse. No observation construction logic is duplicated here.

MOC's Observation is one-fact-per-record by design (see
market_observation/models.py's own module docstring) -- a single
MarketSnapshot carries three independent facts (spot price, VIX level,
futures quote), so this bridge exposes one builder per fact rather than
forcing them into a single Observation object. Option-chain legs are
handled separately in option_observation_bridge.py, since MOC's own
domain split (73B futures / 73C options) treats them as a distinct kind.

Missing spot/VIX/futures data (per Phase 1/2's own honest-gap design)
means these builders return None -- never a fabricated Observation.
"""
from __future__ import annotations

from typing import Optional, Sequence

from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import Observation

from bujji.market_perception.models import MarketSnapshot

SOURCE = "fyers_live"
SCHEMA_VERSION = "1.0"
ORIGIN = moc_taxonomy.ORIGIN_LIVE
SOURCE_QUALITY = moc_taxonomy.SOURCE_QUALITY_HIGH


def _build(
    observation_type: str, instrument: str, segment: str, timestamp: str,
    value_kind: str, payload, missing_fields: Sequence[str] = (),
) -> Observation:
    missing = tuple(missing_fields)
    completeness = 0.0 if missing else 1.0
    validation_status = (
        moc_taxonomy.VALIDATION_INCOMPLETE if missing else moc_taxonomy.VALIDATION_VALID
    )
    return moc_engine.build_observation(
        observation_type=observation_type, instrument=instrument, exchange="NSE",
        segment=segment, timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_TICK,
        source=SOURCE, schema_version=SCHEMA_VERSION, value_kind=value_kind, payload=payload,
        completeness=completeness, freshness=0.0, confidence=None,
        missing_fields=missing, validation_status=validation_status,
        source_quality=SOURCE_QUALITY, originating_source=SOURCE,
        acquisition_timestamp=timestamp, normalization_timestamp=timestamp,
        origin=ORIGIN, provenance_version=SCHEMA_VERSION,
    )


def build_observation_from_snapshot(snapshot: MarketSnapshot) -> Optional[Observation]:
    """The PRICE (spot) Observation -- this is what feeds
    live_market_events.detect_price_change() / Episode formation /
    msi_price_structure / msi_market_structure downstream. Returns None
    if spot is honestly unavailable this cycle, never a fabricated price."""
    if snapshot.spot.ltp is None:
        return None
    return _build(
        moc_taxonomy.TYPE_PRICE, snapshot.spot.symbol, "INDEX", snapshot.timestamp,
        moc_taxonomy.VALUE_KIND_SCALAR, float(snapshot.spot.ltp),
    )


def build_vix_observation_from_snapshot(snapshot: MarketSnapshot) -> Optional[Observation]:
    """Separate VIX Observation -- not wired into Episode/MarketEvent
    detection this phase (only the PRICE stream is), but built for
    completeness/future use. Returns None if VIX is honestly unavailable."""
    if snapshot.vix.value is None:
        return None
    return _build(
        moc_taxonomy.TYPE_VOLATILITY_VIX, "INDIAVIX", "INDEX", snapshot.timestamp,
        moc_taxonomy.VALUE_KIND_SCALAR, float(snapshot.vix.value),
    )


def build_futures_observation_from_snapshot(snapshot: MarketSnapshot) -> Optional[Observation]:
    """Separate FUTURES Observation, MAPPING-shaped (close/volume/OI),
    mirroring futures_observation's own payload shape. Returns None if
    the futures leg is honestly unavailable; volume/open_interest are
    individually recorded as missing_fields when only ltp came through,
    never fabricated."""
    if snapshot.futures is None or snapshot.futures.ltp is None:
        return None
    payload = {"close": snapshot.futures.ltp}
    missing = []
    if snapshot.futures.volume is None:
        missing.append("volume")
    else:
        payload["volume"] = snapshot.futures.volume
    if snapshot.futures.open_interest is None:
        missing.append("open_interest")
    else:
        payload["open_interest"] = snapshot.futures.open_interest
    return _build(
        moc_taxonomy.TYPE_FUTURES, snapshot.futures.symbol, "FO", snapshot.timestamp,
        moc_taxonomy.VALUE_KIND_MAPPING, payload, missing_fields=missing,
    )

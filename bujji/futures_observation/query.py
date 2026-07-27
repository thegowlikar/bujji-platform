"""Pure, read-only query helpers over FuturesObservationSeries —
Engineering Series 73B.

No mutation, no aggregation, no market-meaning filtering (no
long-buildup/short-covering classification) -- only lookups over what
is already stored, delegating to
`bujji.market_observation.query` wherever the lookup is
domain-neutral.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.market_observation import query as moc_query

from .models import FuturesObservation, FuturesObservationSeries


def by_underlying(fs: FuturesObservationSeries, underlying: str) -> Tuple[FuturesObservation, ...]:
    """All FuturesObservations in the series matching a given
    underlying (defensive filter -- a well-formed series only ever
    contains one underlying+expiry contract, per MOC's
    ObservationSeries.instrument invariant)."""
    return tuple(o for o in fs.observations() if o.underlying == underlying)


def by_instrument_symbol(fs: FuturesObservationSeries, instrument_symbol: str) -> Tuple[FuturesObservation, ...]:
    return tuple(o for o in fs.observations() if o.instrument_symbol == instrument_symbol)


def by_expiry(fs: FuturesObservationSeries, expiry: str) -> Tuple[FuturesObservation, ...]:
    return tuple(o for o in fs.observations() if o.expiry == expiry)


def by_resolution(fs: FuturesObservationSeries, resolution: str) -> Tuple[FuturesObservation, ...]:
    return tuple(o for o in fs.observations() if o.resolution == resolution)


def at_or_before(fs: FuturesObservationSeries, timestamp: str) -> Optional[FuturesObservation]:
    result = moc_query.at_or_before(fs.series, timestamp)
    if result is None:
        return None
    return FuturesObservation(observation=result, expiry=fs.expiry, instrument_symbol=fs.instrument_symbol)


def in_window(fs: FuturesObservationSeries, start: str, end: str) -> Tuple[FuturesObservation, ...]:
    matched = moc_query.in_window(fs.series, start, end)
    return tuple(
        FuturesObservation(observation=o, expiry=fs.expiry, underlying=fs.underlying)
        for o in matched
    )


def latest(fs: FuturesObservationSeries) -> Optional[FuturesObservation]:
    result = moc_query.latest(fs.series)
    if result is None:
        return None
    return FuturesObservation(observation=result, expiry=fs.expiry, underlying=fs.underlying)


def earliest(fs: FuturesObservationSeries) -> Optional[FuturesObservation]:
    result = moc_query.earliest(fs.series)
    if result is None:
        return None
    return FuturesObservation(observation=result, expiry=fs.expiry, underlying=fs.underlying)


def count(fs: FuturesObservationSeries) -> int:
    return moc_query.count(fs.series)

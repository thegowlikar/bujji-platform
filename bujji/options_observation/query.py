"""Pure, read-only query helpers over OptionObservationSeries —
Engineering Series 73C.

No mutation, no PCR/OI-aggregation/chain-calculation, no market-meaning
filtering -- only lookups over what is already stored, delegating to
`bujji.market_observation.query` wherever the lookup is domain-neutral.
Mirrors `bujji/futures_observation/query.py` exactly, extended with
strike/option_type lookups since an options series carries those extra
identity dimensions.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.market_observation import query as moc_query

from .models import OptionObservation, OptionObservationSeries


def by_underlying(os_: OptionObservationSeries, underlying: str) -> Tuple[OptionObservation, ...]:
    """All OptionObservations in the series matching a given underlying
    (defensive filter -- a well-formed series only ever contains one
    underlying+strike+expiry+option_type contract, per MOC's
    ObservationSeries.instrument invariant)."""
    return tuple(o for o in os_.observations() if o.underlying == underlying)


def by_instrument_symbol(os_: OptionObservationSeries, instrument_symbol: str) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.instrument_symbol == instrument_symbol)


def by_strike(os_: OptionObservationSeries, strike: float) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.strike == strike)


def by_expiry(os_: OptionObservationSeries, expiry: str) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.expiry == expiry)


def by_option_type(os_: OptionObservationSeries, option_type: str) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.option_type == option_type)


def by_observation_type(os_: OptionObservationSeries, observation_type: str) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.observation_type == observation_type)


def by_resolution(os_: OptionObservationSeries, resolution: str) -> Tuple[OptionObservation, ...]:
    return tuple(o for o in os_.observations() if o.resolution == resolution)


def at_or_before(os_: OptionObservationSeries, timestamp: str) -> Optional[OptionObservation]:
    result = moc_query.at_or_before(os_.series, timestamp)
    if result is None:
        return None
    return OptionObservation(
        observation=result,
        strike=os_.strike,
        expiry=os_.expiry,
        option_type=os_.option_type,
        underlying=os_.underlying,
        symbol_provenance=os_.symbol_provenance,
    )


def in_window(os_: OptionObservationSeries, start: str, end: str) -> Tuple[OptionObservation, ...]:
    matched = moc_query.in_window(os_.series, start, end)
    return tuple(
        OptionObservation(
            observation=o,
            strike=os_.strike,
            expiry=os_.expiry,
            option_type=os_.option_type,
            underlying=os_.underlying,
            symbol_provenance=os_.symbol_provenance,
        )
        for o in matched
    )


def latest(os_: OptionObservationSeries) -> Optional[OptionObservation]:
    result = moc_query.latest(os_.series)
    if result is None:
        return None
    return OptionObservation(
        observation=result,
        strike=os_.strike,
        expiry=os_.expiry,
        option_type=os_.option_type,
        underlying=os_.underlying,
        symbol_provenance=os_.symbol_provenance,
    )


def earliest(os_: OptionObservationSeries) -> Optional[OptionObservation]:
    result = moc_query.earliest(os_.series)
    if result is None:
        return None
    return OptionObservation(
        observation=result,
        strike=os_.strike,
        expiry=os_.expiry,
        option_type=os_.option_type,
        underlying=os_.underlying,
        symbol_provenance=os_.symbol_provenance,
    )


def count(os_: OptionObservationSeries) -> int:
    return moc_query.count(os_.series)

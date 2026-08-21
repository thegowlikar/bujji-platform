"""Option Observation Bridge -- Shadow Campaign v2 Phase 3B.

Converts MarketSnapshot.option_chain legs into real
options_observation.models.OptionObservation objects, using the
EXISTING, unmodified options_observation.engine.build_option_observation().
This becomes the ChainSnapshot input for msi_participant_positioning --
a plain Tuple[OptionObservation, ...] satisfies its duck-typed
ChainSnapshot contract (confirmed by reading assess_participant_
positioning's own helper functions during Phase 3B's investigation;
ChainSnapshot is not a real class anywhere in this codebase).

No Greeks are invented -- OptionLeg's iv/delta/gamma/theta/vega (never
populated by Phase 1's option_chain_adapter) are simply not passed
through; build_option_observation() has no such parameters at all.
"""
from __future__ import annotations

from typing import Tuple

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.options_observation import taxonomy as opt_taxonomy
from bujji.options_observation.engine import build_option_observation
from bujji.options_observation.models import OptionObservation

from bujji.market_perception.models import MarketSnapshot

SOURCE = "fyers_live"
SCHEMA_VERSION = "1.0"
ORIGIN = moc_taxonomy.ORIGIN_LIVE


def build_option_observations_from_snapshot(snapshot: MarketSnapshot) -> Tuple[OptionObservation, ...]:
    """Empty tuple (never fabricated legs) if there's no option chain
    this cycle. Preserves strike, option type, OI, underlying price,
    and timestamp exactly as recorded in the snapshot; bid/ask are
    also preserved (available since Phase 1)."""
    if snapshot.option_chain is None:
        return ()
    underlying_price = snapshot.spot.ltp
    observations = []
    for leg in snapshot.option_chain.legs:
        observations.append(
            build_option_observation(
                underlying=snapshot.option_chain.underlying,
                instrument_symbol=leg.symbol,
                strike=leg.strike,
                expiry=snapshot.option_chain.expiry,
                option_type=leg.option_type,
                exchange="NSE",
                segment="FO",
                timestamp=snapshot.timestamp,
                resolution=moc_taxonomy.RESOLUTION_TICK,
                open_=None, high=None, low=None, close=leg.ltp,
                settlement=None,
                volume=leg.volume,
                open_interest=leg.open_interest,
                change_in_open_interest=None,
                underlying_price=underlying_price,
                origin=ORIGIN,
                acquisition_timestamp=snapshot.timestamp,
                normalization_timestamp=snapshot.timestamp,
                bid=leg.bid, ask=leg.ask,
                source=SOURCE, schema_version=SCHEMA_VERSION,
                # leg.symbol traces to OptionContract.symbol, read verbatim
                # from FYERS's own cached instrument master
                # (market_perception/option_chain_adapter.py: COL_SYMBOL of
                # data/instrument_master/fyers_fo_NSE.csv). The venue's string.
                symbol_provenance=opt_taxonomy.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE,
            )
        )
    return tuple(observations)

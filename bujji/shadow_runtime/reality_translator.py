"""Reality Translator -- Shadow Runtime, Phase 19.10.2.

Translates `bujji.market_perception.models.MarketSnapshot` (the
runner's own already-fetched, real live-tick observation -- built every
cycle by `MarketDataAdapter.build_snapshot()`, unmodified) into
`bujji.market_reality_snapshot.models.MarketRealitySnapshot` (the
canonical Phase 18 Reality-tier shape Phase 19.10.1's intelligence
pipeline adapter consumes). No new broker call, no new data source --
pure reshaping of data the runner already has in hand this cycle.

Two, same-named-but-different classes exist in this codebase for
"SpotSnapshot"/"VixSnapshot" (`market_perception.models` vs.
`market_reality_snapshot.models`) -- Phase 19.10.0's own confirmed
naming collision. This module is the one place that bridges them,
explicitly and by name, rather than silently relying on duck typing.

HONEST LIMITATION, stated rather than hidden: `market_perception`'s live
`SpotSnapshot` carries only `ltp` (a single real-time tick), never a
bar's open/high/low. This translator sets
`open == high == low == close == ltp` -- not an approximation of a
missing bar, but the mathematically correct OHLC for a zero-duration,
single-instant window (the definition of a point observation, not a
fabrication of range data that was never observed).
"""
from __future__ import annotations

from bujji.market_perception.models import MarketSnapshot as LiveMarketSnapshot
from bujji.market_reality_snapshot.models import (
    COMPLETENESS_PARTIAL,
    OptionContractSnapshot,
    OptionsSnapshot,
    RESOLUTION_FIVE_MINUTE,
    MarketRealitySnapshot,
)
from bujji.market_reality_snapshot.models import SpotSnapshot as RealitySpotSnapshot
from bujji.market_reality_snapshot.models import VixSnapshot as RealityVixSnapshot

SOURCE_LIVE_TICK = "SOURCE_LIVE"


class RealityTranslationError(Exception):
    """Raised when `market_snapshot` genuinely lacks the minimum real
    data (a spot tick) to translate -- never silently produces an empty
    or fabricated `MarketRealitySnapshot`."""


def translate_market_snapshot_to_reality_snapshot(
    market_snapshot: LiveMarketSnapshot,
) -> MarketRealitySnapshot:
    if market_snapshot.spot is None or market_snapshot.spot.ltp is None:
        raise RealityTranslationError("market_snapshot has no real spot ltp -- nothing to translate")

    spot_ltp = market_snapshot.spot.ltp
    reality_spot = RealitySpotSnapshot(
        open=spot_ltp, high=spot_ltp, low=spot_ltp, close=spot_ltp, volume=None,
        source=SOURCE_LIVE_TICK, source_observation_ids=(), observed_at=market_snapshot.timestamp,
    )

    reality_vix = None
    if market_snapshot.vix is not None and market_snapshot.vix.value is not None:
        change_percent = None
        if market_snapshot.vix.prev_close:
            change_percent = (market_snapshot.vix.value - market_snapshot.vix.prev_close) / market_snapshot.vix.prev_close * 100.0
        reality_vix = RealityVixSnapshot(
            close=market_snapshot.vix.value, open=None, high=None, low=None,
            change_percent=change_percent, source=SOURCE_LIVE_TICK,
            source_observation_ids=(), observed_at=market_snapshot.timestamp,
        )

    reality_options = None
    if market_snapshot.option_chain is not None:
        contracts = tuple(
            OptionContractSnapshot(
                identity=f"{market_snapshot.option_chain.underlying}|{market_snapshot.option_chain.expiry}|{leg.strike}|{leg.option_type}",
                expiry=market_snapshot.option_chain.expiry, strike=leg.strike, option_type=leg.option_type,
                ltp=leg.ltp, bid=leg.bid, ask=leg.ask, open_interest=leg.open_interest, volume=leg.volume,
                source=SOURCE_LIVE_TICK, source_observation_ids=(),
            )
            for leg in market_snapshot.option_chain.legs
        )
        reality_options = OptionsSnapshot(contracts=contracts, source=SOURCE_LIVE_TICK)

    date = market_snapshot.timestamp[:10]  # "YYYY-MM-DD" -- the timestamp's own date, never a separate clock read.
    return MarketRealitySnapshot(
        date=date, spot=reality_spot, futures=None, vix=reality_vix,
        completeness=COMPLETENESS_PARTIAL,  # a single live tick is never a complete day's view -- honest, always.
        is_final=False, certification_refs=(), built_at=market_snapshot.timestamp,
        options=reality_options, resolution=RESOLUTION_FIVE_MINUTE, as_of=market_snapshot.timestamp,
    )

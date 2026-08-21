"""Phase 20.25 -- calls the REAL, already-existing, already-tested
`bujji.intelligence.{volatility_brain,liquidity_brain,structure_brain,
greeks_brain}` directly -- zero fill/slippage/IV/Greeks math is
reimplemented anywhere in this file. Every brain is stateless (each
docstring's own "never mutates anything, never talks to a broker"
disclosure) -- fresh instances are constructed per call, matching
`bujji.mic_v0.engine`'s own `RegimeBrain()` reuse pattern exactly.

`regime` is deliberately never populated here: `bujji.mic_v0.engine.
compose_market_state()` computes its own internal `RegimeReading` but
returns only the mapped `MarketState` string (confirmed by direct
inspection) -- reconstructing a synthetic `RegimeReading` from that
string would require either fabricating a `confidence` float that was
never real, or reconciling two independently-real regime vocabularies
(Phase 20.24's own disclosed finding) without real data to do so
honestly. The real MIC regime is already carried through faithfully
via `RuntimeIntelligenceContext.mic_market_context.mic_regime`
(Phase 20.24) -- nothing is lost by leaving it out here.

`premium` is deliberately never populated here -- see `models.
OptionMarketDataForCycle`'s own docstring: `PremiumBrain` requires a
real position entry point that does not exist in Cycle 1's shadow
runtime.
"""
from __future__ import annotations

from typing import Sequence

from bujji.core.models import Candle
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.greeks_brain import GreeksBrain
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.structure_brain import StructureBrain
from bujji.intelligence.volatility_brain import VolatilityBrain
from bujji.mic_context_bridge import MarketUnderstandingContext, build_market_understanding_context

from .models import OptionMarketDataForCycle


def assemble_market_understanding_from_option_data(
    spot_candles: Sequence[Candle], option_data: OptionMarketDataForCycle, *, context: IntelligenceContext,
) -> MarketUnderstandingContext:
    volatility_reading = VolatilityBrain().analyze(
        list(spot_candles), option_data.spot, option_data.strike, option_data.t_years,
        option_data.ce_premium, option_data.pe_premium, context=context,
    )
    liquidity_reading = LiquidityBrain().analyze(
        option_data.ce_bid, option_data.ce_ask, option_data.pe_bid, option_data.pe_ask, context,
    )
    structure_reading = StructureBrain().analyze(option_data.spot, list(option_data.strikes), context)
    greeks_reading = GreeksBrain().analyze(
        option_data.spot, option_data.strike, option_data.t_years,
        volatility_reading.iv_ce, volatility_reading.iv_pe, context=context,
    )

    return build_market_understanding_context(
        regime=None, volatility=volatility_reading, premium=None,
        liquidity=liquidity_reading, structure=structure_reading, greeks=greeks_reading,
    )

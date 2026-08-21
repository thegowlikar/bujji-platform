"""Market Thesis Regime Provider — BUJJI Options OS v3, the minimal
wire connecting real market intelligence to `TradingSessionGovernor`.

WHY THIS FILE EXISTS AND NOT A BIGGER CHANGE: `bujji.production_
runtime.regime_provider.RegimeProvider` was DELIBERATELY designed as
the swap point for exactly this purpose — its own module docstring
states "This is what makes a future MIC/regime-classifier swap a
provider-only change, never a change to strategy_selector.py or
session_governor.py." Phase-1 shipped exactly one concrete provider,
`HumanSuppliedRegimeProvider` (a human types the regime). This module
adds the second one: a provider backed by real, already-composed
market intelligence instead of a human. `TradingSessionGovernor`, its
own local `strategy_selector.select_strategy()` (the deliberately
narrow, Gate-B-approved, premium-SELLING-only {IRON_CONDOR, IRON_FLY}
table), and `market_regime_adapter.py` are all imported and called
exactly as they already exist — none of them are modified.

NO NEW MARKET INTELLIGENCE IS COMPUTED HERE. Every input this module
reads is already real:
  - `market_thesis.assess()`'s own `market_regime` (msi_trade_thesis's
    real thesis_type) and `directional_bias` (real MDI overall_
    direction pass-through) -- Phase 3, already tested, unmodified.
  - A real `VolatilityStructureAssessment.volatility_regime` value
    (the SAME real evidence used to build the thesis), fed directly
    into `market_regime_adapter.adapt_volatility_regime()` -- an
    already-real, already-tested, unmodified relabeling function.

WHY `market_thesis.volatility_environment` IS NOT REUSED FOR THE
VOLATILITY REGIME: it is `msi_trade_thesis`'s own 4-value vocabulary
(EXPANSION/COMPRESSION/STABLE/UNKNOWN), a DIFFERENT coarse bucketing
than VSB's own real `volatility_regime` field (HIGH_VOLATILITY/
STABLE/TRANSITIONING/COMPRESSED/UNKNOWN) that `adapt_volatility_
regime()` was actually built to translate (confirmed by direct
comparison of the two taxonomies, not assumed). Silently feeding one
into a function built for the other would produce a plausible-looking
but unverified mapping -- exactly the class of mistake this whole
engagement has repeatedly found and corrected. The caller therefore
supplies the real VSB `volatility_regime` value directly.

TREND REGIME TRANSLATION -- the one genuinely new piece of logic in
this file, and it is a small, disclosed relabeling table, the same
CLASS of work `market_regime_adapter.py` itself already does for
RegimeBrain's vocabulary -- applied here to `market_thesis`'s real
output instead:
  RANGE_PERSISTENCE / MEAN_REVERSION          -> SIDEWAYS
  TREND_CONTINUATION / BREAKOUT + bullish lean -> TRENDING_UP
  TREND_CONTINUATION / BREAKOUT + bearish lean -> TRENDING_DOWN
  anything else (VOLATILITY_EXPANSION/COMPRESSION, FAILED_BREAKOUT,
  TREND_REVERSAL, EVENT_RISK, NO_TRADE, UNKNOWN, or a TREND_
  CONTINUATION/BREAKOUT with no clear directional lean) -> UNKNOWN,
  fails closed into the existing selector's own NO_TRADE path -- never
  guessed into SIDEWAYS or a direction it doesn't support.
"""
from __future__ import annotations

from typing import Optional

from bujji.market_thesis.models import MarketThesisAssessment
from bujji.production_runtime.regime_provider import MissingRegimeInputError, RegimeProvider
from bujji.trading_brain.risk_governor import market_regime_adapter as _mra

_SIDEWAYS_THESIS_TYPES = ("RANGE_PERSISTENCE", "MEAN_REVERSION")
_TREND_THESIS_TYPES = ("TREND_CONTINUATION", "BREAKOUT")
_BULLISH_DIRECTIONS = ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
_BEARISH_DIRECTIONS = ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")


def adapt_trend_regime_from_thesis(market_regime: str, directional_bias: str) -> str:
    """Pure relabeling, no computation. Exposed as a standalone
    function (not just a method) so it can be unit tested directly
    against every real msi_trade_thesis.taxonomy.ALL_THESIS_TYPES
    value without constructing a full provider."""
    if market_regime in _SIDEWAYS_THESIS_TYPES:
        return _mra.TREND_SIDEWAYS
    if market_regime in _TREND_THESIS_TYPES:
        if directional_bias in _BULLISH_DIRECTIONS:
            return _mra.TREND_TRENDING_UP
        if directional_bias in _BEARISH_DIRECTIONS:
            return _mra.TREND_TRENDING_DOWN
    return _mra.TREND_UNKNOWN


class MarketThesisRegimeProvider(RegimeProvider):
    """Backed by a real `MarketThesisAssessment` (from `bujji.
    market_thesis.assess()`) plus a real VSB `volatility_regime`
    string, instead of a human. Same fail-closed contract as
    `HumanSuppliedRegimeProvider`: a missing or UNKNOWN input raises
    `MissingRegimeInputError` at the point of use, never silently
    substitutes a guess."""

    def __init__(
        self, thesis: Optional[MarketThesisAssessment], volatility_regime: Optional[str] = None,
    ) -> None:
        self._thesis = thesis
        self._volatility_regime = volatility_regime

    def get_trend_regime(self) -> Optional[str]:
        if self._thesis is None:
            raise MissingRegimeInputError(
                "No real MarketThesisAssessment was supplied -- refusing to guess a trend regime."
            )
        if self._thesis.market_regime == "UNKNOWN":
            raise MissingRegimeInputError(
                "market_thesis could not form a real thesis this cycle (market_regime=UNKNOWN) -- "
                "refusing to guess a trend regime."
            )
        return adapt_trend_regime_from_thesis(self._thesis.market_regime, self._thesis.directional_bias)

    def get_volatility_regime(self) -> Optional[str]:
        if self._volatility_regime is None or self._volatility_regime == "UNKNOWN":
            raise MissingRegimeInputError(
                "No real VolatilityStructureAssessment.volatility_regime value was supplied (or it was "
                "itself UNKNOWN) -- refusing to guess a volatility regime."
            )
        return _mra.adapt_volatility_regime(self._volatility_regime)

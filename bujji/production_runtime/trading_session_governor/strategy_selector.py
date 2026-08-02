"""Regime-Based Strategy Selector -- BUJJI Options OS v3, Gate V1.1
Component 2.

PURPOSE: a pure lookup table, not an intelligence engine. Consumes
already-normalized regime labels from the EXISTING `market_regime_
adapter.py` (Gate D.5) -- ALL_TREND_REGIMES / ALL_VOLATILITY_REGIMES --
never recomputes a regime reading of its own. Chooses at most one
strategy family, always from Gate B's own already-approved, already-
tested defined-risk formula set -- never invents a new strategy shape.

STEP 1 FINDING THAT SHAPES THIS TABLE: Bujji's mandate is options
SELLING. Of Gate B's 7 defined-risk-APPROVED families
(msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES), only
IRON_CONDOR and IRON_FLY are genuine net-credit/premium-selling
shapes -- CALENDAR/BUTTERFLY/LONG_DIRECTIONAL/NEUTRAL_PREMIUM_BUYING/
VOLATILITY_EXPANSION are net-debit/buying shapes, and the real naked-
selling families (SHORT_DIRECTIONAL, NEUTRAL_PREMIUM_SELLING) are
Gate B's own PERMANENT VETOES for unbounded risk. This selector's
candidate universe is therefore honestly just {IRON_CONDOR, IRON_FLY}
-- never forcing a buying shape into a selling mandate, and never
reaching for a vetoed naked-selling family.

Fails closed on any missing/unrecognized input -- NO_TRADE, never a
guessed strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from bujji.trading_brain.risk_governor.market_regime_adapter import (
    TREND_SIDEWAYS, TREND_TRENDING_DOWN, TREND_TRENDING_UP, TREND_UNKNOWN,
    VOL_CONTRACTION, VOL_EXPANSION, VOL_HIGH, VOL_LOW, VOL_UNKNOWN,
)

Clock = Callable[[], datetime]

# The only two Gate-B-approved, genuinely premium-SELLING defined-risk
# families -- see module docstring. Never extended without a
# corresponding Gate B formula already existing and already approved.
SELLING_UNIVERSE = ("IRON_CONDOR", "IRON_FLY")

NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class StrategySelectionResult:
    selected_strategy: Optional[str]   # None means NO_TRADE
    trend_regime: str
    volatility_regime: str
    reasoning: str
    confidence: str                     # "HIGH" | "NONE" -- deterministic, from data completeness only, never a fabricated probability
    evaluated_at: datetime


# Deterministic mapping table -- the ENTIRE decision surface of this
# module. Every branch is documented; nothing outside this table
# influences the outcome.
def select_strategy(trend_regime: Optional[str], volatility_regime: Optional[str], clock: Clock) -> StrategySelectionResult:
    now = clock()

    if trend_regime is None or volatility_regime is None or trend_regime == TREND_UNKNOWN or volatility_regime == VOL_UNKNOWN:
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime or "MISSING", volatility_regime=volatility_regime or "MISSING",
            reasoning="Market regime unavailable or unrecognized -- fail closed, no trade today.",
            confidence="NONE", evaluated_at=now,
        )

    if trend_regime == TREND_SIDEWAYS and volatility_regime in (VOL_LOW, VOL_CONTRACTION):
        return StrategySelectionResult(
            selected_strategy="IRON_CONDOR", trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning=f"Range-bound market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"favors theta decay in a wide-wing defined-risk iron condor.",
            confidence="HIGH", evaluated_at=now,
        )

    if trend_regime == TREND_SIDEWAYS and volatility_regime == VOL_HIGH:
        return StrategySelectionResult(
            selected_strategy="IRON_FLY", trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning="Range-bound market with HIGH_VOL -- favors a tighter-body iron fly to capture "
                      "richer premium while keeping defined risk.",
            confidence="HIGH", evaluated_at=now,
        )

    if trend_regime in (TREND_TRENDING_UP, TREND_TRENDING_DOWN):
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning=f"Trending market ({trend_regime}) -- no Gate-B-approved defined-risk SELLING "
                      f"strategy fits a directional regime (the naked directional-selling family is "
                      f"permanently vetoed for unbounded risk); disciplined no-trade rather than forcing "
                      f"a buying strategy into a selling mandate.",
            confidence="NONE", evaluated_at=now,
        )

    if volatility_regime == VOL_EXPANSION:
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning="Volatility expanding -- premium-selling risk/reward is unfavorable while vol is "
                      "still rising; wait for stabilization rather than sell into expansion.",
            confidence="NONE", evaluated_at=now,
        )

    return StrategySelectionResult(
        selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
        reasoning=f"Regime combination ({trend_regime}, {volatility_regime}) has no mapped selling "
                  f"strategy in this table -- fail closed rather than guess.",
        confidence="NONE", evaluated_at=now,
    )

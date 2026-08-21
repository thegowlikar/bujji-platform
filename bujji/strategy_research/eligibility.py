"""Phase 20.3 -- Strategy Eligibility Matrix. Research metadata only:
a plain, declarative lookup from MIC's intraday regime label to the
Cycle-1 family allowed to trade it. No execution wiring, no scoring,
no ranking -- matches this codebase's established "declarative gate,
not a scorer" convention (see msi_strategy_selection_foundation for
the precedent this deliberately mirrors, at Cycle-1's much smaller
scope).
"""
from __future__ import annotations

from bujji.mic_v0_validation.models_intraday import (
    INTRADAY_BREAKOUT_ATTEMPT, INTRADAY_NO_TRADE, INTRADAY_RANGE, INTRADAY_TRANSITION,
    INTRADAY_TREND_DOWN, INTRADAY_TREND_UP, INTRADAY_UNKNOWN,
    INTRADAY_VOLATILITY_COMPRESSION, INTRADAY_VOLATILITY_EXPANSION,
)

from .families import MEAN_REVERSION, TREND_FOLLOWING

NO_TRADE = "NO_TRADE"

# Charter rule, quoted verbatim in this phase's own spec: "TRANSITION
# = No strategy initially. Capital preservation state." Extended here,
# disclosed rather than silently assumed, to every other regime this
# phase's two families were never validated against (Phase 20.1C only
# validated TREND vs RANGE separation -- VOLATILITY_EXPANSION/
# COMPRESSION/UNKNOWN/BREAKOUT_ATTEMPT/NO_TRADE were never part of
# that validation's own PASS finding).
ELIGIBILITY_MATRIX = {
    INTRADAY_TREND_UP: TREND_FOLLOWING.name,
    INTRADAY_TREND_DOWN: TREND_FOLLOWING.name,
    INTRADAY_RANGE: MEAN_REVERSION.name,
    INTRADAY_TRANSITION: NO_TRADE,
    INTRADAY_VOLATILITY_EXPANSION: NO_TRADE,
    INTRADAY_VOLATILITY_COMPRESSION: NO_TRADE,
    INTRADAY_BREAKOUT_ATTEMPT: NO_TRADE,
    INTRADAY_NO_TRADE: NO_TRADE,
    INTRADAY_UNKNOWN: NO_TRADE,
}


def eligible_family_for_regime(regime: str) -> str:
    """Never raises on an unrecognized label -- degrades to NO_TRADE,
    the same capital-preservation default as every disclosed-unready
    regime above, rather than assuming a family."""
    return ELIGIBILITY_MATRIX.get(regime, NO_TRADE)

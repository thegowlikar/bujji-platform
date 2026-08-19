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
# Every shape this selector may name. Widened 2026-08-19 with the three-part
# regime rewrite; IRON_CONDOR/IRON_FLY remain constructible families but are
# no longer reachable from this table, so they are not in the universe it can
# select. Kept as a single declared tuple so "what may Bujji sell?" has one
# answer rather than being inferred from branch bodies.
SELLING_UNIVERSE = (
    "VOLATILITY_COMPRESSION",    # short straddle  -- sideways, high vol
    "NEUTRAL_PREMIUM_SELLING",   # short strangle  -- sideways, thin premium
    "BULL_PUT_SPREAD",           # credit spread   -- up-trend
    "BEAR_CALL_SPREAD",          # credit spread   -- down-trend
)

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
# ---------------------------------------------------------------------------
# THREE-PART REGIME SELECTION (operator directive, 2026-08-19)
# ---------------------------------------------------------------------------
# The market does one of three things, and a volatility seller should have a
# sellable shape for each:
#
#   UP        -> BULL_PUT_SPREAD    credit spread leaning with the trend
#   DOWN      -> BEAR_CALL_SPREAD   its mirror
#   SIDEWAYS  -> SHORT_STRADDLE or SHORT_STRANGLE, by volatility
#
# WHAT THIS REPLACED, and why. The previous table had exactly two tradeable
# outcomes -- IRON_CONDOR and IRON_FLY, both neutral -- so a TRENDING market
# always resolved to no-trade. Its own reasoning said a directional regime had
# "no Gate-B-approved defined-risk SELLING strategy", which was true only
# because no directional credit spread existed in the construction engine.
# Two were added (bujji/msi_trade_construction), so the refusal no longer
# describes reality and the trending third of the market becomes tradeable.
#
# STRADDLE vs STRANGLE is a judgement, so it is CONFIG, not a hardcoded rule:
#   HIGH_VOL              -> straddle. Premium at the money is rich enough to
#                            pay for sitting on the gamma.
#   LOW_VOL / CONTRACTION -> strangle. Premium is thin, so distance from the
#                            money is worth more than the extra credit.
# Invert SIDEWAYS_SHAPE_BY_VOLATILITY to trade the opposite opinion; plenty of
# sellers argue it the other way, and neither is a fact about the market.
#
# RISK POSTURE CHANGED HERE, deliberately and with operator approval on
# 2026-08-19. Every previously selectable family (IRON_CONDOR, IRON_FLY) was
# structurally DEFINED-risk. A short straddle and a short strangle carry naked
# short legs with UNBOUNDED loss -- taxonomy.UNDEFINED_RISK_FAMILIES says so.
# The two credit spreads remain defined-risk. What still protects the book:
# the real SPAN margin veto at Gate B, the capital check, portfolio limits,
# the daily loss limit, the emergency brake and the mandatory 15:15 exit.
# What no longer protects it: the SHAPE. On a gap, a naked strangle's loss is
# bounded by the exit policy firing, not by the position's own structure.
#
# UNCHANGED, on purpose: every no-trade path. Missing or unknown regime,
# volatility expansion, and any unmapped combination still fail closed. This
# widened what Bujji can sell; it did not widen when Bujji is willing to sell.

# The family names below MUST exist in
# msi_trade_construction.taxonomy.SUPPORTED_FAMILIES -- a selector that picks
# a shape the engine cannot build produces a guaranteed rejection instead of a
# trade. tests/test_three_part_strategy_selection.py asserts the linkage.
FAMILY_SHORT_STRADDLE = "VOLATILITY_COMPRESSION"   # sell CE+PE, both ATM
FAMILY_SHORT_STRANGLE = "NEUTRAL_PREMIUM_SELLING"  # sell CE+PE, delta-targeted
FAMILY_BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
FAMILY_BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"

SIDEWAYS_SHAPE_BY_VOLATILITY = {
    VOL_HIGH: FAMILY_SHORT_STRADDLE,
    VOL_LOW: FAMILY_SHORT_STRANGLE,
    VOL_CONTRACTION: FAMILY_SHORT_STRANGLE,
}


def select_strategy(trend_regime: Optional[str], volatility_regime: Optional[str], clock: Clock) -> StrategySelectionResult:
    now = clock()

    # 1. NO OPINION -> NO TRADE. Unchanged: an absent or unrecognised regime
    #    is not a neutral market, it is an unknown one.
    if (trend_regime is None or volatility_regime is None
            or trend_regime == TREND_UNKNOWN or volatility_regime == VOL_UNKNOWN):
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime or "MISSING",
            volatility_regime=volatility_regime or "MISSING",
            reasoning="Market regime unavailable or unrecognized -- fail closed, no trade today.",
            confidence="NONE", evaluated_at=now,
        )

    # 2. VOLATILITY EXPANSION VETOES EVERYTHING. Checked BEFORE direction:
    #    selling into rising volatility is a bad trade in a trend exactly as
    #    it is in a range, and this ordering makes that non-negotiable rather
    #    than something a directional branch could reach around.
    if volatility_regime == VOL_EXPANSION:
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning="Volatility expanding -- premium-selling risk/reward is unfavorable while vol "
                      "is still rising; wait for stabilization rather than sell into expansion. "
                      "Applies in every regime, including a trending one.",
            confidence="NONE", evaluated_at=now,
        )

    # 3. SIDEWAYS -- the most common state, and the one premium selling is for.
    if trend_regime == TREND_SIDEWAYS:
        family = SIDEWAYS_SHAPE_BY_VOLATILITY.get(volatility_regime)
        if family is None:
            return StrategySelectionResult(
                selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
                reasoning=f"Range-bound market with unmapped volatility regime "
                          f"({volatility_regime}) -- fail closed rather than guess a shape.",
                confidence="NONE", evaluated_at=now,
            )
        shape = "short straddle (ATM both legs)" if family == FAMILY_SHORT_STRADDLE \
            else "short strangle (delta-targeted both legs)"
        rationale = ("premium at the money is rich enough to pay for the gamma"
                     if family == FAMILY_SHORT_STRADDLE
                     else "premium is thin, so distance from the money is worth more than the credit")
        return StrategySelectionResult(
            selected_strategy=family, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning=f"Range-bound market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"{shape}: {rationale}. NAKED short legs: loss is not bounded by the shape, "
                      f"only by the exit policy and the risk gates.",
            confidence="HIGH", evaluated_at=now,
        )

    # 4. TRENDING -- sell against the trend's own direction, with protection.
    if trend_regime == TREND_TRENDING_UP:
        return StrategySelectionResult(
            selected_strategy=FAMILY_BULL_PUT_SPREAD, trend_regime=trend_regime,
            volatility_regime=volatility_regime,
            reasoning=f"Up-trending market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"bull put spread: sell the put and buy a lower put, collecting a credit that "
                      f"profits if price rises, stalls, or falls less than the short strike. "
                      f"Defined risk: max loss capped by the long leg.",
            confidence="HIGH", evaluated_at=now,
        )

    if trend_regime == TREND_TRENDING_DOWN:
        return StrategySelectionResult(
            selected_strategy=FAMILY_BEAR_CALL_SPREAD, trend_regime=trend_regime,
            volatility_regime=volatility_regime,
            reasoning=f"Down-trending market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"bear call spread: sell the call and buy a higher call, collecting a credit "
                      f"that profits if price falls, stalls, or rises less than the short strike. "
                      f"Defined risk: max loss capped by the long leg.",
            confidence="HIGH", evaluated_at=now,
        )

    # 5. ANYTHING ELSE -> NO TRADE. Unchanged.
    return StrategySelectionResult(
        selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
        reasoning=f"Regime combination ({trend_regime}, {volatility_regime}) has no mapped selling "
                  f"strategy in this table -- fail closed rather than guess.",
        confidence="NONE", evaluated_at=now,
    )

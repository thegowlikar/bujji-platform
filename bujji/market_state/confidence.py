"""Confidence -- Shadow Campaign v2 Phase 3C.

Evidence-based confidence only: HIGH when multiple independent real
sources genuinely agree; LOW/UNKNOWN when data is missing; explicitly
LOWERED (never silently ignored) when real sources disagree. This
module NEVER predicts a market direction or scores a probability -- it
only describes how well-supported the current MarketState description
is, and always discloses WHY via `uncertainties`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

CONFIDENCE_UNKNOWN = "UNKNOWN"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"


def _direction_from_price_structure(price_structure) -> Optional[str]:
    """price_structure.trend_direction_signal is a real field ("UP"/
    "DOWN"/None) -- never inferred from trend_state, which describes
    trend MATURITY (EMERGING/ESTABLISHED/...), not direction."""
    if price_structure is None:
        return None
    return price_structure.trend_direction_signal


def _direction_from_market_structure(market_structure) -> Optional[str]:
    if market_structure is None:
        return None
    breakout = market_structure.breakout_state == "CONFIRMED"
    breakdown = market_structure.breakdown_state == "CONFIRMED"
    if breakout and not breakdown:
        return "UP"
    if breakdown and not breakout:
        return "DOWN"
    return None  # both/neither confirmed -- no real directional read, never guessed


def _direction_from_positioning(participant_positioning) -> Optional[str]:
    if participant_positioning is None:
        return None
    if participant_positioning.positioning_bias == "BULLISH_POSITIONING":
        return "UP"
    if participant_positioning.positioning_bias == "BEARISH_POSITIONING":
        return "DOWN"
    return None


def compute_confidence(
    intelligence_snapshot: Dict[str, Any], market_state_assessment,
) -> Tuple[str, Tuple[str, ...]]:
    """Returns (overall_confidence, uncertainties). Never fabricates a
    HIGH confidence from missing/conflicting data; never resolves a
    genuine disagreement between two real sources -- only reports it,
    exactly as this phase's own hard rule requires."""
    uncertainties: List[str] = []

    if "regime" not in intelligence_snapshot:
        uncertainties.append("regime unavailable (no spot candle history)")
    if "volatility" not in intelligence_snapshot:
        uncertainties.append("volatility unavailable (no active position -- a documented Phase 2 limitation)")
    liquidity = intelligence_snapshot.get("liquidity")
    if liquidity is None or liquidity.get("data_quality") == "INSUFFICIENT":
        uncertainties.append("liquidity data insufficient or unavailable")
    if market_state_assessment.price_structure is None:
        uncertainties.append("price structure unavailable (no episodes/events yet)")
    if market_state_assessment.market_structure is None:
        uncertainties.append("market structure unavailable (no episodes/events yet)")
    if market_state_assessment.participant_positioning is None:
        uncertainties.append("participant positioning unavailable (no option chain this cycle)")
    if not market_state_assessment.episodes:
        uncertainties.append("no active episodes -- insufficient history for structural confidence")

    directions = [
        _direction_from_price_structure(market_state_assessment.price_structure),
        _direction_from_market_structure(market_state_assessment.market_structure),
        _direction_from_positioning(market_state_assessment.participant_positioning),
    ]
    real_directions = [d for d in directions if d is not None]
    agreeing = 0
    if len(real_directions) >= 2:
        distinct = set(real_directions)
        if len(distinct) > 1:
            uncertainties.append(
                f"conflicting directional signals across independent sources: {sorted(real_directions)} "
                "-- reported as-is, not resolved"
            )
        else:
            agreeing = len(real_directions)

    has_any_structural_input = (
        market_state_assessment.price_structure is not None
        or market_state_assessment.market_structure is not None
        or market_state_assessment.participant_positioning is not None
    )

    if len(real_directions) >= 2 and agreeing == len(real_directions) and not uncertainties:
        overall = CONFIDENCE_HIGH
    elif len(real_directions) >= 2 and agreeing == len(real_directions):
        overall = CONFIDENCE_MODERATE
    elif len(real_directions) >= 2 and agreeing == 0:
        overall = CONFIDENCE_LOW  # genuine multi-source conflict
    elif has_any_structural_input:
        overall = CONFIDENCE_LOW  # some real data, but not enough independent agreement
    else:
        overall = CONFIDENCE_UNKNOWN  # no structural data at all

    return overall, tuple(uncertainties)

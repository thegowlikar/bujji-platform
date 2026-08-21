"""Adaptive Risk Recommendation -- BUJJI Options OS v3, Numeric Risk
Governor Gate D.5, Part 5.

PURPOSE: turn a StrategyExperience (Part 4's pure aggregation over
AdaptiveRiskMemory) into a deterministic sizing-adjustment suggestion.
This is NOT machine learning, NOT a probability model, NOT
reinforcement learning, NOT an optimizer -- it is a fixed, documented
if/elif rule table over already-computed statistics. No hidden
scoring: every threshold used below is a named module-level constant,
and the rule that fired is always recorded on the returned object.

Advisory only -- nothing here ever touches sizing directly. Gate D.4
(and D.1-D.3 beneath it) still make the actual admission/sizing
decision; this recommendation is one more input adaptive_risk_
governor.py may apply on top, never a replacement for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .adaptive_risk_memory import StrategyExperience

RECOMMENDATION_NO_CHANGE = "NO_CHANGE"
RECOMMENDATION_REDUCE_SIZE_10 = "REDUCE_SIZE_10"
RECOMMENDATION_REDUCE_SIZE_20 = "REDUCE_SIZE_20"
RECOMMENDATION_REDUCE_SIZE_30 = "REDUCE_SIZE_30"
RECOMMENDATION_INCREASE_SIZE_10 = "INCREASE_SIZE_10"
RECOMMENDATION_INCREASE_SIZE_20 = "INCREASE_SIZE_20"
RECOMMENDATION_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# Multiplicative sizing factor per recommendation -- the ONLY place
# this mapping exists, reused identically by both this module's own
# explanation text and adaptive_risk_governor.py's actual size
# calculation, so the two can never silently disagree.
SIZE_ADJUSTMENT_FACTOR = {
    RECOMMENDATION_NO_CHANGE: 1.00,
    RECOMMENDATION_REDUCE_SIZE_10: 0.90,
    RECOMMENDATION_REDUCE_SIZE_20: 0.80,
    RECOMMENDATION_REDUCE_SIZE_30: 0.70,
    RECOMMENDATION_INCREASE_SIZE_10: 1.10,
    RECOMMENDATION_INCREASE_SIZE_20: 1.20,
    RECOMMENDATION_INSUFFICIENT_DATA: 1.00,   # no reliable evidence -> no adjustment, not a guess
}

# Documented, named thresholds -- the complete rule table. Nothing
# below this point references an unnamed/inline number.
MIN_SAMPLE_SIZE = 10

WIN_RATE_VERY_HIGH = 0.75
WIN_RATE_HIGH = 0.60
WIN_RATE_LOW = 0.40
WIN_RATE_VERY_LOW = 0.25

DRAWDOWN_ACCEPTABLE_FRACTION = 0.05   # <= 5% average drawdown
DRAWDOWN_LARGE_FRACTION = 0.08         # >= 8% average drawdown
DRAWDOWN_SEVERE_FRACTION = 0.12         # >= 12% average drawdown


@dataclass(frozen=True)
class AdaptiveRiskRecommendation:
    recommendation: str
    rule_applied: str
    explanation: str
    based_on: StrategyExperience


def recommend_adaptive_risk_adjustment(experience: StrategyExperience) -> AdaptiveRiskRecommendation:
    """Rule priority, checked in this order, first match wins -- every
    branch documented, matching the task brief's own worked rule
    table exactly:

      1. sample_size < MIN_SAMPLE_SIZE
           -> INSUFFICIENT_DATA (not enough history to say anything).
      2. win_rate >= WIN_RATE_VERY_HIGH AND average_drawdown <= DRAWDOWN_ACCEPTABLE_FRACTION
           -> INCREASE_SIZE_20 (strong, low-drawdown edge).
      3. win_rate >= WIN_RATE_HIGH AND average_drawdown <= DRAWDOWN_ACCEPTABLE_FRACTION
           -> INCREASE_SIZE_10 (win rate high AND drawdown acceptable).
      4. average_drawdown >= DRAWDOWN_SEVERE_FRACTION OR win_rate <= WIN_RATE_VERY_LOW
           -> REDUCE_SIZE_30 (consistent poor performance).
      5. average_drawdown >= DRAWDOWN_LARGE_FRACTION
           -> REDUCE_SIZE_20 (drawdown repeatedly large).
      6. win_rate <= WIN_RATE_LOW
           -> REDUCE_SIZE_10 (win rate meaningfully below even).
      7. otherwise
           -> NO_CHANGE."""
    stats_line = (
        f"regime={experience.market_regime!r} samples={experience.sample_size} "
        f"win_rate={_fmt_pct(experience.win_rate)} "
        f"avg_drawdown={_fmt_pct(experience.average_drawdown)} "
        f"avg_profit={_fmt_pct(experience.average_profit)}"
    )

    if experience.sample_size < MIN_SAMPLE_SIZE:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_INSUFFICIENT_DATA,
            rule_applied=f"sample_size({experience.sample_size}) < MIN_SAMPLE_SIZE({MIN_SAMPLE_SIZE})",
            explanation=(
                f"{stats_line}. Sample size below the minimum of {MIN_SAMPLE_SIZE} required "
                f"observations -- no adjustment recommended."
            ),
            based_on=experience,
        )

    win_rate = experience.win_rate if experience.win_rate is not None else 0.0
    average_drawdown = experience.average_drawdown if experience.average_drawdown is not None else 0.0

    if win_rate >= WIN_RATE_VERY_HIGH and average_drawdown <= DRAWDOWN_ACCEPTABLE_FRACTION:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_INCREASE_SIZE_20,
            rule_applied=(
                f"win_rate({win_rate:.2f}) >= WIN_RATE_VERY_HIGH({WIN_RATE_VERY_HIGH}) AND "
                f"avg_drawdown({average_drawdown:.2f}) <= DRAWDOWN_ACCEPTABLE_FRACTION({DRAWDOWN_ACCEPTABLE_FRACTION})"
            ),
            explanation=f"{stats_line}. Very high win rate with acceptable drawdown -- increase size 20%.",
            based_on=experience,
        )

    if win_rate >= WIN_RATE_HIGH and average_drawdown <= DRAWDOWN_ACCEPTABLE_FRACTION:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_INCREASE_SIZE_10,
            rule_applied=(
                f"win_rate({win_rate:.2f}) >= WIN_RATE_HIGH({WIN_RATE_HIGH}) AND "
                f"avg_drawdown({average_drawdown:.2f}) <= DRAWDOWN_ACCEPTABLE_FRACTION({DRAWDOWN_ACCEPTABLE_FRACTION})"
            ),
            explanation=f"{stats_line}. High win rate with acceptable drawdown -- increase size 10%.",
            based_on=experience,
        )

    if average_drawdown >= DRAWDOWN_SEVERE_FRACTION or win_rate <= WIN_RATE_VERY_LOW:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_REDUCE_SIZE_30,
            rule_applied=(
                f"avg_drawdown({average_drawdown:.2f}) >= DRAWDOWN_SEVERE_FRACTION({DRAWDOWN_SEVERE_FRACTION}) OR "
                f"win_rate({win_rate:.2f}) <= WIN_RATE_VERY_LOW({WIN_RATE_VERY_LOW})"
            ),
            explanation=f"{stats_line}. Consistent poor performance -- reduce size 30%.",
            based_on=experience,
        )

    if average_drawdown >= DRAWDOWN_LARGE_FRACTION:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_REDUCE_SIZE_20,
            rule_applied=f"avg_drawdown({average_drawdown:.2f}) >= DRAWDOWN_LARGE_FRACTION({DRAWDOWN_LARGE_FRACTION})",
            explanation=f"{stats_line}. Drawdown repeatedly large -- reduce size 20%.",
            based_on=experience,
        )

    if win_rate <= WIN_RATE_LOW:
        return AdaptiveRiskRecommendation(
            recommendation=RECOMMENDATION_REDUCE_SIZE_10,
            rule_applied=f"win_rate({win_rate:.2f}) <= WIN_RATE_LOW({WIN_RATE_LOW})",
            explanation=f"{stats_line}. Win rate below acceptable level -- reduce size 10%.",
            based_on=experience,
        )

    return AdaptiveRiskRecommendation(
        recommendation=RECOMMENDATION_NO_CHANGE,
        rule_applied="no threshold breached",
        explanation=f"{stats_line}. No rule threshold breached -- no size adjustment recommended.",
        based_on=experience,
    )


def _fmt_pct(value):
    return f"{value:.2%}" if value is not None else "N/A"

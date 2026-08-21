"""Adaptive Risk Governor -- BUJJI Options OS v3, Numeric Risk Governor
Gate D.5, Parts 6-7 (Integration + Explanation).

PURPOSE: combine D.1-D.4's already-computed decisions with Part 5's
AdaptiveRiskRecommendation into one final suggested size. D.5 NEVER
overrides a BLOCKED/REJECTED outcome from D.1-D.4 -- it only ever
adjusts sizing on trades that were already going to be admitted.

ONE-WAY DEPENDENCY, matching every prior Gate D phase's discipline:
this module imports only plain status-string CONSTANTS from D.1/D.3/
D.4 (SAFETY_BLOCKED, BUDGET_REJECTED, ACTION_BLOCK_NEW_RISK) -- never
their decision functions (evaluate_trade_capital_safety,
assess_trade_risk_budget, recommend_risk_action, etc). Callers are
expected to have already run D.1-D.4 and pass their resulting status
strings in as plain data; this module never re-derives or second-
guesses those decisions, it only reads whether they represent a block.

RESOLVING THE "NEVER EXCEEDS D.3" REQUIREMENT AGAINST INCREASE_SIZE_*
RECOMMENDATIONS: the task brief requires both that D.5 can recommend
INCREASE_SIZE_10/20 (Part 5) AND that "adaptive sizing never exceeds
D.3" (Part 8 safety requirement / Part 10 adversarial check #4). These
are only compatible if "D.3" here means D.3's own hard ceiling --
risk_budget_governor.PositionSizeRecommendation.maximum_quantity (the
largest quantity the account's risk budget structurally permits right
now) -- not D.3's recommended_quantity (which is often already smaller
than that ceiling, e.g. because the desired_quantity requested upstream
was itself modest). An INCREASE_SIZE_* recommendation can therefore
raise the final size above D.3's recommended_quantity, using headroom
D.3 itself already certified as safe, but this function clamps the
result to maximum_quantity unconditionally -- it can never manufacture
size beyond what D.3 already proved the account can carry. Callers
that only have a single D.3 figure available should pass it as BOTH
base_size and maximum_size; the clamp then degrades to "never exceeds
what D.3 recommended," the strictest reading of the requirement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .adaptive_risk_memory import RiskMemoryEntry
from .adaptive_risk_recommendation import SIZE_ADJUSTMENT_FACTOR, AdaptiveRiskRecommendation
from .capital_safety_governor import SAFETY_BLOCKED
from .position_lifecycle_intelligence import ACTION_BLOCK_NEW_RISK
from .risk_budget_governor import BUDGET_REJECTED

# Any admission_status equal to one of these is an unconditional block
# -- D.5 must never touch sizing on these, only pass the block through.
BLOCKING_ADMISSION_STATUSES = frozenset({SAFETY_BLOCKED, BUDGET_REJECTED, ACTION_BLOCK_NEW_RISK, "BLOCKED", "REJECTED"})


class IllegalAdaptiveGovernorInputError(Exception):
    """Raised on a structurally impossible input (negative size) --
    never silently normalized."""


@dataclass(frozen=True)
class AdaptiveGovernorDecision:
    admission_status: str
    is_blocked: bool
    base_size: int
    maximum_size: int
    adaptive_recommendation: str
    final_suggested_size: int
    reason: str


def evaluate_adaptive_governor_decision(
    admission_status: str, base_size: int, maximum_size: Optional[int],
    adaptive_recommendation: AdaptiveRiskRecommendation,
) -> AdaptiveGovernorDecision:
    if base_size < 0:
        raise IllegalAdaptiveGovernorInputError(f"base_size must be non-negative, got {base_size!r}")
    effective_maximum = maximum_size if maximum_size is not None else base_size
    if effective_maximum < 0:
        raise IllegalAdaptiveGovernorInputError(f"maximum_size must be non-negative, got {maximum_size!r}")

    is_blocked = admission_status in BLOCKING_ADMISSION_STATUSES

    if is_blocked:
        return AdaptiveGovernorDecision(
            admission_status=admission_status, is_blocked=True, base_size=base_size,
            maximum_size=effective_maximum, adaptive_recommendation=adaptive_recommendation.recommendation,
            final_suggested_size=0,
            reason=(
                f"Admission status {admission_status!r} is a blocking status. Adaptive Risk Memory "
                f"never overrides a block from D.1-D.4 -- final suggested size is 0 regardless of the "
                f"adaptive recommendation ({adaptive_recommendation.recommendation})."
            ),
        )

    factor = SIZE_ADJUSTMENT_FACTOR[adaptive_recommendation.recommendation]
    adjusted_size = math.floor(base_size * factor)
    final_suggested_size = max(0, min(adjusted_size, effective_maximum))

    reason = (
        f"Admission APPROVED ({admission_status}). Base size {base_size} lots. Adaptive recommendation "
        f"{adaptive_recommendation.recommendation} (factor {factor:.2f}) -> {adjusted_size} lots, "
        f"clamped to D.3 maximum of {effective_maximum} lots -> final suggested size "
        f"{final_suggested_size} lots. {adaptive_recommendation.explanation}"
    )

    return AdaptiveGovernorDecision(
        admission_status=admission_status, is_blocked=False, base_size=base_size,
        maximum_size=effective_maximum, adaptive_recommendation=adaptive_recommendation.recommendation,
        final_suggested_size=final_suggested_size, reason=reason,
    )

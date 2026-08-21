"""Risk Budget Allocation & Position Sizing Governor — BUJJI Options OS
v3, Numeric Risk Governor Gate D.3.

PURPOSE: D.1 (capital_safety_governor.py) answers "is this account safe
right now." D.2 (portfolio_risk_aggregator.py) answers "what is the
true whole-book risk right now." Neither answers "how much ADDITIONAL
risk can this portfolio safely accept" -- that is the missing decision
layer this module builds. Its output is a RECOMMENDATION downstream
systems MAY use later; this module places no trade, calls no broker,
and performs no automatic sizing in any live path -- it only
calculates allowed risk and a proportional size recommendation given
that budget.

NOT A COMPETING RISK MODEL -- VERIFIED BEFORE WRITING ANYTHING:
bujji/trading_brain/position_sizing/engine.py::size_position() already
exists, from an earlier generation (MSI trade construction). Its own
docstring explicitly scopes it as a FIXED LOOKUP TABLE -- quantity
comes solely from PositionSizingConfig's finite lot table keyed by
capital_intent, and it states outright that it "never calculates
broker margin, never models exposure, never applies leverage." This
module's whole purpose -- dynamic sizing driven by real margin/
exposure/portfolio-risk figures from Gate B/C/D.1/D.2 -- is a
genuinely different, new capability that module explicitly disclaims.
This file does not import, call, or modify that module in any way.

capital_check.assess_capital() (Gate B) remains completely untouched
and unimported, exactly as it has been by every prior Gate C/D phase.
This module is a further, additional intelligence layer, never a
replacement for the actual ALLOW/VETO authority.

DEPENDENCY DIRECTION (one-way, mirroring D.2's own established
discipline): this module imports CapitalSafetySnapshot and
PortfolioRiskSnapshot as plain data inputs; it never imports or calls
evaluate_trade_capital_safety/classify_capital_safety (D.1) or
aggregate_portfolio_risk/classify_portfolio_risk (D.2) -- callers are
responsible for having already computed those upstream, exactly as D.2
did not recompute anything from Gate C either."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from bujji.trading_brain.risk_governor.capital_safety_governor import (
    SAFETY_BLOCKED,
    SAFETY_RESTRICTED,
    CapitalSafetySnapshot,
)
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import PortfolioRiskSnapshot

Clock = Callable[[], datetime]

BUDGET_APPROVED = "APPROVED"
BUDGET_APPROVED_WITH_WARNING = "APPROVED_WITH_WARNING"
BUDGET_REDUCED_SIZE_REQUIRED = "REDUCED_SIZE_REQUIRED"
BUDGET_REJECTED = "REJECTED"


class IllegalRiskBudgetInputError(Exception):
    """Raised on negative or otherwise nonsensical risk inputs -- a
    caller passing a negative "requested risk" for a risk-INCREASING
    trade is a malformed input, never silently treated as extra
    budget headroom."""


# --------------------------------------------------------------------- #
# Part 1 -- Risk Budget Model
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskPolicy:
    """The one policy knob this module needs -- explicitly configurable,
    illustrative default only, matching this whole Governor's
    established discipline."""

    max_portfolio_risk_fraction: float = 0.05     # 5% of total_capital, matching the task's own worked example
    warning_utilization_fraction: float = 0.80     # 80% of the budget itself triggers APPROVED_WITH_WARNING


@dataclass(frozen=True)
class RiskBudgetSnapshot:
    """OBSERVED/composed fields only where directly sourced; DERIVED
    fields computed once, here, from those -- never accepted as direct
    input. Any missing upstream figure (total_capital, current_risk_used)
    leaves the corresponding field explicitly None, never a silent 0.0
    (a 0.0 "current risk used" would misrepresent an unknown book as an
    empty one)."""

    # Account
    total_capital: Optional[float]
    current_risk_used: Optional[float]
    available_risk_budget: Optional[float]      # maximum_risk_budget - current_risk_used; CAN be negative (over budget)
    maximum_risk_budget: Optional[float]          # total_capital * risk_policy.max_portfolio_risk_fraction

    # Portfolio
    current_margin_usage: Optional[float]          # fraction, from the capital safety snapshot
    current_drawdown: Optional[float]                # fraction, from the capital safety snapshot
    current_risk_status: Optional[str]                # D.1's SAFE/CAUTION/RESTRICTED/BLOCKED

    # Derived
    remaining_risk_capacity: Optional[float]          # available_risk_budget, floored at 0, AND
                                                        # forced to 0 if current_risk_status is BLOCKED/RESTRICTED
    utilization_percent: Optional[float]                # current_risk_used / maximum_risk_budget

    timestamp: datetime


# --------------------------------------------------------------------- #
# Part 2 -- Risk Budget Calculation Engine
# --------------------------------------------------------------------- #

def calculate_available_risk_budget(
    capital_safety_snapshot: CapitalSafetySnapshot,
    portfolio_risk_snapshot: PortfolioRiskSnapshot,
    risk_policy: RiskPolicy,
    current_risk_status: Optional[str],
    clock: Clock,
) -> RiskBudgetSnapshot:
    """`current_risk_status` is the caller-supplied result of D.1's own
    classify_capital_safety() (this module never calls that function
    itself -- see module docstring's dependency-direction note), so
    the BLOCKED/RESTRICTED invariant below can be enforced without
    re-deriving D.1's own classification logic.

    current_risk_used is sourced from portfolio_risk_snapshot.
    total_max_loss (D.2's whole-book aggregation) in preference to
    capital_safety_snapshot.open_risk -- D.2 is the more precise,
    dedicated aggregation layer; capital_safety_snapshot supplies the
    account-level facts (total_capital, margin usage, drawdown) that
    are NOT derivable from portfolio state at all."""
    total_capital = capital_safety_snapshot.total_capital
    current_risk_used = portfolio_risk_snapshot.total_max_loss

    maximum_risk_budget = (
        total_capital * risk_policy.max_portfolio_risk_fraction if total_capital is not None else None
    )
    available_risk_budget = (
        maximum_risk_budget - current_risk_used
        if maximum_risk_budget is not None and current_risk_used is not None else None
    )
    utilization_percent = (
        current_risk_used / maximum_risk_budget
        if current_risk_used is not None and maximum_risk_budget not in (None, 0) else None
    )

    remaining_risk_capacity = None
    if available_risk_budget is not None:
        remaining_risk_capacity = max(0.0, available_risk_budget)
        if current_risk_status in (SAFETY_BLOCKED, SAFETY_RESTRICTED):
            # Part 8: an existing BLOCKED/RESTRICTED capital state cannot be
            # overridden by raw arithmetic headroom -- RESTRICTED per D.1's
            # own admission rule only ever allows risk-REDUCING trades, so
            # there is no capacity for NEW risk-increasing trades even if
            # the budget subtraction alone would suggest otherwise.
            remaining_risk_capacity = 0.0

    current_margin_usage = None
    current_drawdown = None
    if capital_safety_snapshot.used_margin is not None and capital_safety_snapshot.total_capital not in (None, 0):
        current_margin_usage = capital_safety_snapshot.used_margin / capital_safety_snapshot.total_capital
    if (
        capital_safety_snapshot.peak_capital is not None and capital_safety_snapshot.total_capital is not None
        and capital_safety_snapshot.peak_capital > 0
    ):
        current_drawdown = (capital_safety_snapshot.peak_capital - capital_safety_snapshot.total_capital) / capital_safety_snapshot.peak_capital

    return RiskBudgetSnapshot(
        total_capital=total_capital, current_risk_used=current_risk_used,
        available_risk_budget=available_risk_budget, maximum_risk_budget=maximum_risk_budget,
        current_margin_usage=current_margin_usage, current_drawdown=current_drawdown,
        current_risk_status=current_risk_status, remaining_risk_capacity=remaining_risk_capacity,
        utilization_percent=utilization_percent, timestamp=clock(),
    )


# --------------------------------------------------------------------- #
# Part 3 -- Strategy Risk Allocation. Reuses the EXACT strategy-family
# vocabulary already established in msi_entry_bridge.py's own
# _MSI_FORMULA_SPECS (the seven wired formulas) plus the six explicitly
# permanently-vetoed families -- never a new naming scheme. Pure
# classification, never a return prediction of any kind.
# --------------------------------------------------------------------- #

RISK_CATEGORY_DEFINED_RISK = "DEFINED_RISK"
RISK_CATEGORY_UNDEFINED_RISK = "UNDEFINED_RISK"
RISK_CATEGORY_UNKNOWN = "UNKNOWN"

# Matches this session's own Gate B wiring history exactly.
_DEFINED_RISK_STRATEGY_TYPES = frozenset({
    "LONG_DIRECTIONAL", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
    "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "CALENDAR",
})
_UNDEFINED_RISK_STRATEGY_TYPES = frozenset({
    "COVERED", "SHORT_DIRECTIONAL", "NEUTRAL_PREMIUM_SELLING",
    "VOLATILITY_COMPRESSION", "RATIO", "SYNTHETIC",
})


@dataclass(frozen=True)
class StrategyRiskAllocation:
    strategy_type: str
    risk_category: str              # DEFINED_RISK | UNDEFINED_RISK | UNKNOWN
    risk_amount: float                # the real max_loss figure for the proposed trade -- never a prediction


def classify_strategy_risk(strategy_type: str, risk_amount: float) -> StrategyRiskAllocation:
    if strategy_type in _DEFINED_RISK_STRATEGY_TYPES:
        category = RISK_CATEGORY_DEFINED_RISK
    elif strategy_type in _UNDEFINED_RISK_STRATEGY_TYPES:
        category = RISK_CATEGORY_UNDEFINED_RISK
    else:
        category = RISK_CATEGORY_UNKNOWN
    return StrategyRiskAllocation(strategy_type=strategy_type, risk_category=category, risk_amount=risk_amount)


# --------------------------------------------------------------------- #
# Part 4 -- Trade Risk Assessment
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskBudgetDecision:
    allowed: bool
    requested_risk: float
    available_budget: Optional[float]
    remaining_after_trade: Optional[float]
    utilization_after_trade: Optional[float]
    status: str                        # APPROVED | APPROVED_WITH_WARNING | REDUCED_SIZE_REQUIRED | REJECTED
    explanation: str
    evaluated_at: datetime


def assess_trade_risk_budget(
    portfolio_risk_snapshot: PortfolioRiskSnapshot,
    proposed_trade_risk: float,
    risk_budget_snapshot: RiskBudgetSnapshot,
    risk_policy: RiskPolicy,
    clock: Clock,
) -> RiskBudgetDecision:
    """Part 8 invariants, enforced unconditionally, no override
    parameter anywhere in this signature:
      - A negative proposed_trade_risk for what is being asserted as a
        risk-INCREASING trade is malformed input -- raises. A hedge
        (risk-REDUCING) is expressed by calling this with
        proposed_trade_risk <= 0 explicitly understood as a reduction;
        see the dedicated handling below.
      - Missing risk_budget_snapshot data (remaining_risk_capacity is
        None) -> REJECTED, never approved.
      - An already-BLOCKED/RESTRICTED status (remaining_risk_capacity
        already forced to 0 by calculate_available_risk_budget) ->
        REJECTED for any risk-increasing request.
      - Zero budget means zero additional risk -> REJECTED."""
    as_of = clock()

    if proposed_trade_risk < 0:
        # A genuine hedge/de-risking trade: always approved, never
        # scaled down (matches D.1's own "risk-reducing trades bypass
        # restriction" precedent) -- it can only ever IMPROVE the budget.
        return RiskBudgetDecision(
            allowed=True, requested_risk=proposed_trade_risk,
            available_budget=risk_budget_snapshot.remaining_risk_capacity,
            remaining_after_trade=(
                risk_budget_snapshot.remaining_risk_capacity - proposed_trade_risk
                if risk_budget_snapshot.remaining_risk_capacity is not None else None
            ),
            utilization_after_trade=None, status=BUDGET_APPROVED,
            explanation="Risk-reducing trade (hedge). Approved unconditionally; it improves available capacity.",
            evaluated_at=as_of,
        )

    if risk_budget_snapshot.remaining_risk_capacity is None:
        return RiskBudgetDecision(
            allowed=False, requested_risk=proposed_trade_risk, available_budget=None,
            remaining_after_trade=None, utilization_after_trade=None, status=BUDGET_REJECTED,
            explanation="Rejected. Risk budget data is unavailable -- cannot approve without it.",
            evaluated_at=as_of,
        )

    if risk_budget_snapshot.current_risk_status == SAFETY_BLOCKED:
        return RiskBudgetDecision(
            allowed=False, requested_risk=proposed_trade_risk,
            available_budget=risk_budget_snapshot.remaining_risk_capacity,
            remaining_after_trade=risk_budget_snapshot.remaining_risk_capacity,
            utilization_after_trade=None, status=BUDGET_REJECTED,
            explanation="Rejected. Account capital status is BLOCKED. No additional risk permitted.",
            evaluated_at=as_of,
        )

    remaining = risk_budget_snapshot.remaining_risk_capacity
    if remaining <= 0:
        return RiskBudgetDecision(
            allowed=False, requested_risk=proposed_trade_risk, available_budget=remaining,
            remaining_after_trade=remaining, utilization_after_trade=None, status=BUDGET_REJECTED,
            explanation="Rejected. Zero remaining risk budget. No additional risk permitted.",
            evaluated_at=as_of,
        )

    if proposed_trade_risk > remaining:
        reduced_fraction = remaining / proposed_trade_risk if proposed_trade_risk > 0 else 0.0
        return RiskBudgetDecision(
            allowed=False, requested_risk=proposed_trade_risk, available_budget=remaining,
            remaining_after_trade=remaining, utilization_after_trade=None, status=BUDGET_REDUCED_SIZE_REQUIRED,
            explanation=(
                f"Requested risk {proposed_trade_risk:.2f} exceeds remaining risk budget {remaining:.2f}. "
                f"Recommended size reduced to {reduced_fraction:.0%}."
            ),
            evaluated_at=as_of,
        )

    remaining_after_trade = remaining - proposed_trade_risk
    utilization_after_trade = (
        (risk_budget_snapshot.current_risk_used + proposed_trade_risk) / risk_budget_snapshot.maximum_risk_budget
        if risk_budget_snapshot.current_risk_used is not None and risk_budget_snapshot.maximum_risk_budget not in (None, 0)
        else None
    )
    if utilization_after_trade is not None and utilization_after_trade >= risk_policy.warning_utilization_fraction:
        return RiskBudgetDecision(
            allowed=True, requested_risk=proposed_trade_risk, available_budget=remaining,
            remaining_after_trade=remaining_after_trade, utilization_after_trade=utilization_after_trade,
            status=BUDGET_APPROVED_WITH_WARNING,
            explanation=(
                f"Requested risk {proposed_trade_risk:.2f}. Available risk budget {remaining:.2f}. "
                f"Portfolio utilization after trade {utilization_after_trade:.0%} -- approaching the configured limit."
            ),
            evaluated_at=as_of,
        )

    return RiskBudgetDecision(
        allowed=True, requested_risk=proposed_trade_risk, available_budget=remaining,
        remaining_after_trade=remaining_after_trade, utilization_after_trade=utilization_after_trade,
        status=BUDGET_APPROVED,
        explanation=(
            f"Requested risk {proposed_trade_risk:.2f}. Available risk budget {remaining:.2f}. "
            f"Portfolio utilization after trade {_format_pct(utilization_after_trade)}."
        ),
        evaluated_at=as_of,
    )


def _format_pct(value: Optional[float]) -> str:
    return f"{value:.0%}" if value is not None else "unavailable"


# --------------------------------------------------------------------- #
# Part 5 -- Position Size Recommendation
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionSizeRecommendation:
    recommended_quantity: int
    maximum_quantity: int
    reduction_required: bool
    reason: str


def calculate_safe_position_size(
    desired_quantity: int,
    requested_risk: float,
    risk_budget_snapshot: RiskBudgetSnapshot,
) -> PositionSizeRecommendation:
    """Part 8: requested size can never exceed the calculated maximum,
    enforced structurally by construction (recommended_quantity is
    always derived as a fraction of desired_quantity, never a separate
    figure that could exceed it). Zero budget means zero additional
    risk -- REJECTED (quantity 0), never a fabricated minimum size."""
    if desired_quantity < 0:
        raise IllegalRiskBudgetInputError(f"desired_quantity must be non-negative, got {desired_quantity!r}")
    if requested_risk < 0:
        raise IllegalRiskBudgetInputError(
            f"requested_risk must be non-negative for a position-size calculation "
            f"(a hedge/de-risking trade should be handled via assess_trade_risk_budget, "
            f"not sized down) -- got {requested_risk!r}"
        )

    if risk_budget_snapshot.remaining_risk_capacity is None:
        return PositionSizeRecommendation(
            recommended_quantity=0, maximum_quantity=desired_quantity, reduction_required=True,
            reason="Risk budget data is unavailable -- cannot size a position without it.",
        )

    available = risk_budget_snapshot.remaining_risk_capacity
    if available <= 0:
        return PositionSizeRecommendation(
            recommended_quantity=0, maximum_quantity=desired_quantity, reduction_required=True,
            reason="Zero remaining risk budget. No additional risk permitted.",
        )

    if requested_risk <= 0:
        # No risk consumed at all (a fully offsetting/free structure) -- full size, nothing to scale.
        return PositionSizeRecommendation(
            recommended_quantity=desired_quantity, maximum_quantity=desired_quantity,
            reduction_required=False, reason="Requested risk is zero. Full size approved.",
        )

    scale_fraction = min(1.0, available / requested_risk)
    recommended_quantity = math.floor(desired_quantity * scale_fraction)

    if recommended_quantity >= desired_quantity:
        return PositionSizeRecommendation(
            recommended_quantity=desired_quantity, maximum_quantity=desired_quantity,
            reduction_required=False,
            reason=f"Requested risk {requested_risk:.2f} fits within available budget {available:.2f}. Full size approved.",
        )

    return PositionSizeRecommendation(
        recommended_quantity=recommended_quantity, maximum_quantity=desired_quantity,
        reduction_required=True,
        reason=(
            f"Requested risk {requested_risk:.2f} exceeds available budget {available:.2f}. "
            f"Recommended size reduced to {scale_fraction:.0%} ({recommended_quantity} of {desired_quantity})."
        ),
    )

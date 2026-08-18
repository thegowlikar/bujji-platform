"""Phase 20.17 -- the bridge. Composes ONLY already-real, already-
tested functions from `bujji.trading_brain.risk_governor` (D.1
`capital_safety_governor`, D.2 `portfolio_risk_aggregator`, D.3
`risk_budget_governor`) -- nothing here recomputes any governor's own
admit/reject logic, exactly matching `risk_governor_pipeline.py`'s own
established "no new risk calculations" discipline.

SCOPE, disclosed here and in `RiskGovernorAssessment.skip_reason`: D.4
(`position_lifecycle_intelligence`, existing-position lifecycle) and
D.5 (`adaptive_risk_memory`/`adaptive_risk_governor`, adaptive
strategy experience) are NOT called. Both require real inputs Cycle 1
does not have -- D.4 needs a real, already-open `PositionRiskSnapshot`
(Cycle 1 has zero real positions); D.5 needs real historical risk-
memory entries (Cycle 1 has none). Constructing fake inputs for either
would be fabrication, not a bridge -- so this phase honestly evaluates
D.1-D.3 (the new-trade-admission stages) only, using a real, empty
`position_groups=[]` flat book (no real position groups exist to
aggregate) rather than inventing one.

KNOWN CURRENT LIMIT: D.2's real `aggregate_portfolio_risk` marks a flat
book `RISK_INVALID` (`INSUFFICIENT_PORTFOLIO_RISK_DATA`) because
`total_margin_required`/`total_max_loss` are `None` without a real
`MarginSnapshot` -- and a real `MarginSnapshot` itself requires a real
broker margin query against at least one real leg, which does not
exist for a flat book. This means `STATUS_ADMITTED` is not reachable
today with real Cycle-1 data; every real evaluation currently ends
`STATUS_BLOCKED` at `D.2_PORTFOLIO_RISK` once D.1 clears. This is
disclosed, not hidden -- see `docs/PHASE_20_17_RISK_GOVERNOR_BRIDGE_REPORT.md`.

Reads Phase 20.10's own `FinalDecision` and Phase 20.8's own
`AllocationAssessment` as plain inputs -- never recomputes either,
never calls `compose_decision()`/`assess_risk_allocation()`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bujji.decision_orchestration import BLOCKED as DECISION_BLOCKED, FinalDecision
from bujji.decision_orchestration import EXECUTABLE_CANDIDATE, WATCH
from bujji.trading_brain.risk_governor.capital_safety_governor import (
    CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect,
    evaluate_trade_capital_safety,
)
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import (
    RISK_INVALID, aggregate_portfolio_risk, classify_portfolio_risk, explain_portfolio_risk,
)
from bujji.trading_brain.risk_governor.risk_budget_governor import (
    RiskPolicy, assess_trade_risk_budget, calculate_available_risk_budget, calculate_safe_position_size,
)

from .models import (
    NOTIONAL_PROBE_DESIRED_QUANTITY, NOTIONAL_PROBE_MARGIN, NOTIONAL_PROBE_MAX_LOSS,
    RiskGovernorAssessment, STATUS_ADMITTED, STATUS_BLOCKED, STATUS_NOT_EVALUATED,
)

_WORTH_REVIEWING = (EXECUTABLE_CANDIDATE, WATCH)


def evaluate_risk_governor(
    final_decision: FinalDecision, capital_snapshot: CapitalSafetySnapshot, *, clock,
) -> RiskGovernorAssessment:
    """Only decisions Cycle 1 itself found worth further review
    (`EXECUTABLE_CANDIDATE`/`WATCH`) are ever sent to the real risk
    governor -- a `NO_OPPORTUNITY`/`BLOCKED`/`INSUFFICIENT_INTELLIGENCE`
    decision is never re-reviewed here (the real governor's own D.1-D.3
    admit/reject logic cannot rescue a decision Cycle 1's own evidence-
    based chain already rejected -- mirrors Phase 20.10's own Rule 3)."""
    strategy_name = final_decision.strategy_name or "UNKNOWN"

    if final_decision.decision_state not in _WORTH_REVIEWING:
        return RiskGovernorAssessment(
            strategy_name=strategy_name, final_status=STATUS_NOT_EVALUATED, blocking_stage=None,
        )

    thresholds = CapitalSafetyThresholds()
    effect = ProposedTradeEffect(additional_margin=NOTIONAL_PROBE_MARGIN, additional_max_loss=NOTIONAL_PROBE_MAX_LOSS)

    # -- D.1 Capital Safety -------------------------------------------------
    capital_decision = evaluate_trade_capital_safety(capital_snapshot, effect, thresholds, clock)
    if not capital_decision.allowed:
        return RiskGovernorAssessment(
            strategy_name=strategy_name, final_status=STATUS_BLOCKED, blocking_stage="D.1_CAPITAL_SAFETY",
            capital_status=capital_decision.status, capital_allowed=capital_decision.allowed,
            capital_explanation=capital_decision.explanation,
        )

    # -- D.2 Portfolio Risk (flat book -- Cycle 1 has zero real positions) --
    portfolio_snapshot = aggregate_portfolio_risk([], None, None, None, clock)
    portfolio_status, portfolio_reasons = classify_portfolio_risk(portfolio_snapshot, None)
    portfolio_explanation = explain_portfolio_risk(portfolio_snapshot, portfolio_status, portfolio_reasons)
    if portfolio_status == RISK_INVALID:
        return RiskGovernorAssessment(
            strategy_name=strategy_name, final_status=STATUS_BLOCKED, blocking_stage="D.2_PORTFOLIO_RISK",
            capital_status=capital_decision.status, capital_allowed=capital_decision.allowed,
            capital_explanation=capital_decision.explanation,
            portfolio_status=portfolio_status, portfolio_explanation=portfolio_explanation,
        )

    # -- D.3 Risk Budget --------------------------------------------------
    # current_risk_status = capital_decision.status (D.1's own AFTER-trade
    # status), matching risk_governor_pipeline.py's own established call
    # exactly -- not a separately-computed BEFORE-trade status.
    risk_policy = RiskPolicy()
    budget_snapshot = calculate_available_risk_budget(
        capital_snapshot, portfolio_snapshot, risk_policy, capital_decision.status, clock,
    )
    budget_decision = assess_trade_risk_budget(
        portfolio_snapshot, NOTIONAL_PROBE_MAX_LOSS, budget_snapshot, risk_policy, clock,
    )
    sizing = calculate_safe_position_size(NOTIONAL_PROBE_DESIRED_QUANTITY, NOTIONAL_PROBE_MAX_LOSS, budget_snapshot)

    if not budget_decision.allowed:
        return RiskGovernorAssessment(
            strategy_name=strategy_name, final_status=STATUS_BLOCKED, blocking_stage="D.3_RISK_BUDGET",
            capital_status=capital_decision.status, capital_allowed=capital_decision.allowed,
            capital_explanation=capital_decision.explanation,
            portfolio_status=portfolio_status, portfolio_explanation=portfolio_explanation,
            budget_status=budget_decision.status, budget_allowed=budget_decision.allowed,
            budget_explanation=budget_decision.explanation, real_risk_ceiling_units=sizing.maximum_quantity,
        )

    return RiskGovernorAssessment(
        strategy_name=strategy_name, final_status=STATUS_ADMITTED, blocking_stage=None,
        capital_status=capital_decision.status, capital_allowed=capital_decision.allowed,
        capital_explanation=capital_decision.explanation,
        portfolio_status=portfolio_status, portfolio_explanation=portfolio_explanation,
        budget_status=budget_decision.status, budget_allowed=budget_decision.allowed,
        budget_explanation=budget_decision.explanation, real_risk_ceiling_units=sizing.maximum_quantity,
    )

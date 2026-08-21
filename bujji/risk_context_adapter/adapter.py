"""Phase 20.17.1 -- the adapter. Translates Cycle 1's own Opportunity/
Decision Intelligence into a `RiskContextRequest`, then evaluates it
against ONLY D.1 (capital safety) -- the one real pre-trade-compatible
governor stage identified by this phase's Step 1 audit. D.2 (portfolio
risk) is deliberately NEVER called here: Phase 20.17 already proved
`aggregate_portfolio_risk()` marks a flat/zero-position book
`RISK_INVALID` because it requires a real `MarginSnapshot`, which
itself requires a real broker margin query against a real position --
structurally unavailable for a pre-trade opportunity review. Forcing
that call here would just reproduce Phase 20.17's own discovered
mismatch; this phase instead names the gap directly
(`NOT_READY_FOR_CAPITAL_APPROVAL`) rather than asking D.2 a question
it cannot honestly answer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, FinalDecision, WATCH
from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_portfolio.models import PortfolioDecision
from bujji.opportunity_ranking.models import RankingResult
from bujji.risk_governor_bridge import NOTIONAL_PROBE_MARGIN, NOTIONAL_PROBE_MAX_LOSS
from bujji.trading_brain.risk_governor.capital_safety_governor import (
    CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect, evaluate_trade_capital_safety,
)

from .models import (
    RiskContextAssessment, RiskContextRequest,
    STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_RESTRICTED,
    STATUS_UNAVAILABLE_RISK_CONTEXT,
)

Clock = Callable[[], datetime]

_WORTH_REVIEWING = (EXECUTABLE_CANDIDATE, WATCH)

_PORTFOLIO_CONTEXT_UNAVAILABLE = (
    "PORTFOLIO_CONTEXT_UNAVAILABLE: no live Position Group Journal / MarginSnapshot wiring exists "
    "for a pre-trade opportunity review -- D.2 (portfolio risk) was NOT evaluated, not forced to answer "
    "a question it structurally cannot answer for a flat book (see Phase 20.17's own discovered gap)."
)


def build_risk_context_request(
    final_decision: FinalDecision,
    allocation_assessment: Optional[AllocationAssessment],
    ranking_result: Optional[RankingResult],
    portfolio_decision: Optional[PortfolioDecision],
    *,
    timestamp: datetime,
) -> RiskContextRequest:
    """Pure translation -- reads only already-computed fields, never
    recomputes evidence/confidence/allocation/ranking. `ranking_result`/
    `portfolio_decision` are accepted per this phase's own required
    signature but are not currently needed to populate any field
    Cycle 1 can honestly supply; they are validated for presence only
    so a future field addition has no contract change to make."""
    allocation_class = allocation_assessment.allocation_class if allocation_assessment is not None else None

    confidence_level: Optional[str] = None
    market_regime: Optional[str] = None
    if allocation_assessment is not None:
        assessment = allocation_assessment.candidate.assessment
        confidence_level = assessment.strategy_score.confidence
        market_regime = assessment.environment.mic_regime

    return RiskContextRequest(
        strategy_name=final_decision.strategy_name or "UNKNOWN",
        opportunity_id=final_decision.strategy_name or "UNKNOWN",
        allocation_class=allocation_class,
        decision_state=final_decision.decision_state,
        expected_exposure=None,        # Cycle 1 never computes a real position size -- honestly None.
        expected_loss_boundary=None,   # same.
        confidence_level=confidence_level,
        market_regime=market_regime,
        timestamp=timestamp,
    )


def evaluate_risk_context(
    request: RiskContextRequest, capital_snapshot: Optional[CapitalSafetySnapshot], *, clock: Clock,
) -> RiskContextAssessment:
    if request.decision_state not in _WORTH_REVIEWING:
        return RiskContextAssessment(
            status=STATUS_NOT_EVALUATED, risk_context_valid=False, governor_response=None, blockers=(),
            explanation=(
                f"{request.strategy_name}: decision_state={request.decision_state!r} was never worth "
                f"reviewing by Cycle 1's own Decision Brain -- the Risk Governor cannot rescue a decision "
                f"Cycle 1's own evidence chain already rejected."
            ),
        )

    if capital_snapshot is None:
        return RiskContextAssessment(
            status=STATUS_UNAVAILABLE_RISK_CONTEXT, risk_context_valid=False, governor_response=None, blockers=(),
            explanation=(
                f"{request.strategy_name}: no capital_snapshot was supplied -- no pre-trade-compatible "
                f"Risk Governor interface could be reached. Never fabricated."
            ),
        )

    thresholds = CapitalSafetyThresholds()
    effect = ProposedTradeEffect(additional_margin=NOTIONAL_PROBE_MARGIN, additional_max_loss=NOTIONAL_PROBE_MAX_LOSS)
    capital_decision = evaluate_trade_capital_safety(capital_snapshot, effect, thresholds, clock)

    if not capital_decision.allowed:
        return RiskContextAssessment(
            status=STATUS_RESTRICTED, risk_context_valid=True, governor_response=capital_decision.status,
            blockers=capital_decision.blocking_reasons,
            explanation=(
                f"{request.strategy_name}: D.1 capital safety identifies a real restriction -- "
                f"{capital_decision.explanation}"
            ),
        )

    return RiskContextAssessment(
        status=STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, risk_context_valid=True,
        governor_response=capital_decision.status, blockers=(_PORTFOLIO_CONTEXT_UNAVAILABLE,),
        explanation=(
            f"{request.strategy_name}: D.1 capital safety clears ({capital_decision.status}). "
            f"D.2 portfolio risk was not evaluated -- no live margin/position context exists yet. "
            f"Risk Governor not rejected; this opportunity requires portfolio context before capital "
            f"admission can be assessed."
        ),
    )

"""Phase 20.10 -- the composer. Pure functions, no ML, no
optimization, no new scoring/ranking/qualification/regime/risk logic.
Every rule below reads an already-computed upstream field; none is
recalculated.
"""
from __future__ import annotations

from typing import List, Optional

from bujji.capital_intelligence import AllocationAssessment, MAXIMUM as ALLOC_MAXIMUM, NONE_ALLOCATION, NORMAL as ALLOC_NORMAL
from bujji.mic_v0.models import EVENT_CONTEXT_NOT_AVAILABLE
from bujji.opportunity_intelligence import BLOCKED as QUAL_BLOCKED, ELIGIBLE, INSUFFICIENT_EVIDENCE as QUAL_INSUFFICIENT_EVIDENCE
from bujji.opportunity_portfolio import REDUCE_CONFLICT as PORTFOLIO_REDUCE_CONFLICT, SELECT_PRIMARY as PORTFOLIO_SELECT_PRIMARY, PortfolioDecision

from .models import BLOCKED, EXECUTABLE_CANDIDATE, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY, WATCH, FinalDecision

_STRONG_ALLOCATIONS = (ALLOC_MAXIMUM, ALLOC_NORMAL)


def compose_decision(
    allocation: Optional[AllocationAssessment], portfolio_decision: Optional[PortfolioDecision],
    event_context: str = EVENT_CONTEXT_NOT_AVAILABLE,
) -> FinalDecision:
    """Rule 4 (insufficient intelligence must be honest), checked
    first: a genuinely MISSING upstream layer -- never a poor answer
    an upstream layer actually gave -- is the only thing that produces
    `INSUFFICIENT_INTELLIGENCE`."""
    unknown: List[str] = [f"Event calendar: {event_context}"] if event_context == EVENT_CONTEXT_NOT_AVAILABLE else []

    if allocation is None or portfolio_decision is None:
        missing: List[str] = []
        if allocation is None:
            missing.append("Missing allocation assessment (Phase 20.8).")
        if portfolio_decision is None:
            missing.append("Missing portfolio decision (Phase 20.9).")
        return FinalDecision(
            strategy_name=allocation.strategy_name if allocation else None,
            decision_state=INSUFFICIENT_INTELLIGENCE,
            positive=(), negative=(), unknown=tuple(missing),
            allocation=allocation, portfolio_decision=portfolio_decision,
        )

    strategy_name = allocation.strategy_name
    known_names = {c.strategy_name for c in portfolio_decision.candidates}
    if strategy_name not in known_names:
        return FinalDecision(
            strategy_name=strategy_name, decision_state=INSUFFICIENT_INTELLIGENCE,
            positive=(), negative=(),
            unknown=(f"{strategy_name} was not part of the portfolio decision Phase 20.9 evaluated -- "
                     f"cannot honestly compose a final decision from mismatched inputs.",),
            allocation=allocation, portfolio_decision=portfolio_decision,
        )

    score = allocation.candidate.assessment.strategy_score
    qual_state = allocation.candidate.qualification_state

    # Rule 3 (capital intelligence is a gate) + hard block: a genuinely
    # adverse condition (Phase 20.6's own BLOCKED, e.g. EXTREME risk)
    # is BLOCKED, never softened into NO_OPPORTUNITY.
    if qual_state == QUAL_BLOCKED:
        reason = allocation.candidate.assessment.decision.reasons[0]
        return FinalDecision(
            strategy_name=strategy_name, decision_state=BLOCKED,
            positive=(), negative=(f"{reason.code}: {reason.detail}",), unknown=tuple(unknown),
            allocation=allocation, portfolio_decision=portfolio_decision,
        )

    # Rule 1 (evidence first) + Rule 5 (MIC cannot manufacture opportunity):
    # insufficient evidence is a failed validation, never rescued by
    # anything downstream -- NO_OPPORTUNITY, not BLOCKED (nothing adverse
    # happened; the strategy simply never earned consideration).
    if allocation.allocation_class == NONE_ALLOCATION:
        assert qual_state == QUAL_INSUFFICIENT_EVIDENCE  # the only remaining way to reach NONE allocation.
        return FinalDecision(
            strategy_name=strategy_name, decision_state=NO_OPPORTUNITY,
            positive=(), negative=("Strategy validation failed -- insufficient historical evidence.",),
            unknown=tuple(unknown), allocation=allocation, portfolio_decision=portfolio_decision,
        )

    # Rule 2 (portfolio conflict matters): the portfolio-level process
    # (Phase 20.9) may still exclude an otherwise evidence-sufficient
    # candidate (conflict with a stronger opportunity).
    if strategy_name in portfolio_decision.excluded:
        reason = next((r for r in portfolio_decision.reasons if strategy_name in r), "Excluded by portfolio conflict resolution.")
        return FinalDecision(
            strategy_name=strategy_name, decision_state=NO_OPPORTUNITY,
            positive=(f"Validated historical edge: effective_score={score.effective_score:.0f}",),
            negative=(f"Portfolio-level exclusion: {reason}",), unknown=tuple(unknown),
            allocation=allocation, portfolio_decision=portfolio_decision,
        )

    positive: List[str] = [f"Validated historical edge: effective_score={score.effective_score:.0f}/100"]
    negative: List[str] = []

    if qual_state == ELIGIBLE:
        positive.append(f"Compatible regime: {allocation.candidate.assessment.decision.reasons[0].detail}")
    else:
        negative.append(f"Qualification WATCH: {allocation.candidate.assessment.decision.reasons[0].detail}")

    if allocation.allocation_class in _STRONG_ALLOCATIONS:
        positive.append(f"Risk allocation available: {allocation.allocation_class}")
    else:
        negative.append(f"Reduced risk allocation confidence: {allocation.allocation_class}")

    if portfolio_decision.decision == PORTFOLIO_SELECT_PRIMARY and strategy_name == portfolio_decision.primary:
        positive.append("Portfolio accepted as primary candidate.")
    elif strategy_name in portfolio_decision.kept:
        positive.append(f"Portfolio kept ({portfolio_decision.decision}).")
        if portfolio_decision.decision == PORTFOLIO_REDUCE_CONFLICT:
            negative.append("Portfolio-level conflict present -- proceed with caution.")

    if qual_state == ELIGIBLE and allocation.allocation_class in _STRONG_ALLOCATIONS and not negative:
        decision_state = EXECUTABLE_CANDIDATE
    else:
        decision_state = WATCH

    return FinalDecision(
        strategy_name=strategy_name, decision_state=decision_state,
        positive=tuple(positive), negative=tuple(negative), unknown=tuple(unknown),
        allocation=allocation, portfolio_decision=portfolio_decision,
    )

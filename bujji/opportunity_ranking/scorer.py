"""Phase 20.7 -- ranking. Pure functions, no ML, no optimization. Every
threshold is a round, disclosed reference point, not fit to any
specific strategy's numbers.
"""
from __future__ import annotations

from typing import List, Sequence

from bujji.opportunity_intelligence import BLOCKED, ELIGIBLE, INSUFFICIENT_EVIDENCE, WATCH

from .explain import build_penalties, build_reasons
from .models import (
    PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM,
    ExcludedOpportunity, OpportunityCandidate, RankedOpportunity, RankingResult,
)

# Rule 3: qualification modifies priority via a bounded multiplier on
# the evidence-based `effective_score` (Phase 20.5) -- never able to
# invert the evidence ordering by itself, since it only ever shrinks
# the score, never grows it. WATCH's 0.6 multiplier is a round,
# disclosed reduction, not fit to make any specific pair of strategies
# come out a particular way.
_QUALIFICATION_WEIGHT = {ELIGIBLE: 1.0, WATCH: 0.6}

_PRIORITY_TIER_HIGH_MIN = 60.0
_PRIORITY_TIER_MEDIUM_MIN = 30.0


def _priority_tier(priority_score: float) -> str:
    if priority_score >= _PRIORITY_TIER_HIGH_MIN:
        return PRIORITY_HIGH
    if priority_score >= _PRIORITY_TIER_MEDIUM_MIN:
        return PRIORITY_MEDIUM
    return PRIORITY_LOW


def rank_opportunities(candidates: Sequence[OpportunityCandidate]) -> RankingResult:
    """Rule 1 & 2: BLOCKED and INSUFFICIENT_EVIDENCE are excluded
    entirely -- never assigned a priority, never ordered. Rule 3:
    qualification (ELIGIBLE vs WATCH) scales `effective_score` by a
    bounded, disclosed multiplier. Rule 4: `effective_score` (Phase
    20.5's own evidence-based, confidence-capped score) is the base
    term every included candidate's `priority_score` is built from --
    qualification only ever shrinks it, so historical evidence stays
    dominant by construction, not by a rule this function has to
    remember to enforce separately."""
    ranked: List[RankedOpportunity] = []
    excluded: List[ExcludedOpportunity] = []

    for candidate in candidates:
        state = candidate.qualification_state
        assessment = candidate.assessment

        if state == BLOCKED:
            reason = assessment.decision.reasons[0]
            excluded.append(ExcludedOpportunity(
                strategy_name=candidate.strategy_name,
                reason=f"BLOCKED -- {reason.code}: {reason.detail}",
                candidate=candidate,
            ))
            continue

        if state == INSUFFICIENT_EVIDENCE:
            reason = assessment.decision.reasons[0]
            excluded.append(ExcludedOpportunity(
                strategy_name=candidate.strategy_name,
                reason=f"INSUFFICIENT_EVIDENCE -- {reason.detail}",
                candidate=candidate,
            ))
            continue

        weight = _QUALIFICATION_WEIGHT.get(state, 0.0)
        priority_score = round(assessment.strategy_score.effective_score * weight, 2)

        ranked.append(RankedOpportunity(
            strategy_name=candidate.strategy_name,
            priority=_priority_tier(priority_score),
            priority_score=priority_score,
            reasons=build_reasons(candidate),
            penalties=build_penalties(candidate),
            candidate=candidate,
        ))

    ranked.sort(key=lambda r: (r.priority_score, r.candidate.assessment.strategy_score.evidence.sample_size), reverse=True)
    return RankingResult(ranked=tuple(ranked), excluded=tuple(excluded))

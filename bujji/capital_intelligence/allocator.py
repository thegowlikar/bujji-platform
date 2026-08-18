"""Phase 20.8 -- the allocation engine. Pure functions, no ML, no
optimization, no parameter search. Every threshold is a round,
disclosed reference point, not fit to any specific strategy's numbers
-- in particular, the MAXIMUM threshold (85) is deliberately set ABOVE
Trend Following's own real Phase 20.5 score (78.62), a disclosed
calibration choice: no Cycle-1 strategy has yet earned the top tier,
by design, given single-signal, untuned research (Phase 20.4's own
"neither family is validated as a real strategy yet" caveat).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from bujji.epistemics import uncertainty as epi
from bujji.opportunity_intelligence import BLOCKED, ELIGIBLE, INSUFFICIENT_EVIDENCE, WATCH
from bujji.opportunity_ranking import OpportunityCandidate, RankingResult

from .explain import build_penalties, build_reasons, exclusion_reason
from .models import MAXIMUM, MINIMAL, NONE_ALLOCATION, NORMAL, REDUCED, AllocationAssessment, allocation_rank, demote_allocation

# Rule 3 (hard gate): these two qualification states always -> NONE,
# checked before anything else, exactly mirroring Phase 20.6/20.7's
# own "checked first" discipline for the same two states.
_HARD_GATE_STATES = (BLOCKED, INSUFFICIENT_EVIDENCE)

# Rule 3 fallback ONLY: if a caller passes a bare OpportunityCandidate
# without Phase 20.7's own already-computed `priority_score` (e.g. it
# was never ranked), this reconstructs the SAME formula Phase 20.7
# itself uses -- disclosed as a fallback, never the primary path (the
# primary path always reads Phase 20.7's real, already-computed value).
_QUALIFICATION_WEIGHT_FALLBACK = {ELIGIBLE: 1.0, WATCH: 0.6}

# Base allocation tier from priority_score (0-100). MAXIMUM is
# deliberately set above any evidence this Cycle has actually produced
# -- see module docstring.
_TIER_MAXIMUM_MIN = 85.0
_TIER_NORMAL_MIN = 60.0
_TIER_REDUCED_MIN = 35.0

# Rule 2: confidence caps allocation. HIGH imposes no cap; MODERATE/LOW
# progressively lower the ceiling regardless of how strong the raw
# priority_score is. Keyed by `bujji.epistemics.uncertainty`'s own
# confidence constants, reused unmodified -- never a second scale.
_CONFIDENCE_CAP = {epi.HIGH: MAXIMUM, epi.MODERATE: NORMAL, epi.LOW: REDUCED}

_STRESS_EXECUTION_PROFILES = ("STRESS", "EXTREME")


def _base_tier(priority_score: float) -> str:
    if priority_score >= _TIER_MAXIMUM_MIN:
        return MAXIMUM
    if priority_score >= _TIER_NORMAL_MIN:
        return NORMAL
    if priority_score >= _TIER_REDUCED_MIN:
        return REDUCED
    return MINIMAL


def assess_risk_allocation(
    candidate: OpportunityCandidate, priority_score: Optional[float] = None, rank: Optional[int] = None,
) -> AllocationAssessment:
    """`priority_score`/`rank`: pass Phase 20.7's own already-computed
    values (from a `RankedOpportunity`) -- this function reads them,
    never recalculates Phase 20.5's `effective_score` or Phase 20.7's
    qualification-weighted `priority_score` on the primary path."""
    assessment = candidate.assessment
    state = candidate.qualification_state
    score = assessment.strategy_score

    if state in _HARD_GATE_STATES:
        return AllocationAssessment(
            strategy_name=candidate.strategy_name, allocation_class=NONE_ALLOCATION,
            reasons=(exclusion_reason(candidate),), penalties=(),
            candidate=candidate, priority_score=None, rank=None,
        )

    if priority_score is None:
        weight = _QUALIFICATION_WEIGHT_FALLBACK.get(state, 0.0)
        priority_score = round(score.effective_score * weight, 2)

    reasons: List[str] = list(build_reasons(candidate))
    penalties: List[str] = list(build_penalties(candidate))

    allocation_class = _base_tier(priority_score)

    # Rule 2: confidence cap.
    cap = _CONFIDENCE_CAP.get(score.confidence, MINIMAL)
    if allocation_rank(allocation_class) > allocation_rank(cap):
        penalties.append(f"Confidence {score.confidence} caps allocation at {cap} (raw tier would have been {allocation_class})")
        allocation_class = cap
    else:
        reasons.append(f"Confidence {score.confidence} supports this allocation tier")

    # Rule 4: execution stress reduces allocation by one tier.
    execution_profile = assessment.environment.execution_profile_name
    if execution_profile in _STRESS_EXECUTION_PROFILES:
        demoted = demote_allocation(allocation_class, 1)
        penalties.append(f"{execution_profile} execution environment assumption -- allocation reduced from {allocation_class} to {demoted}")
        allocation_class = demoted
    else:
        reasons.append("Execution environment NORMAL -- no stress penalty")

    return AllocationAssessment(
        strategy_name=candidate.strategy_name, allocation_class=allocation_class,
        reasons=tuple(reasons), penalties=tuple(penalties),
        candidate=candidate, priority_score=priority_score, rank=rank,
    )


def assess_ranking_result(result: RankingResult) -> Tuple[AllocationAssessment, ...]:
    """Convenience: assess every candidate in a Phase 20.7 `RankingResult`
    -- ranked candidates use their own already-computed `priority_score`/
    position; excluded candidates go straight through the hard gate."""
    assessments: List[AllocationAssessment] = []
    for i, r in enumerate(result.ranked, start=1):
        assessments.append(assess_risk_allocation(r.candidate, priority_score=r.priority_score, rank=i))
    for e in result.excluded:
        assessments.append(assess_risk_allocation(e.candidate))
    return tuple(assessments)

"""Phase 20.9 -- conflict detection. Pure functions, no ML, no
optimization, no price-correlation computation. Every pair's
`CorrelationAssessment` is derived entirely from `bujji.strategy_
research.ALL_FAMILIES`'s own already-declared regime sets (Phase
20.3) -- reused unmodified, never re-derived or fit to data.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_intelligence import ELIGIBLE
from bujji.strategy_research import ALL_FAMILIES

from .models import COMPATIBLE, CONFLICTING, EXCLUSIVE, NEUTRAL, CorrelationAssessment, OpportunityConflict

_FAMILIES_BY_NAME = {f.name: f for f in ALL_FAMILIES}


def _assess_correlation(name_a: str, name_b: str) -> CorrelationAssessment:
    """Declarative only -- see `CorrelationAssessment`'s own
    docstring. A strategy name not found in `bujji.strategy_research.
    ALL_FAMILIES` (e.g. a future, not-yet-registered family) yields an
    honestly empty assessment -- never a fabricated overlap."""
    fam_a, fam_b = _FAMILIES_BY_NAME.get(name_a), _FAMILIES_BY_NAME.get(name_b)
    if fam_a is None or fam_b is None:
        return CorrelationAssessment(
            strategy_a=name_a, strategy_b=name_b, shared_favorable_regimes=(), opposing_regimes=(),
            basis=f"one or both strategies ({name_a!r}, {name_b!r}) are not registered in "
                  f"bujji.strategy_research.ALL_FAMILIES -- no declared regime sets to compare.",
        )
    required_a, required_b = set(fam_a.market_conditions_required), set(fam_b.market_conditions_required)
    incompatible_a, incompatible_b = set(fam_a.incompatible_conditions), set(fam_b.incompatible_conditions)

    shared = tuple(sorted(required_a & required_b))
    opposing = tuple(sorted((required_a & incompatible_b) | (required_b & incompatible_a)))

    return CorrelationAssessment(
        strategy_a=name_a, strategy_b=name_b, shared_favorable_regimes=shared, opposing_regimes=opposing,
        basis=f"{name_a}.market_conditions_required={fam_a.market_conditions_required!r} vs "
              f"{name_b}.market_conditions_required={fam_b.market_conditions_required!r} / "
              f"incompatible_conditions={fam_a.incompatible_conditions!r},{fam_b.incompatible_conditions!r}",
    )


def _classify_pair(a: AllocationAssessment, b: AllocationAssessment, correlation: CorrelationAssessment) -> Tuple[str, str]:
    """Returns (conflict_state, detail). EXCLUSIVE is CONFLICTING
    escalated by REAL, currently-observed simultaneity -- both
    candidates ELIGIBLE (both would actually be traded right now, not
    merely both theoretically defined) -- never assumed from the
    declared sets alone."""
    both_eligible = a.candidate.qualification_state == ELIGIBLE and b.candidate.qualification_state == ELIGIBLE

    if correlation.opposing_regimes:
        if both_eligible:
            return EXCLUSIVE, (
                f"Opposing market assumptions ({correlation.opposing_regimes}) AND both currently ELIGIBLE "
                f"at the same time -- only one should survive."
            )
        return CONFLICTING, f"Opposing market assumptions: {correlation.opposing_regimes} ({correlation.basis})"

    if correlation.shared_favorable_regimes:
        return COMPATIBLE, f"Shared favorable regimes: {correlation.shared_favorable_regimes}"

    return NEUTRAL, "No declared regime overlap in either direction -- no strong interaction."


def evaluate_conflicts(assessments: Sequence[AllocationAssessment]) -> Tuple[OpportunityConflict, ...]:
    """Every pairwise conflict among `assessments`. O(n^2) pairs --
    fine at Cycle-1's scale (a handful of strategy families); never
    intended for a large portfolio."""
    conflicts: List[OpportunityConflict] = []
    for i in range(len(assessments)):
        for j in range(i + 1, len(assessments)):
            a, b = assessments[i], assessments[j]
            correlation = _assess_correlation(a.strategy_name, b.strategy_name)
            state, detail = _classify_pair(a, b, correlation)
            conflicts.append(OpportunityConflict(
                strategy_a=a.strategy_name, strategy_b=b.strategy_name,
                conflict_state=state, detail=detail, correlation=correlation,
            ))
    return tuple(conflicts)

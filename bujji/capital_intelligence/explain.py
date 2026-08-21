"""Phase 20.8 -- explainability. Every allocation decision must answer
"why this risk level" -- this module builds those human-readable
strings from already-real fields, never inventing a justification
`allocator.py` didn't actually use.
"""
from __future__ import annotations

from typing import Tuple

from bujji.opportunity_intelligence import ELIGIBLE
from bujji.opportunity_ranking import OpportunityCandidate


def build_reasons(candidate: OpportunityCandidate) -> Tuple[str, ...]:
    """Positive factors for an INCLUDED (non-hard-gated) candidate --
    cites only fields `allocator.assess_risk_allocation` actually used."""
    score = candidate.assessment.strategy_score
    reasons = [
        f"Validated historical edge: effective_score={score.effective_score:.0f}/100 "
        f"(stability={score.stability_component:.0f}/25)",
    ]
    if candidate.qualification_state == ELIGIBLE:
        reasons.append(f"Market compatible: {candidate.assessment.decision.reasons[0].detail}")
    return tuple(reasons)


def build_penalties(candidate: OpportunityCandidate) -> Tuple[str, ...]:
    """Negative factors present at the QUALIFICATION level (WATCH) --
    the confidence-cap and execution-stress penalties are appended
    separately by `allocator.py`, since only it knows whether they
    actually fired."""
    if candidate.qualification_state == ELIGIBLE:
        return ()
    reason = candidate.assessment.decision.reasons[0]
    return (f"Qualification WATCH -- {reason.detail}",)


def exclusion_reason(candidate: OpportunityCandidate) -> str:
    """The single reason a hard-gated (BLOCKED/INSUFFICIENT_EVIDENCE)
    candidate received NONE -- cites Phase 20.6's own recorded reason
    verbatim, never a second, independently-derived explanation."""
    state = candidate.qualification_state
    reason = candidate.assessment.decision.reasons[0]
    if state == "INSUFFICIENT_EVIDENCE":
        return f"Strategy evidence insufficient ({reason.detail}) -- market conditions cannot override failed validation."
    return f"{state} -- {reason.code}: {reason.detail}"


def explain_allocation(assessment) -> str:
    """Human-readable "why this risk level" narrative for one
    `AllocationAssessment`."""
    lines = [f"{assessment.strategy_name}: {assessment.allocation_class}"]
    for reason in assessment.reasons:
        lines.append(f"  + {reason}")
    for penalty in assessment.penalties:
        lines.append(f"  - {penalty}")
    return "\n".join(lines)

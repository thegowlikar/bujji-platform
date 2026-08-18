"""Phase 20.7 -- explainability. Every ranked or excluded result must
answer "why" -- this module builds those human-readable strings from
already-real fields (`OpportunityAssessment`/`StrategyScore`), never
inventing a justification `scorer.py` didn't actually use.
"""
from __future__ import annotations

from typing import Tuple

from bujji.opportunity_intelligence import ELIGIBLE

from .models import OpportunityCandidate, RankingResult


def build_reasons(candidate: OpportunityCandidate) -> Tuple[str, ...]:
    """Positive factors -- cites only fields `scorer.rank_opportunities`
    actually used to compute this candidate's `priority_score`."""
    score = candidate.assessment.strategy_score
    reasons = [
        f"Validated edge: effective_score={score.effective_score:.0f}/100 "
        f"(edge={score.edge_component:.0f}, execution={score.execution_component:.0f}, "
        f"stability={score.stability_component:.0f})",
        f"Confidence: {score.confidence} (n={score.evidence.sample_size})",
    ]
    if candidate.qualification_state == ELIGIBLE:
        reasons.append(f"Current regime compatible: {candidate.assessment.decision.reasons[0].detail}")
    return tuple(reasons)


def build_penalties(candidate: OpportunityCandidate) -> Tuple[str, ...]:
    """Negative factors -- present only when qualification is WATCH
    (the only included-but-discounted state); cites the SAME reason
    Phase 20.6 already recorded for why it isn't ELIGIBLE, never a
    second, independently-derived explanation."""
    if candidate.qualification_state == ELIGIBLE:
        return ()
    reason = candidate.assessment.decision.reasons[0]
    return (f"Qualification WATCH -- {reason.detail} (priority discounted, not excluded)",)


def explain_ranking(result: RankingResult) -> str:
    """Human-readable "why is this above that" narrative -- consecutive
    ranked pairs compared directly, then excluded candidates listed
    with their exclusion reason."""
    lines = ["Opportunity Ranking Explanation:", ""]
    for i, r in enumerate(result.ranked):
        lines.append(f"#{i + 1}: {r.strategy_name} -- priority={r.priority}, score={r.priority_score:.0f}")
        for reason in r.reasons:
            lines.append(f"    + {reason}")
        for penalty in r.penalties:
            lines.append(f"    - {penalty}")
        if i + 1 < len(result.ranked):
            nxt = result.ranked[i + 1]
            lines.append(
                f"    Ahead of #{i + 2} ({nxt.strategy_name}, score={nxt.priority_score:.0f}) "
                f"by {r.priority_score - nxt.priority_score:.0f} points."
            )
        lines.append("")
    if result.excluded:
        lines.append("Excluded from ranking entirely:")
        for e in result.excluded:
            lines.append(f"  {e.strategy_name}: {e.reason}")
    return "\n".join(lines)

"""Phase 20.9 -- the portfolio decision engine. Pure functions, no ML,
no optimization, no forced diversification. "More strategies is safer"
is explicitly NOT assumed anywhere here -- a single dominant,
well-evidenced opportunity is a completely valid, un-penalized
outcome.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from bujji.capital_intelligence import AllocationAssessment, NONE_ALLOCATION, allocation_rank
from bujji.opportunity_intelligence import INSUFFICIENT_EVIDENCE as QUAL_INSUFFICIENT_EVIDENCE

from .conflict import evaluate_conflicts
from .models import (
    ALLOW_MULTIPLE, COMPATIBLE, CONFLICTING, EXCLUSIVE, INSUFFICIENT_EVIDENCE, NEUTRAL, NO_SELECTION,
    REDUCE_CONFLICT, SELECT_PRIMARY, OpportunityConflict, PortfolioDecision,
)


def _evidence_key(a: AllocationAssessment):
    """Rule 1 (evidence dominates): sort by Phase 20.8's own allocation
    tier first, then Phase 20.7's own priority_score, then Phase 20.5's
    own sample_size as a final, non-evidence-inflating tiebreak --
    reused values throughout, nothing recalculated."""
    score = a.candidate.assessment.strategy_score
    return (allocation_rank(a.allocation_class), a.priority_score or 0.0, score.evidence.sample_size)


def rank_portfolio_choices(assessments: Sequence[AllocationAssessment]) -> PortfolioDecision:
    if not assessments:
        return PortfolioDecision(
            decision=NO_SELECTION, primary=None, kept=(), excluded=(),
            reasons=("No candidates supplied.",), conflicts=(), candidates=(),
        )

    survivors = [a for a in assessments if a.allocation_class != NONE_ALLOCATION]
    excluded_initial = [a for a in assessments if a.allocation_class == NONE_ALLOCATION]

    if not survivors:
        # Rule: weak-strategy exclusion can never be rescued by "there were several of them."
        if all(a.candidate.qualification_state == QUAL_INSUFFICIENT_EVIDENCE for a in excluded_initial):
            reasons = tuple(f"{a.strategy_name}: {a.reasons[0]}" for a in excluded_initial)
            return PortfolioDecision(
                decision=INSUFFICIENT_EVIDENCE, primary=None, kept=(), excluded=tuple(a.strategy_name for a in excluded_initial),
                reasons=reasons, conflicts=(), candidates=tuple(assessments),
            )
        reasons = tuple(f"{a.strategy_name}: {a.reasons[0]}" for a in excluded_initial)
        return PortfolioDecision(
            decision=NO_SELECTION, primary=None, kept=(), excluded=tuple(a.strategy_name for a in excluded_initial),
            reasons=reasons, conflicts=(), candidates=tuple(assessments),
        )

    if len(survivors) == 1:
        only = survivors[0]
        excluded_names = tuple(a.strategy_name for a in excluded_initial)
        reasons = (f"Only qualified, sufficiently-evidenced candidate: {only.strategy_name} "
                   f"(allocation={only.allocation_class}).",)
        return PortfolioDecision(
            decision=SELECT_PRIMARY, primary=only.strategy_name, kept=(only.strategy_name,), excluded=excluded_names,
            reasons=reasons, conflicts=(), candidates=tuple(assessments),
        )

    conflicts = evaluate_conflicts(survivors)
    ranked = sorted(survivors, key=_evidence_key, reverse=True)

    exclusive_pairs = [c for c in conflicts if c.conflict_state == EXCLUSIVE]
    conflicting_pairs = [c for c in conflicts if c.conflict_state == CONFLICTING]

    if not exclusive_pairs and not conflicting_pairs:
        # Every pair is COMPATIBLE or NEUTRAL -- no forced diversification, but no forced
        # exclusion either: nothing here contradicts anything else.
        kept = tuple(a.strategy_name for a in survivors)
        excluded_names = tuple(a.strategy_name for a in excluded_initial)
        reasons = tuple(f"{a.strategy_name}: no conflicting assumption with any other survivor" for a in survivors)
        return PortfolioDecision(
            decision=ALLOW_MULTIPLE, primary=None, kept=kept, excluded=excluded_names,
            reasons=reasons, conflicts=conflicts, candidates=tuple(assessments),
        )

    strongest = ranked[0]
    weaker = ranked[1:]

    if exclusive_pairs:
        # Rule 1 (evidence dominance) resolves an EXCLUSIVE pair outright: the
        # strongest survives, everything it's EXCLUSIVE-conflicted with is cut.
        excluded_names = tuple(a.strategy_name for a in weaker) + tuple(a.strategy_name for a in excluded_initial)
        reasons = (
            f"{strongest.strategy_name} dominates on evidence "
            f"(allocation={strongest.allocation_class}, priority_score={strongest.priority_score}).",
        ) + tuple(f"{a.strategy_name}: excluded -- exclusive conflict with {strongest.strategy_name}" for a in weaker)
        return PortfolioDecision(
            decision=SELECT_PRIMARY, primary=strongest.strategy_name, kept=(strongest.strategy_name,),
            excluded=excluded_names, reasons=reasons, conflicts=conflicts, candidates=tuple(assessments),
        )

    # CONFLICTING but never simultaneously EXCLUSIVE: real tension exists, but
    # nothing forces an outright exclusion -- keep the survivors, flagged for caution.
    kept = tuple(a.strategy_name for a in survivors)
    excluded_names = tuple(a.strategy_name for a in excluded_initial)
    reasons = (f"Conflicting market assumptions present among survivors -- proceed with reduced confidence, "
               f"strongest evidence remains {strongest.strategy_name}.",)
    return PortfolioDecision(
        decision=REDUCE_CONFLICT, primary=strongest.strategy_name, kept=kept, excluded=excluded_names,
        reasons=reasons, conflicts=conflicts, candidates=tuple(assessments),
    )

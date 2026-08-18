"""Phase 20.15.1 -- deterministic memory influence logic. Does NOT
modify `bujji.strategy_intelligence.scoring` -- consumes its own
already-real `confidence` string as a plain input, never reaches into
or recomputes `StrategyScore`. Mirrors `_apply_mic_context()`'s own
exact demotion mechanism (Phase 20.5, `bujji.epistemics.uncertainty.
demote()`, reused unmodified) for the CONTRADICTED case, and a small,
disclosed, symmetric mirror (`_promote_one_band`, since `bujji.
epistemics.uncertainty` has never needed to raise confidence before
this phase) for the SUPPORTED case -- capped at HIGH, one band only,
same as demote's own one-band-at-a-time discipline.

Rule ladder (checked in this fixed order):
  no memory / too few similar conditions   -> UNCHANGED, honest.
  too few KNOWN outcomes                    -> UNCHANGED, never guessed.
  favorable ratio strictly above threshold  -> SUPPORTED, +1 band, capped HIGH.
  unfavorable ratio strictly above threshold -> CONTRADICTED, -1 band via epi.demote.
  otherwise (roughly balanced)               -> UNCHANGED, flagged CONTRADICTORY_HISTORY.
"""
from __future__ import annotations

from typing import Sequence

from bujji.epistemics import uncertainty as epi
from bujji.market_memory.models import OutcomeMemoryRecord, STATUS_KNOWN
from bujji.market_memory.retrieval import MemoryContext

from .models import (
    DIRECTION_CONTRADICTED, DIRECTION_SUPPORTED, DIRECTION_UNCHANGED,
    FLAG_CONTRADICTORY_HISTORY, FLAG_INSUFFICIENT_KNOWN_OUTCOMES, FLAG_INSUFFICIENT_SIMILAR, FLAG_NO_MEMORY,
    MemoryInfluenceAssessment,
)

# Disclosed, not tuned -- mirrors the same "documented threshold, not
# optimized" discipline every prior phase's own constants use.
MIN_SIMILAR_FOR_INFLUENCE = 3
MIN_KNOWN_OUTCOMES_FOR_INFLUENCE = 3
FAVORABLE_RATIO_THRESHOLD = 0.5    # strictly above -> SUPPORTED.
UNFAVORABLE_RATIO_THRESHOLD = 0.5  # strictly above -> CONTRADICTED.

_CONFIDENCE_ORDER = (epi.NONE, epi.LOW, epi.MODERATE, epi.HIGH)


def _promote_one_band(confidence: str) -> str:
    """Mirrors `epi.demote()`'s own logic, inverted. `bujji.epistemics.
    uncertainty` exposes no `promote()` (this codebase has never
    needed to raise confidence before this phase) -- a small, disclosed,
    symmetric mirror, never a private-symbol import, capped at HIGH
    exactly like `demote()` floors at NONE."""
    idx = _CONFIDENCE_ORDER.index(confidence)
    return _CONFIDENCE_ORDER[min(len(_CONFIDENCE_ORDER) - 1, idx + 1)]


def evaluate_memory_influence(
    strategy_name: str, base_confidence: str, context: MemoryContext, outcome_memories: Sequence[OutcomeMemoryRecord],
) -> MemoryInfluenceAssessment:
    if context.similar_count == 0:
        return MemoryInfluenceAssessment(
            strategy_name=strategy_name, memory_available=False, similarity_count=0,
            historical_outcome_summary={}, base_confidence=base_confidence, confidence_modifier=base_confidence,
            confidence_direction=DIRECTION_UNCHANGED, uncertainty_flags=(FLAG_NO_MEMORY,),
            explanation="No memory adjustment applied -- no similar historical conditions exist in memory.",
        )

    if context.similar_count < MIN_SIMILAR_FOR_INFLUENCE:
        return MemoryInfluenceAssessment(
            strategy_name=strategy_name, memory_available=True, similarity_count=context.similar_count,
            historical_outcome_summary={}, base_confidence=base_confidence, confidence_modifier=base_confidence,
            confidence_direction=DIRECTION_UNCHANGED, uncertainty_flags=(FLAG_INSUFFICIENT_SIMILAR,),
            explanation=f"No memory adjustment applied -- only {context.similar_count} similar historical "
                        f"observation(s) exist (minimum {MIN_SIMILAR_FOR_INFLUENCE} required).",
        )

    similar_ids = {s.memory_id for s in context.similarities}
    known = [o for o in outcome_memories if o.memory_id in similar_ids and o.status == STATUS_KNOWN]

    if len(known) < MIN_KNOWN_OUTCOMES_FOR_INFLUENCE:
        return MemoryInfluenceAssessment(
            strategy_name=strategy_name, memory_available=True, similarity_count=context.similar_count,
            historical_outcome_summary={}, base_confidence=base_confidence, confidence_modifier=base_confidence,
            confidence_direction=DIRECTION_UNCHANGED, uncertainty_flags=(FLAG_INSUFFICIENT_KNOWN_OUTCOMES,),
            explanation=f"No memory adjustment applied -- {context.similar_count} similar conditions found "
                        f"but only {len(known)} known outcome(s) (minimum {MIN_KNOWN_OUTCOMES_FOR_INFLUENCE} "
                        f"required); the rest are not yet observed.",
        )

    favorable = sum(1 for o in known if o.regime_unchanged is True)
    unfavorable = sum(1 for o in known if o.regime_unchanged is False)
    summary = {"favorable": favorable, "unfavorable": unfavorable}
    favorable_ratio = favorable / len(known)
    unfavorable_ratio = unfavorable / len(known)

    if favorable_ratio > FAVORABLE_RATIO_THRESHOLD:
        return MemoryInfluenceAssessment(
            strategy_name=strategy_name, memory_available=True, similarity_count=context.similar_count,
            historical_outcome_summary=summary, base_confidence=base_confidence,
            confidence_modifier=_promote_one_band(base_confidence), confidence_direction=DIRECTION_SUPPORTED,
            explanation=f"Confidence support: {favorable}/{len(known)} similar historical conditions showed "
                        f"favorable outcomes (regime held) -- confidence raised one band, evidence_score unchanged.",
        )

    if unfavorable_ratio > UNFAVORABLE_RATIO_THRESHOLD:
        return MemoryInfluenceAssessment(
            strategy_name=strategy_name, memory_available=True, similarity_count=context.similar_count,
            historical_outcome_summary=summary, base_confidence=base_confidence,
            confidence_modifier=epi.demote(base_confidence, 1), confidence_direction=DIRECTION_CONTRADICTED,
            explanation=f"Confidence reduced: {unfavorable}/{len(known)} similar historical conditions showed "
                        f"poor outcomes (regime changed) -- confidence demoted one band, evidence_score unchanged.",
        )

    return MemoryInfluenceAssessment(
        strategy_name=strategy_name, memory_available=True, similarity_count=context.similar_count,
        historical_outcome_summary=summary, base_confidence=base_confidence, confidence_modifier=base_confidence,
        confidence_direction=DIRECTION_UNCHANGED, uncertainty_flags=(FLAG_CONTRADICTORY_HISTORY,),
        explanation=f"No memory adjustment applied -- historical outcomes are contradictory "
                    f"({favorable} favorable / {unfavorable} unfavorable of {len(known)} known) -- "
                    f"uncertainty flagged, confidence unchanged rather than guessed.",
    )

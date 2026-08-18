"""Phase 20.22 -- the thin composition adapter. Combines an already-
computed `FinalDecision` (Phase 20.10) and an already-computed
`MemoryInfluenceAssessment` (Phase 20.15.1) into one `MemoryDecisionContext`
view object. Zero calculation: no memory similarity, no EventStore
query, no historical outcome evaluation, no evidence/qualification/
ranking/allocation recomputation happens anywhere in this file.
"""
from __future__ import annotations

from bujji.decision_orchestration import (
    BLOCKED, EXECUTABLE_CANDIDATE, FinalDecision, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY, WATCH,
)
from bujji.memory_intelligence import (
    DIRECTION_CONTRADICTED, DIRECTION_SUPPORTED, DIRECTION_UNCHANGED, MemoryInfluenceAssessment,
)

from .models import MemoryDecisionContext

# Only a decision Cycle 1 itself found worth further review ever gets a
# memory-adjusted confidence VIEW -- mirrors the same "not worth
# reviewing" boundary Phase 20.17/20.17.1/20.18 each already established
# independently for their own downstream stages.
_ELIGIBLE_FOR_ADJUSTED_VIEW = (EXECUTABLE_CANDIDATE, WATCH)
_REJECTED_STATES = (NO_OPPORTUNITY, BLOCKED, INSUFFICIENT_INTELLIGENCE)

_EFFECT_SUMMARY = {
    DIRECTION_SUPPORTED: "Historical conditions support this pattern.",
    DIRECTION_CONTRADICTED: "Historical conditions contradict this pattern.",
    DIRECTION_UNCHANGED: "Historical evidence is absent, insufficient, or mixed -- no directional signal.",
}


def build_memory_decision_context(
    final_decision: FinalDecision, memory_assessment: MemoryInfluenceAssessment,
) -> MemoryDecisionContext:
    """`final_decision.decision_state` is NEVER read to decide whether
    to alter it -- only to decide whether an ADJUSTED CONFIDENCE VIEW
    is a meaningful concept to attach at all. The decision itself
    (`decision_status`, via `MemoryDecisionContext`'s own property) is
    always the original, unmodified `FinalDecision.decision_state`,
    regardless of what memory says."""
    effect_summary = _EFFECT_SUMMARY[memory_assessment.confidence_direction]

    if final_decision.decision_state in _REJECTED_STATES:
        adjusted_confidence_view = None
        explanation = (
            f"{final_decision.strategy_name}: decision_state={final_decision.decision_state!r} is not "
            f"eligible for further review. {effect_summary} Historical support (if any) does not change "
            f"this -- the base decision is not eligible, and memory cannot create an opportunity or "
            f"reverse a rejection."
        )
    else:
        adjusted_confidence_view = memory_assessment.confidence_modifier
        explanation = (
            f"{final_decision.strategy_name}: decision_state={final_decision.decision_state!r} remains "
            f"unchanged. {effect_summary} Base confidence {memory_assessment.base_confidence!r} -> "
            f"memory-adjusted view {adjusted_confidence_view!r} (context only -- the Decision Brain's own "
            f"confidence value is never overwritten by this package)."
        )

    return MemoryDecisionContext(
        original_decision=final_decision, memory_influence_assessment=memory_assessment,
        memory_effect_summary=effect_summary, adjusted_confidence_view=adjusted_confidence_view,
        explanation=explanation,
    )

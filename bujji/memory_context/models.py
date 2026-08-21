"""Phase 20.22 -- pure data contracts. No IO, no broker, no execution,
no memory-evaluation logic anywhere in this module -- this package
consumes `bujji.memory_intelligence`'s own already-computed
`MemoryInfluenceAssessment`, never recomputes one.

DISCLOSED NAME PRECEDENT: `bujji.decision_context.DecisionContext`
(Phase 19.4) and `bujji.intelligence.context.IntelligenceContext`
(Phase 19.2.2) both already exist -- real, separate classes in the
older Phase 19.x/MSI lineage. `MemoryDecisionContext` below is a
distinct name specifically to avoid any ambiguity with either, same
precedent as `ShadowResultRecord` (Phase 20.20) avoiding the bare
`ShadowResult` name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bujji.decision_orchestration import FinalDecision
from bujji.memory_intelligence import MemoryInfluenceAssessment


@dataclass(frozen=True)
class MemoryDecisionContext:
    """A VIEW/CONTEXT object -- NOT a new decision, and it cannot
    replace `FinalDecision`. `decision_status` is always a verbatim
    passthrough of `original_decision.decision_state` -- this field
    exists so a downstream reader never has to separately fetch the
    original decision just to see its own status; it is never derived
    or recomputed here.

    `adjusted_confidence_view` is `memory_influence_assessment.
    confidence_modifier` carried through unmodified when the base
    decision was ever worth reviewing (EXECUTABLE_CANDIDATE/WATCH);
    for a decision Cycle 1 itself already rejected (NO_OPPORTUNITY/
    BLOCKED/INSUFFICIENT_INTELLIGENCE), it is honestly `None` -- a
    memory-adjusted "view" of a decision that was never eligible in
    the first place is not a meaningful concept, and inventing one
    would risk being misread as encouragement to reconsider a
    rejection."""

    original_decision: FinalDecision
    memory_influence_assessment: MemoryInfluenceAssessment
    memory_effect_summary: str
    adjusted_confidence_view: Optional[str]
    explanation: str

    @property
    def decision_status(self) -> str:
        return self.original_decision.decision_state

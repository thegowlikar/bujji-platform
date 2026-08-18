"""Phase 20.15.1 -- the composition adapter. Names the flow explicitly:

    Decision Brain -> Memory Context -> Confidence Adjustment -> Decision Brain continues

Composition only -- does NOT modify `bujji.decision_orchestration`,
does NOT modify `bujji.strategy_intelligence`, does NOT call
`compose_decision()` or `score_strategy()`. Takes their own already-real
outputs (a `StrategyScore`, a `MemoryContext`) as plain inputs and
returns a `MemoryInfluenceAssessment` -- an assessment ALONGSIDE the
existing decision chain, never a replacement step inside it. A caller
that never calls `apply_memory_influence()` gets EXACTLY Phase 20.10's
own unmodified `FinalDecision` -- this module is purely additive.
"""
from __future__ import annotations

from typing import Sequence

from bujji.market_memory.models import OutcomeMemoryRecord
from bujji.market_memory.retrieval import MemoryContext
from bujji.strategy_intelligence.models import StrategyScore

from .evaluator import evaluate_memory_influence
from .models import MemoryInfluenceAssessment


def apply_memory_influence(
    score: StrategyScore, context: MemoryContext, outcome_memories: Sequence[OutcomeMemoryRecord],
) -> MemoryInfluenceAssessment:
    """The single entry point a caller uses. Reads `score.strategy_name`/
    `score.confidence` -- Phase 20.5's own already-computed values --
    and nothing else off `score`; `evidence_score`/`effective_score`
    are never read, never touched, never present anywhere downstream
    of this call."""
    return evaluate_memory_influence(
        strategy_name=score.strategy_name, base_confidence=score.confidence,
        context=context, outcome_memories=outcome_memories,
    )

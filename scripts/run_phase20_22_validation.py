"""Phase 20.22 real-artifact validation script -- reuses Phase 20.5's
own published real evidence verbatim and Phase 20.15.1's own real
memory-influence scenario shapes.
"""
from __future__ import annotations

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, FinalDecision
from bujji.memory_intelligence import DIRECTION_SUPPORTED, MemoryInfluenceAssessment
from bujji.epistemics.uncertainty import HIGH, MODERATE
from bujji.memory_context import build_memory_decision_context, explain_memory_decision_context


def _decision(state, strategy_name="TrendFollowing"):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=state,
        positive=("real_evidence",) if state != NO_OPPORTUNITY else (),
        negative=() if state != NO_OPPORTUNITY else ("insufficient_evidence",),
        unknown=(), allocation=None, portfolio_decision=None,
    )


def _positive_memory():
    return MemoryInfluenceAssessment(
        strategy_name="TrendFollowing", memory_available=True, similarity_count=5,
        historical_outcome_summary={"favorable": 5, "unfavorable": 0}, base_confidence=MODERATE,
        confidence_modifier=HIGH, confidence_direction=DIRECTION_SUPPORTED,
        uncertainty_flags=(), explanation="5/5 real similar historical conditions were favorable.",
    )


def main():
    print("=" * 70)
    print("Scenario A: Decision rejected (NO_OPPORTUNITY) + positive memory")
    print("=" * 70)
    context_a = build_memory_decision_context(_decision(NO_OPPORTUNITY, "MeanReversion"), _positive_memory())
    print(explain_memory_decision_context(context_a))
    assert context_a.decision_status == NO_OPPORTUNITY
    assert context_a.adjusted_confidence_view is None
    print()

    print("=" * 70)
    print("Scenario B: Executable candidate + positive memory")
    print("=" * 70)
    context_b = build_memory_decision_context(_decision(EXECUTABLE_CANDIDATE), _positive_memory())
    print(explain_memory_decision_context(context_b))
    assert context_b.decision_status == EXECUTABLE_CANDIDATE
    assert context_b.adjusted_confidence_view == HIGH
    print()

    print("=" * 70)
    print("Scenario C: Blocked decision + positive memory")
    print("=" * 70)
    context_c = build_memory_decision_context(_decision(BLOCKED), _positive_memory())
    print(explain_memory_decision_context(context_c))
    assert context_c.decision_status == BLOCKED
    assert context_c.adjusted_confidence_view is None
    print()

    print("PROOF: evidence_score field does not exist anywhere on FinalDecision/MemoryInfluenceAssessment/MemoryDecisionContext.")
    for obj in (context_a.original_decision, context_a.memory_influence_assessment, context_a):
        assert not hasattr(obj, "evidence_score")
    print("Confirmed.")


if __name__ == "__main__":
    main()

"""Phase 20.15.1 -- Market Memory Read Layer (memory_intelligence)
tests."""
from __future__ import annotations

import os

import pytest

from bujji.epistemics import uncertainty as epi
from bujji.mic_v0 import models as mic_models
from bujji.mic_v0.models import ConfidenceInfo, EventContext, MarketState
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.capital_intelligence import assess_risk_allocation
from bujji.opportunity_portfolio import rank_portfolio_choices
from bujji.decision_orchestration import compose_decision, BLOCKED
from bujji.strategy_intelligence import StrategyEvidence, score_strategy
from bujji.shadow_decision_runtime import run_shadow_cycle
from bujji.market_memory.models import OutcomeMemoryRecord, STATUS_KNOWN, STATUS_NOT_YET_OBSERVED
from bujji.market_memory.retrieval import MemoryContext, SimilarityExplanation
from bujji.memory_intelligence import (
    DIRECTION_CONTRADICTED, DIRECTION_SUPPORTED, DIRECTION_UNCHANGED,
    MIN_KNOWN_OUTCOMES_FOR_INFLUENCE, MIN_SIMILAR_FOR_INFLUENCE,
    apply_memory_influence, evaluate_memory_influence, explain_memory_influence,
)

STRONG_EVIDENCE = StrategyEvidence(
    strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
    net_expectancy=517.0, gross_expectancy=949.0,
    train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
)
TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)


def _similarities(n):
    return tuple(
        SimilarityExplanation(memory_id=f"MKTMEM-{i}", score=100, max_score=100,
                               regime_match=True, volatility_match=True, risk_match=True)
        for i in range(n)
    )


def _context(similar_count):
    sims = _similarities(similar_count)
    return MemoryContext(
        target_memory_id="MKTMEM-target", similar_count=similar_count, known_outcome_count=0,
        not_yet_observed_count=similar_count, outcome_distribution={}, similarities=sims,
    )


def _outcome(memory_id, regime_unchanged, status=STATUS_KNOWN):
    return OutcomeMemoryRecord(
        memory_id=memory_id, status=status,
        observed_at="2026-08-11T09:30:00+05:30" if status == STATUS_KNOWN else None,
        regime_after="RANGE", volatility_state_after="LOW", decision_state_after="NO_OPPORTUNITY",
        regime_unchanged=regime_unchanged,
    )


def _context_with_known(n, favorable_count):
    sims = _similarities(n)
    return MemoryContext(
        target_memory_id="MKTMEM-target", similar_count=n, known_outcome_count=n, not_yet_observed_count=0,
        outcome_distribution={}, similarities=sims,
    ), [
        _outcome(sims[i].memory_id, regime_unchanged=(i < favorable_count)) for i in range(n)
    ]


def _real_final_decision(regime="TREND_UP", risk=mic_models.RISK_NORMAL):
    env = MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=mic_models.VOLATILITY_NORMAL,
                             execution_profile_name="NORMAL")
    score = score_strategy(STRONG_EVIDENCE)
    assessment = evaluate_opportunity(score, env, TREND_FAVORABLE, TREND_UNFAVORABLE)
    allocation = assess_risk_allocation(OpportunityCandidate(assessment=assessment))
    portfolio = rank_portfolio_choices([allocation])
    return compose_decision(allocation, portfolio), score


# --------------------------------------------------------------------- #
# 1. Memory unavailable honesty
# --------------------------------------------------------------------- #
def test_no_memory_produces_no_fabricated_adjustment():
    context = _context(similar_count=0)
    result = evaluate_memory_influence("FAMILY_A_TREND_FOLLOWING", epi.HIGH, context, outcome_memories=[])
    assert result.memory_available is False
    assert result.confidence_modifier == result.base_confidence == epi.HIGH
    assert result.confidence_direction == DIRECTION_UNCHANGED


# --------------------------------------------------------------------- #
# 2. Evidence preservation
# --------------------------------------------------------------------- #
def test_evidence_score_unchanged_before_and_after():
    score_before = score_strategy(STRONG_EVIDENCE)
    context, outcomes = _context_with_known(4, favorable_count=0)  # all unfavorable -> would demote confidence.
    apply_memory_influence(score_before, context, outcomes)
    score_after = score_strategy(STRONG_EVIDENCE)
    assert score_before.evidence_score == score_after.evidence_score == 78.62
    assert score_before.effective_score == score_after.effective_score


# --------------------------------------------------------------------- #
# 3. Qualification preservation -- BLOCKED remains BLOCKED
# --------------------------------------------------------------------- #
def test_blocked_decision_cannot_be_rescued_by_memory():
    final_decision, score = _real_final_decision(risk=mic_models.RISK_EXTREME)
    assert final_decision.decision_state == BLOCKED

    context, outcomes = _context_with_known(5, favorable_count=5)  # maximally favorable memory.
    assessment = apply_memory_influence(score, context, outcomes)
    assert assessment.confidence_direction == DIRECTION_SUPPORTED  # memory itself is favorable...

    # ...but the real FinalDecision, entirely independent of memory_intelligence, is untouched.
    final_decision_again, _ = _real_final_decision(risk=mic_models.RISK_EXTREME)
    assert final_decision_again.decision_state == BLOCKED


# --------------------------------------------------------------------- #
# 4. Strategy independence -- memory changes confidence only
# --------------------------------------------------------------------- #
def test_memory_never_touches_ranking_or_allocation_fields():
    final_decision, score = _real_final_decision()
    context, outcomes = _context_with_known(4, favorable_count=0)
    assessment = apply_memory_influence(score, context, outcomes)
    # MemoryInfluenceAssessment carries no ranking/allocation-shaped field at all.
    assert not hasattr(assessment, "priority_score")
    assert not hasattr(assessment, "allocation_class")
    assert not hasattr(assessment, "evidence_score")
    assert not hasattr(assessment, "effective_score")


# --------------------------------------------------------------------- #
# 5. Negative memory effect
# --------------------------------------------------------------------- #
def test_similar_historical_failures_reduce_confidence():
    score = score_strategy(STRONG_EVIDENCE)
    context, outcomes = _context_with_known(4, favorable_count=0)  # 4/4 unfavorable.
    result = evaluate_memory_influence(score.strategy_name, score.confidence, context, outcomes)
    assert result.confidence_direction == DIRECTION_CONTRADICTED
    assert result.confidence_modifier == epi.demote(score.confidence, 1)


# --------------------------------------------------------------------- #
# 6. Positive memory effect -- increases only within limits
# --------------------------------------------------------------------- #
def test_strong_historical_support_increases_confidence_one_band_only():
    score = score_strategy(STRONG_EVIDENCE)
    context, outcomes = _context_with_known(4, favorable_count=4)  # 4/4 favorable.
    result = evaluate_memory_influence(score.strategy_name, score.confidence, context, outcomes)
    assert result.confidence_direction == DIRECTION_SUPPORTED
    rank = {epi.NONE: 0, epi.LOW: 1, epi.MODERATE: 2, epi.HIGH: 3}
    assert rank[result.confidence_modifier] <= rank[score.confidence] + 1
    assert rank[result.confidence_modifier] >= rank[score.confidence]

    # Already at HIGH -- capped, never overflows past the vocabulary.
    context_high, outcomes_high = _context_with_known(4, favorable_count=4)
    result_high = evaluate_memory_influence("x", epi.HIGH, context_high, outcomes_high)
    assert result_high.confidence_modifier == epi.HIGH


# --------------------------------------------------------------------- #
# 7. No-lookahead (delegated to market_memory, reconfirmed at this layer)
# --------------------------------------------------------------------- #
def test_insufficient_known_outcomes_never_guesses():
    score = score_strategy(STRONG_EVIDENCE)
    context, outcomes = _context_with_known(4, favorable_count=1)
    # Only 1 known outcome supplied even though 4 are "similar" -- simulate by trimming outcomes.
    trimmed = outcomes[:2]  # below MIN_KNOWN_OUTCOMES_FOR_INFLUENCE.
    result = evaluate_memory_influence(score.strategy_name, score.confidence, context, trimmed)
    assert result.confidence_direction == DIRECTION_UNCHANGED
    assert result.confidence_modifier == score.confidence


# --------------------------------------------------------------------- #
# 8. Explainability
# --------------------------------------------------------------------- #
def test_every_adjustment_has_a_reason():
    score = score_strategy(STRONG_EVIDENCE)
    for favorable_count in (0, 2, 4):
        context, outcomes = _context_with_known(4, favorable_count=favorable_count)
        result = evaluate_memory_influence(score.strategy_name, score.confidence, context, outcomes)
        assert result.explanation
        text = explain_memory_influence(result)
        assert score.strategy_name in text
        assert len(text) > 0


# --------------------------------------------------------------------- #
# 9. Safety boundary
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.memory_intelligence as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(",
                 "PaperBroker", "FyersBroker", "entry_price", "exit_price", "order_id", "position_size")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_or_decision_orchestration_modification():
    """Checks actual CALLS, not disclosure prose -- this package's own
    docstrings legitimately name these functions when explaining what
    they deliberately never call (the same pattern every other phase's
    own safety disclosure uses)."""
    import bujji.memory_intelligence as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        assert "import bujji.broker" not in source
        assert "from bujji.broker" not in source
        assert "import bujji.capital." not in source
        assert "from bujji.capital." not in source
        assert "= compose_decision(" not in source
        assert "def score_strategy(" not in source
        assert "= compose_market_state(" not in source

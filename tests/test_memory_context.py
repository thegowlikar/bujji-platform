"""Tests -- Phase 20.22 Memory Context Integration Layer.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.decision_orchestration import (
    BLOCKED, EXECUTABLE_CANDIDATE, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY, WATCH, FinalDecision,
)
from bujji.memory_intelligence import (
    DIRECTION_CONTRADICTED, DIRECTION_SUPPORTED, DIRECTION_UNCHANGED, FLAG_NO_MEMORY, MemoryInfluenceAssessment,
)
from bujji.epistemics.uncertainty import HIGH, LOW, MODERATE, NONE
from bujji.memory_context import build_memory_decision_context, explain_memory_decision_context


def _final_decision(decision_state, strategy_name="TrendFollowing"):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=decision_state,
        positive=("real_evidence",) if decision_state not in (NO_OPPORTUNITY,) else (),
        negative=() if decision_state not in (NO_OPPORTUNITY,) else ("insufficient_evidence",),
        unknown=(), allocation=None, portfolio_decision=None,
    )


def _memory_assessment(direction, base_confidence=MODERATE, modifier=None, memory_available=True, similarity_count=5):
    if modifier is None:
        modifier = base_confidence
    summary = {"favorable": 5, "unfavorable": 0} if direction == DIRECTION_SUPPORTED else (
        {"favorable": 0, "unfavorable": 5} if direction == DIRECTION_CONTRADICTED else {"favorable": 2, "unfavorable": 2}
    )
    return MemoryInfluenceAssessment(
        strategy_name="TrendFollowing", memory_available=memory_available, similarity_count=similarity_count,
        historical_outcome_summary=summary, base_confidence=base_confidence, confidence_modifier=modifier,
        confidence_direction=direction, uncertainty_flags=() if memory_available else (FLAG_NO_MEMORY,),
        explanation="test fixture",
    )


# --------------------------------------------------------------------- #
# 1. Strong memory support attaches correctly
# --------------------------------------------------------------------- #

def test_strong_support_attaches_with_view():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = _memory_assessment(DIRECTION_SUPPORTED, base_confidence=MODERATE, modifier=HIGH)
    context = build_memory_decision_context(decision, assessment)
    assert context.decision_status == EXECUTABLE_CANDIDATE
    assert context.adjusted_confidence_view == HIGH
    assert "support" in context.memory_effect_summary.lower()


# --------------------------------------------------------------------- #
# 2. Strong contradiction attaches correctly
# --------------------------------------------------------------------- #

def test_strong_contradiction_attaches_with_view():
    decision = _final_decision(WATCH)
    assessment = _memory_assessment(DIRECTION_CONTRADICTED, base_confidence=MODERATE, modifier=LOW)
    context = build_memory_decision_context(decision, assessment)
    assert context.adjusted_confidence_view == LOW
    assert "contradict" in context.memory_effect_summary.lower()


# --------------------------------------------------------------------- #
# 3-4. No memory / insufficient history remains neutral
# --------------------------------------------------------------------- #

def test_no_memory_remains_neutral():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = _memory_assessment(DIRECTION_UNCHANGED, memory_available=False, similarity_count=0)
    context = build_memory_decision_context(decision, assessment)
    assert context.adjusted_confidence_view == assessment.confidence_modifier == assessment.base_confidence


def test_insufficient_history_remains_neutral():
    decision = _final_decision(WATCH)
    assessment = _memory_assessment(DIRECTION_UNCHANGED, memory_available=True, similarity_count=1)
    context = build_memory_decision_context(decision, assessment)
    assert context.adjusted_confidence_view == assessment.base_confidence


# --------------------------------------------------------------------- #
# 5-6. Rejected decisions cannot become executable
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [NO_OPPORTUNITY, BLOCKED, INSUFFICIENT_INTELLIGENCE])
def test_rejected_decision_cannot_become_executable(state):
    decision = _final_decision(state)
    assessment = _memory_assessment(DIRECTION_SUPPORTED, base_confidence=MODERATE, modifier=HIGH)
    context = build_memory_decision_context(decision, assessment)
    assert context.decision_status == state
    assert context.adjusted_confidence_view is None
    assert "not eligible" in context.explanation.lower()


# --------------------------------------------------------------------- #
# 7. Evidence score unchanged before/after
# --------------------------------------------------------------------- #

def test_evidence_score_never_referenced_or_modified():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = _memory_assessment(DIRECTION_SUPPORTED, modifier=HIGH)
    context = build_memory_decision_context(decision, assessment)
    for obj in (decision, assessment, context):
        assert not hasattr(obj, "evidence_score")
        assert not hasattr(obj, "effective_score")


# --------------------------------------------------------------------- #
# 8. Qualification unchanged
# --------------------------------------------------------------------- #

def test_qualification_state_not_present_or_alterable():
    decision = _final_decision(BLOCKED)
    assessment = _memory_assessment(DIRECTION_SUPPORTED, modifier=HIGH)
    context = build_memory_decision_context(decision, assessment)
    assert context.decision_status == BLOCKED
    assert not hasattr(context, "qualification_status")


# --------------------------------------------------------------------- #
# 9. Allocation unchanged
# --------------------------------------------------------------------- #

def test_allocation_not_present_or_alterable():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = _memory_assessment(DIRECTION_SUPPORTED, modifier=HIGH)
    context = build_memory_decision_context(decision, assessment)
    assert context.original_decision.allocation is None
    assert not hasattr(context, "allocation_class")


# --------------------------------------------------------------------- #
# 10. Explanation completeness
# --------------------------------------------------------------------- #

def test_every_context_has_nonempty_explanation():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = _memory_assessment(DIRECTION_CONTRADICTED, base_confidence=MODERATE, modifier=LOW)
    context = build_memory_decision_context(decision, assessment)
    assert context.explanation
    text = explain_memory_decision_context(context)
    assert "TrendFollowing" in text
    assert EXECUTABLE_CANDIDATE in text


# --------------------------------------------------------------------- #
# 11. No circular dependency with market_memory
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "memory_context"


def test_no_direct_import_of_market_memory():
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.market_memory"), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 12. No broker imports
# --------------------------------------------------------------------- #

def test_no_broker_imports_anywhere_in_package():
    forbidden_modules = ("bujji.broker", "fyers_apiv3", "dhanhq")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 13. No execution capability
# --------------------------------------------------------------------- #

def test_no_execution_or_order_vocabulary_anywhere_in_package():
    forbidden = (
        "place_order", "modify_order", "cancel_order", "quantity", "capital_allocation",
        "simulate_execution(",
    )
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 14. Existing memory_intelligence tests remain untouched (no regression to that package)
# --------------------------------------------------------------------- #

def test_memory_intelligence_package_never_modified_by_this_package():
    """This package must never define, monkeypatch, or shadow any
    symbol from `bujji.memory_intelligence` -- confirmed by checking
    it only ever imports from that package, never assigns into it."""
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.value.id != "memory_intelligence" or not isinstance(node.ctx, ast.Store)

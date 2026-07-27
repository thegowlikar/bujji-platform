"""Tests for bujji.msi_position_construction — Engineering Series 95."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_position_construction import config as pc_config
from bujji.msi_position_construction import engine, taxonomy
from bujji.msi_position_construction.journal import PositionConstructionJournal
from bujji.msi_position_construction.runner import construct_positions_batch, PositionConstructionStream
from bujji.msi_position_construction import query as pc_query
from bujji.msi_position_construction import serialization as pc_serialization
from bujji.msi_strategy_expression import engine as se_engine
from bujji.msi_strategy_selector.models import CandidateScore, Explanation as MssExplanation, StrategySelectionAssessment
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

TS = "2026-05-25T15:30:00+05:30"


def _thesis(thesis_type, direction="STRONG_BULLISH", conviction="HIGH"):
    exp = ThesisExplanation(assessment_id="x", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                             what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.2,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation=direction,
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _selection(family):
    exp = MssExplanation(assessment_id="s1", why_this_strategy=(), why_not_alternatives=(),
                          evidence_that_mattered_most=(), evidence_that_prevented_alternatives=(),
                          active_market_states=(), schema_version="1.0.0")
    return StrategySelectionAssessment(
        assessment_id="s1", timestamp=TS, selected_strategy_family=family, alternative_candidates=(),
        rejection_reasons=(), supporting_evidence=(), confidence="HIGH", explanation=exp,
        provenance="p", schema_version="1.0.0",
    )


def _construct(family, thesis_type="TREND_CONTINUATION", direction="STRONG_BULLISH", conviction="HIGH"):
    thesis = _thesis(thesis_type, direction, conviction)
    expression = se_engine.derive_strategy_expression(thesis, timestamp=TS)
    selection = _selection(family)
    return engine.construct_position(selection, expression, thesis, timestamp=TS)


# --- Deliverable 4: basic construction across families -------------------

def test_no_family_selected_produces_none_construction():
    a = _construct(None)
    assert a.construction_type == taxonomy.CONSTRUCTION_NONE
    assert a.selected_strategy_family is None
    assert a.risk_profile == taxonomy.RISK_UNKNOWN


def test_long_directional_default_shape_is_single_leg_at_high_conviction():
    a = _construct("LONG_DIRECTIONAL", conviction="HIGH")
    assert a.construction_type == taxonomy.CONSTRUCTION_SINGLE_LEG
    assert a.risk_profile == taxonomy.RISK_DEFINED


def test_long_directional_refines_to_debit_spread_at_moderate_conviction():
    a = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    assert a.construction_type == taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD
    assert a.risk_profile == taxonomy.RISK_DEFINED
    assert a.adjustment_readiness == taxonomy.ADJUSTMENT_FRIENDLY


def test_iron_condor_default_shape_unaffected_by_conviction():
    a_high = _construct("IRON_CONDOR", thesis_type="RANGE_PERSISTENCE", direction="NEUTRAL", conviction="HIGH")
    a_low = _construct("IRON_CONDOR", thesis_type="RANGE_PERSISTENCE", direction="NEUTRAL", conviction="LOW")
    assert a_high.construction_type == a_low.construction_type == taxonomy.CONSTRUCTION_IRON_CONDOR_SHAPE
    assert a_high.risk_profile == taxonomy.RISK_DEFINED


# --- Deliverable 7: multiple construction styles for one family; never
# contradicts the family; never bypasses expression ------------------------

def test_long_directional_produces_multiple_construction_styles_depending_on_thesis():
    high = _construct("LONG_DIRECTIONAL", conviction="HIGH")
    moderate = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    assert high.construction_type != moderate.construction_type
    assert {high.construction_type, moderate.construction_type} == {
        taxonomy.CONSTRUCTION_SINGLE_LEG, taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD,
    }


def test_construction_never_produces_undefined_risk_for_a_refined_shape():
    a = _construct("SHORT_DIRECTIONAL", conviction="MODERATE")
    # SHORT_DIRECTIONAL's own expression rejects DEFINED_RISK requirement
    # unless the thesis demands it; verify the refinement rule fires
    # correctly when it does (constructed independent of the SSF/Selector
    # suitability gate, which is a separate, real concern this test
    # doesn't need to reproduce).
    assert a.construction_type in (taxonomy.CONSTRUCTION_SINGLE_LEG, taxonomy.CONSTRUCTION_VERTICAL_CREDIT_SPREAD)
    if a.construction_type == taxonomy.CONSTRUCTION_VERTICAL_CREDIT_SPREAD:
        assert a.risk_profile == taxonomy.RISK_DEFINED


def test_construction_never_bypasses_strategy_expression_signals():
    thesis = _thesis("RANGE_PERSISTENCE", direction="NEUTRAL", conviction="HIGH")
    expression = se_engine.derive_strategy_expression(thesis, timestamp=TS)
    selection = _selection("IRON_CONDOR")
    a = engine.construct_position(selection, expression, thesis, timestamp=TS)
    assert a.expected_theta == taxonomy.SIGN_POSITIVE  # expression.desired_time_decay == POSITIVE_THETA for RANGE_PERSISTENCE.
    assert a.expected_vega == taxonomy.SIGN_NEGATIVE    # expression.desired_volatility_exposure == SHORT_VOLATILITY.


# --- Deliverable 5: explainability -----------------------------------------

def test_explanation_answers_all_required_questions():
    a = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    assert a.explanation.why_this_construction_style
    assert a.explanation.why_this_expiry_philosophy
    assert a.explanation.why_this_strike_philosophy
    assert a.explanation.evidence_that_drove_the_design


def test_reasoning_never_cites_historical_performance():
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit")
    a = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    text = " ".join(a.explanation.why_this_construction_style + a.explanation.evidence_that_drove_the_design).lower()
    for word in forbidden:
        assert word not in text


# --- Deliverable 1: reuse verification -------------------------------------

def test_expiry_dte_window_reused_verbatim_from_series_90():
    from bujji.msi_trade_construction import config as tc_config
    assert pc_config.DEFAULT_MIN_DTE == tc_config.DEFAULT_MIN_DTE
    assert pc_config.DEFAULT_MAX_DTE == tc_config.DEFAULT_MAX_DTE


def test_delta_targets_reused_verbatim_from_series_90():
    from bujji.msi_trade_construction import config as tc_config
    assert pc_config.FAMILY_DELTA_TARGETS is tc_config.FAMILY_DELTA_TARGETS


def test_strike_plan_uses_real_series_90_delta_target():
    a = _construct("IRON_CONDOR", thesis_type="RANGE_PERSISTENCE", direction="NEUTRAL")
    from bujji.msi_trade_construction import config as tc_config
    assert a.strike_plan.target_delta == tc_config.FAMILY_DELTA_TARGETS["IRON_CONDOR"]


# --- Determinism / immutability / batch-streaming parity -------------------

def test_determinism_identical_input_identical_id():
    a1 = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    a2 = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    assert a1.assessment_id == a2.assessment_id


def test_assessment_is_immutable():
    a = _construct("LONG_DIRECTIONAL")
    with pytest.raises(Exception):
        a.construction_type = "X"


def test_batch_and_streaming_are_byte_identical():
    def _req(family, conviction):
        thesis = _thesis("TREND_CONTINUATION", conviction=conviction)
        expression = se_engine.derive_strategy_expression(thesis, timestamp=TS)
        selection = _selection(family)
        return dict(selection=selection, expression=expression, thesis=thesis, timestamp=TS)

    requests = [_req("LONG_DIRECTIONAL", "HIGH"), _req("LONG_DIRECTIONAL", "MODERATE")]
    batch = construct_positions_batch(requests)
    stream = PositionConstructionStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query --------------------------------------------------

def test_serialization_round_trip():
    a = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    d = pc_serialization.assessment_to_dict(a)
    assert d["assessment_id"] == a.assessment_id
    assert d["construction_type"] == taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD


def test_query_helpers():
    a1 = _construct("LONG_DIRECTIONAL", conviction="HIGH")
    a2 = _construct("LONG_DIRECTIONAL", conviction="MODERATE")
    assessments = (a1, a2)
    assert pc_query.by_id(assessments, a1.assessment_id) is a1
    assert a2 in pc_query.by_construction_type(assessments, taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD)
    assert set(pc_query.by_family(assessments, "LONG_DIRECTIONAL")) == {a1, a2}


def test_journal_is_append_only():
    a = _construct("LONG_DIRECTIONAL")
    j = PositionConstructionJournal()
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) -------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_position_construction")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.options_observation", "bujji.intelligence")
    for path in _pkg_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name.startswith(m) for m in forbidden_modules), f"{path} imports {name}"


def test_ast_no_optimization_vocabulary_or_uuid4_or_randomness():
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in ("optimi", "backtest", "pnl"):
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

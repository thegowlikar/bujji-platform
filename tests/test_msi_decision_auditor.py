"""Tests for bujji.msi_decision_auditor — Engineering Series 99."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_decision_auditor import engine, taxonomy
from bujji.msi_decision_auditor.journal import DecisionAuditorJournal
from bujji.msi_decision_auditor.runner import DecisionAuditorStream, build_decisions_batch, build_outcomes_batch
from bujji.msi_decision_auditor import query as da_query
from bujji.msi_decision_auditor import serialization as da_serialization
from bujji.msi_portfolio_construction.models import Explanation as PcExplanation, PortfolioConstructionAssessment
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

TS = "2026-05-25T15:30:00+05:30"
DAY = "2026-05-25"


def _thesis(thesis_type, conviction="HIGH"):
    exp = ThesisExplanation(assessment_id="t1", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                            what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.0,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation="NEUTRAL",
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _portfolio(approval_state="APPROVED"):
    exp = PcExplanation(assessment_id="p1", why_approved=(), why_rejected=(), dominant_constraint=None,
                        what_would_change_for_approval=(), schema_version="1.0.0")
    return PortfolioConstructionAssessment(
        assessment_id="p1", timestamp=TS, proposed_trade_assessment_id="x", strategy_family="LONG_DIRECTIONAL",
        approval_state=approval_state, rejection_reasons=() if approval_state == "APPROVED" else ("UNDEFINED_RISK_POLICY",),
        required_margin=None, estimated_margin=100000.0, portfolio_delta_after=50.0, portfolio_gamma_after=0.01,
        portfolio_theta_after=10.0, portfolio_vega_after=100.0, concentration_after=(), capital_required=100000.0,
        capital_available=900000.0, risk_budget_used=0.1, position_size_lots=1, confidence="HIGH",
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _decision(thesis_type="RANGE_PERSISTENCE", conviction="HIGH", family=None, portfolio=None):
    return engine.build_decision_record(
        DAY, ("obs1", "obs2"), ("ep1",), "NEUTRAL", "WEAK_CONSENSUS", "STABLE",
        _thesis(thesis_type, conviction), family, None, portfolio, None, None, None, timestamp=TS,
    )


# --- Deliverable 2: DecisionRecord, immutability -----------------------

def test_decision_record_is_fully_immutable():
    d = _decision()
    with pytest.raises(Exception):
        d.decision_outcome = "X"
    with pytest.raises(Exception):
        d.explanation.why = ()


def test_no_trade_recorded_when_no_family_selected():
    d = _decision(family=None, portfolio=None)
    assert d.decision_outcome == taxonomy.DECISION_NO_TRADE


def test_trade_approved_recorded_when_portfolio_approves():
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    assert d.decision_outcome == taxonomy.DECISION_TRADE_APPROVED


def test_no_trade_recorded_when_portfolio_rejects():
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("REJECTED"))
    assert d.decision_outcome == taxonomy.DECISION_NO_TRADE


def test_confidence_is_real_thesis_conviction_passthrough():
    d = _decision(conviction="LOW")
    assert d.confidence == "LOW"


# --- Deliverable 3: journal records BOTH approved and no-trade ------------

def test_journal_records_both_no_trade_and_approved():
    j = DecisionAuditorJournal()
    d1 = _decision(family=None)
    d2 = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    j.record_decision(d1, recorded_at=TS)
    j.record_decision(d2, recorded_at=TS)
    assert len(j) == 2
    kinds = [e.payload["decision_outcome"] for e in j.entries()]
    assert taxonomy.DECISION_NO_TRADE in kinds
    assert taxonomy.DECISION_TRADE_APPROVED in kinds


# --- Deliverable 4: OutcomeRecord --------------------------------------

def test_outcome_records_real_movement_and_direction():
    o = engine.build_outcome_record(DAY, [24000.0, 24050.0, 24310.0], "RANGE_PERSISTENCE", "BREAKOUT", False, timestamp=TS)
    assert o.close_price == 24310.0
    assert o.session_high == 24310.0
    assert o.session_low == 24000.0
    assert o.realised_direction == taxonomy.REALISED_UP
    assert o.realised_movement_pct > 0


def test_outcome_with_no_closes_is_honest_not_guessed():
    o = engine.build_outcome_record(DAY, [], "RANGE_PERSISTENCE", None, False, timestamp=TS)
    assert o.close_price is None
    assert o.thesis_survival == taxonomy.SURVIVAL_UNKNOWN


def test_thesis_survival_unknown_without_next_day_data():
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", None, False, timestamp=TS)
    assert o.thesis_survival == taxonomy.SURVIVAL_UNKNOWN


def test_thesis_survival_survived_on_compatible_transition():
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", "MEAN_REVERSION", False, timestamp=TS)
    assert o.thesis_survival == "SURVIVED"


def test_thesis_survival_invalidated_on_incompatible_transition():
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", "BREAKOUT", False, timestamp=TS)
    assert o.thesis_survival == "INVALIDATED"


def test_execution_feasibility_reflects_real_plan_presence():
    o1 = engine.build_outcome_record(DAY, [24000.0], "RANGE_PERSISTENCE", None, True, timestamp=TS)
    o2 = engine.build_outcome_record(DAY, [24000.0], "RANGE_PERSISTENCE", None, False, timestamp=TS)
    assert o1.execution_feasibility == taxonomy.FEASIBILITY_PLAN_PRODUCED
    assert o2.execution_feasibility == taxonomy.FEASIBILITY_NO_PLAN


# --- Deliverable 5: Decision <-> Outcome link, no fuzzy matching --------

def test_link_produces_deterministic_pair_id():
    d = _decision()
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", None, False, timestamp=TS)
    pair1 = engine.link(d, o)
    pair2 = engine.link(d, o)
    assert pair1.pair_id == pair2.pair_id


def test_link_refuses_mismatched_dates():
    d = _decision()
    o = engine.build_outcome_record("2026-05-26", [24000.0], "RANGE_PERSISTENCE", None, False, timestamp=TS)
    with pytest.raises(ValueError):
        engine.link(d, o)


# --- Deliverable 7: explainability ---------------------------------------

def test_decision_explanation_answers_why():
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    assert d.explanation.why


def test_outcome_explanation_answers_what_happened():
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", "MEAN_REVERSION", False, timestamp=TS)
    assert o.explanation.what_actually_happened


def test_reasoning_never_cites_historical_performance():
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit factor", "should have")
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    o = engine.build_outcome_record(DAY, [24000.0, 24010.0], "RANGE_PERSISTENCE", "MEAN_REVERSION", False, timestamp=TS)
    text = " ".join(d.explanation.why + o.explanation.what_actually_happened).lower()
    for word in forbidden:
        assert word not in text


# --- Deliverable 8: dashboard views (no scoring) --------------------------

def test_daily_view_contains_no_trade_family_confidence():
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    view = da_query.daily_view(d)
    assert "RANGE_PERSISTENCE" in view
    assert "LONG_DIRECTIONAL" in view
    assert "HIGH" in view


def test_portfolio_view_counts_no_trade_vs_trade():
    records = [_decision(family=None), _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))]
    view = da_query.portfolio_view(records)
    assert "2 Decisions" in view
    assert "1 No Trade" in view
    assert "1 Trade" in view


# --- Determinism / batch-streaming -----------------------------------------

def test_batch_and_streaming_decisions_byte_identical():
    requests = [
        dict(date=DAY, observation_ids=("o1",), episode_ids=("e1",), market_direction="NEUTRAL",
             consensus="WEAK_CONSENSUS", volatility_state="STABLE", trade_thesis=_thesis("RANGE_PERSISTENCE"),
             strategy_family=None, position_construction=None, portfolio_decision=None, lifecycle_state=None,
             margin_assessment=None, execution_plan=None, timestamp=TS),
    ]
    batch = build_decisions_batch(requests)
    stream = DecisionAuditorStream()
    streamed = tuple(stream.submit_decision(**r) for r in requests)
    assert [d.decision_id for d in batch] == [d.decision_id for d in streamed]
    assert len(stream.journal) == len(requests)


def test_stream_pairs_decision_and_outcome():
    stream = DecisionAuditorStream()
    d = stream.submit_decision(
        date=DAY, observation_ids=(), episode_ids=(), market_direction="NEUTRAL", consensus="WEAK_CONSENSUS",
        volatility_state="STABLE", trade_thesis=_thesis("RANGE_PERSISTENCE"), strategy_family=None,
        position_construction=None, portfolio_decision=None, lifecycle_state=None, margin_assessment=None,
        execution_plan=None, timestamp=TS,
    )
    o = stream.submit_outcome(date=DAY, closes=[24000.0, 24010.0], entry_thesis_type="RANGE_PERSISTENCE",
                             next_day_thesis_type=None, execution_plan_present=False, timestamp=TS)
    pair = stream.submit_pair(d, o)
    assert pair.decision is d
    assert pair.outcome is o
    assert len(stream.journal) == 3


# --- Serialization / query ---------------------------------------------

def test_serialization_round_trip():
    d = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    payload = da_serialization.decision_record_to_dict(d)
    assert payload["decision_id"] == d.decision_id
    assert payload["decision_outcome"] == taxonomy.DECISION_TRADE_APPROVED


def test_query_helpers():
    d1 = _decision(family=None)
    d2 = _decision(family="LONG_DIRECTIONAL", portfolio=_portfolio("APPROVED"))
    records = (d1, d2)
    assert da_query.by_id(records, d1.decision_id) is d1
    assert da_query.no_trade_only(records) == (d1,)
    assert da_query.approved_only(records) == (d2,)


# --- AST isolation (house convention) -----------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_decision_auditor")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.broker", "bujji.execution", "bujji.journal")
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


def test_ast_no_learning_ml_or_scoring_vocabulary():
    forbidden_fragments = ("optimi", "backtest", "pnl", "score", "rank", "reinforce", "reward", "train", "model.fit")
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in forbidden_fragments:
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

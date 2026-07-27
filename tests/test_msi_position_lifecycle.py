"""Tests for bujji.msi_position_lifecycle — Engineering Series 96."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_position_lifecycle import config as pli_config
from bujji.msi_position_lifecycle import engine, taxonomy
from bujji.msi_position_lifecycle.journal import PositionLifecycleJournal
from bujji.msi_position_lifecycle.runner import assess_lifecycles_batch, PositionLifecycleStream
from bujji.msi_position_lifecycle import query as pli_query
from bujji.msi_position_lifecycle import serialization as pli_serialization
from bujji.msi_portfolio_construction.models import Explanation as PcExplanation, PortfolioConstructionAssessment
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

TS = "2026-05-25T15:30:00+05:30"
ENTRY_DATE = "2026-05-25"
CLOSE_DATE = "2026-06-01"


def _thesis(thesis_type, conviction="HIGH", vol="STABLE"):
    exp = ThesisExplanation(assessment_id="x", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                             what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.2,
        expected_time_horizon="NEXT_SESSION", volatility_expectation=vol, directional_expectation="NEUTRAL",
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _portfolio(delta=50.0, vega=100.0):
    exp = PcExplanation(assessment_id="p1", why_approved=(), why_rejected=(), dominant_constraint=None,
                        what_would_change_for_approval=(), schema_version="1.0.0")
    return PortfolioConstructionAssessment(
        assessment_id="p1", timestamp=TS, proposed_trade_assessment_id="x", strategy_family="IRON_CONDOR",
        approval_state="APPROVED", rejection_reasons=(), required_margin=None, estimated_margin=100000.0,
        portfolio_delta_after=delta, portfolio_gamma_after=0.01, portfolio_theta_after=10.0,
        portfolio_vega_after=vega, concentration_after=(), capital_required=100000.0, capital_available=900000.0,
        risk_budget_used=0.1, position_size_lots=1, confidence="HIGH", explanation=exp, provenance="p",
        schema_version="1.0.0",
    )


def _assess(entry, current, family="IRON_CONDOR", construction_type="IRON_CONDOR_SHAPE",
            portfolio=None, current_date="2026-05-27", close_date=CLOSE_DATE):
    return engine.assess_position_lifecycle(
        entry, current, family, construction_type, portfolio if portfolio is not None else _portfolio(),
        ENTRY_DATE, current_date, close_date, timestamp=TS,
    )


# --- Deliverable 3: lifecycle states ---------------------------------------

def test_newly_opened_on_entry_date():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry, current_date=ENTRY_DATE)
    assert a.position_state == taxonomy.STATE_NEWLY_OPENED


def test_healthy_when_thesis_unchanged_and_conviction_steady():
    entry = _thesis("RANGE_PERSISTENCE", conviction="HIGH")
    current = _thesis("RANGE_PERSISTENCE", conviction="HIGH")
    a = _assess(entry, current)
    assert a.position_state == taxonomy.STATE_HEALTHY
    assert a.thesis_invalidation.compatible is True


def test_improving_when_conviction_increases():
    entry = _thesis("RANGE_PERSISTENCE", conviction="MODERATE")
    current = _thesis("RANGE_PERSISTENCE", conviction="HIGH")
    a = _assess(entry, current)
    assert a.position_state == taxonomy.STATE_IMPROVING


def test_at_risk_when_conviction_decreases():
    entry = _thesis("RANGE_PERSISTENCE", conviction="HIGH")
    current = _thesis("RANGE_PERSISTENCE", conviction="LOW")
    a = _assess(entry, current)
    assert a.position_state == taxonomy.STATE_AT_RISK


def test_thesis_broken_on_incompatible_transition():
    entry = _thesis("RANGE_PERSISTENCE")
    current = _thesis("BREAKOUT")
    a = _assess(entry, current)
    assert a.position_state == taxonomy.STATE_THESIS_BROKEN
    assert a.thesis_invalidation.compatible is False


def test_event_risk_resolving_into_a_real_thesis_is_not_broken():
    entry = _thesis("EVENT_RISK")
    current = _thesis("TREND_CONTINUATION")
    a = _assess(entry, current)
    assert a.thesis_invalidation.compatible is True


def test_event_risk_falling_back_to_no_trade_is_broken():
    entry = _thesis("EVENT_RISK")
    current = _thesis("NO_TRADE")
    a = _assess(entry, current)
    assert a.thesis_invalidation.compatible is False


def test_adjustment_candidate_on_delta_drift():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry, portfolio=_portfolio(delta=350.0))
    assert a.position_state == taxonomy.STATE_ADJUSTMENT_CANDIDATE
    assert taxonomy.TRIGGER_DELTA_DRIFT in a.adjustment_policy.fired


def test_profit_harvest_near_expiry_for_credit_shape():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry, current_date="2026-05-31")  # DTE = 1
    assert a.position_state == taxonomy.STATE_PROFIT_HARVEST
    assert a.expiry_policy.dte_remaining == 1


def test_profit_harvest_not_triggered_for_non_early_harvest_shape_near_expiry():
    entry = _thesis("TREND_CONTINUATION")
    a = _assess(entry, entry, family="LONG_DIRECTIONAL", construction_type="SINGLE_LEG", current_date="2026-05-31")
    assert a.position_state != taxonomy.STATE_PROFIT_HARVEST


def test_closed_after_position_close_date():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry, current_date="2026-06-05")
    assert a.position_state == taxonomy.STATE_CLOSED


def test_exit_candidate_when_thesis_broken_and_hard_exposure_breach():
    entry = _thesis("RANGE_PERSISTENCE")
    current = _thesis("BREAKOUT")
    breach_portfolio = _portfolio(delta=600.0)  # exceeds HARD_MAX_ABS_PORTFOLIO_DELTA (500)
    a = _assess(entry, current, portfolio=breach_portfolio)
    assert a.position_state == taxonomy.STATE_EXIT_CANDIDATE
    assert a.emergency_policy.trigger == "PORTFOLIO_EXPOSURE_HARD_BREACH"


# --- Deliverable 4: adjustment policies distinguish actionable vs
# monitoring-only triggers ---------------------------------------------------

def test_iron_condor_declares_wing_breach_as_monitoring_only():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    assert taxonomy.TRIGGER_WING_BREACH in a.adjustment_policy.monitoring_only_triggers
    assert taxonomy.TRIGGER_WING_BREACH not in a.adjustment_policy.actionable_triggers_today


def test_calendar_declares_term_structure_collapse_as_monitoring_only():
    entry = _thesis("VOLATILITY_EXPANSION")
    a = _assess(entry, entry, family="CALENDAR", construction_type="CALENDAR_SHAPE")
    assert taxonomy.TRIGGER_TERM_STRUCTURE_COLLAPSE in a.adjustment_policy.monitoring_only_triggers


# --- Deliverable 5: thesis monitoring never infers silently -----------------

def test_thesis_invalidation_always_states_both_thesis_types():
    entry = _thesis("RANGE_PERSISTENCE")
    current = _thesis("TREND_CONTINUATION")
    a = _assess(entry, current)
    assert a.thesis_invalidation.entry_thesis_type == "RANGE_PERSISTENCE"
    assert a.thesis_invalidation.current_thesis_type == "TREND_CONTINUATION"


# --- Deliverable 7: explainability -------------------------------------------

def test_explanation_answers_all_required_questions():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    assert a.explanation.why_this_adjustment_policy
    assert a.explanation.why_this_profit_policy
    assert a.explanation.why_this_invalidation_rule
    assert a.explanation.why_this_emergency_policy


def test_reasoning_never_cites_historical_performance():
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit factor")
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    text = " ".join(
        a.explanation.why_this_adjustment_policy + a.explanation.why_this_profit_policy +
        a.explanation.why_this_invalidation_rule + a.explanation.why_this_emergency_policy
    ).lower()
    for word in forbidden:
        assert word not in text


# --- Determinism / immutability / batch-streaming --------------------------

def test_determinism_identical_input_identical_id():
    entry = _thesis("RANGE_PERSISTENCE")
    a1 = _assess(entry, entry)
    a2 = _assess(entry, entry)
    assert a1.lifecycle_id == a2.lifecycle_id


def test_assessment_is_immutable():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    with pytest.raises(Exception):
        a.position_state = "X"


def test_batch_and_streaming_are_byte_identical():
    entry = _thesis("RANGE_PERSISTENCE")
    requests = [
        dict(entry_thesis=entry, current_thesis=entry, strategy_family="IRON_CONDOR",
             construction_type="IRON_CONDOR_SHAPE", portfolio=_portfolio(), entry_date=ENTRY_DATE,
             current_date="2026-05-27", position_close_date=CLOSE_DATE, timestamp=TS),
        dict(entry_thesis=entry, current_thesis=_thesis("BREAKOUT"), strategy_family="IRON_CONDOR",
             construction_type="IRON_CONDOR_SHAPE", portfolio=_portfolio(), entry_date=ENTRY_DATE,
             current_date="2026-05-28", position_close_date=CLOSE_DATE, timestamp=TS),
    ]
    batch = assess_lifecycles_batch(requests)
    stream = PositionLifecycleStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.lifecycle_id for a in batch] == [a.lifecycle_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query --------------------------------------------------

def test_serialization_round_trip():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    d = pli_serialization.assessment_to_dict(a)
    assert d["lifecycle_id"] == a.lifecycle_id
    assert d["position_state"] == taxonomy.STATE_HEALTHY


def test_query_helpers():
    entry = _thesis("RANGE_PERSISTENCE")
    a1 = _assess(entry, entry)
    a2 = _assess(entry, _thesis("BREAKOUT"))
    assessments = (a1, a2)
    assert pli_query.by_id(assessments, a1.lifecycle_id) is a1
    assert a2 in pli_query.by_state(assessments, taxonomy.STATE_THESIS_BROKEN)


def test_journal_is_append_only():
    entry = _thesis("RANGE_PERSISTENCE")
    a = _assess(entry, entry)
    j = PositionLifecycleJournal()
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 2


# --- Reuse verification (Deliverable 1) -------------------------------------

def test_watch_thresholds_are_fraction_of_series_91_hard_thresholds():
    from bujji.msi_portfolio_construction import config as prc_config
    assert pli_config.WATCH_ABS_PORTFOLIO_DELTA < prc_config.MAX_ABS_PORTFOLIO_DELTA
    assert pli_config.HARD_MAX_ABS_PORTFOLIO_DELTA == prc_config.MAX_ABS_PORTFOLIO_DELTA


# --- AST isolation (house convention) --------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_position_lifecycle")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.trade.manager", "bujji.options_observation")
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
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

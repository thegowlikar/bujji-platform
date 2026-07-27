"""Tests for bujji.msi_margin_bridge — Engineering Series 97."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.capital.models import MarginRequirement
from bujji.msi_margin_bridge import engine, taxonomy
from bujji.msi_margin_bridge.journal import MarginBridgeJournal
from bujji.msi_margin_bridge.runner import estimate_margins_batch, MarginBridgeStream
from bujji.msi_margin_bridge import query as mb_query
from bujji.msi_margin_bridge import serialization as mb_serialization
from bujji.msi_trade_construction import engine as tc_engine
from bujji.options_observation import runner as opt_runner

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"
LOT_SIZE = 75


def _real_chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain():
    return _real_chain()


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def _construct(chain, spot, family):
    return tc_engine.construct_trade(family, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)


# --- Deliverable 3: replay path, per risk-profile methodology -------------

def test_debit_defined_risk_construction_is_exact(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.methodology == taxonomy.METHODOLOGY_DEBIT_MAX_LOSS_EXACT
    assert est.confidence == taxonomy.CONFIDENCE_EXACT
    assert est.estimated_margin == pytest.approx(abs(trade.expected_credit_debit) * LOT_SIZE)
    assert est.replay_safe is True


def test_credit_defined_risk_construction_uses_wing_width_formula(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.methodology == taxonomy.METHODOLOGY_CREDIT_SPREAD_MAX_LOSS_EXACT
    assert est.confidence == taxonomy.CONFIDENCE_EXACT
    assert est.estimated_margin is not None and est.estimated_margin > 0


def test_undefined_risk_construction_is_approximate(chain, spot):
    trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.confidence == taxonomy.CONFIDENCE_APPROXIMATE
    assert est.methodology in (taxonomy.METHODOLOGY_NOTIONAL_PERCENTAGE_APPROXIMATION,
                               taxonomy.METHODOLOGY_PREMIUM_MULTIPLE_APPROXIMATION)


def test_undefined_risk_uses_the_higher_of_the_two_candidates(chain, spot):
    trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    from bujji.msi_margin_bridge import config as mb_config
    premium_estimate = abs(trade.expected_credit_debit) * LOT_SIZE * mb_config.UNDEFINED_RISK_PREMIUM_MULTIPLE
    notional_estimate = spot * LOT_SIZE * mb_config.UNDEFINED_RISK_NOTIONAL_PERCENTAGE
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.estimated_margin == pytest.approx(max(premium_estimate, notional_estimate))


def test_unconstructed_trade_is_unknown():
    trade = None
    est = engine.estimate_margin(trade, LOT_SIZE, 24000.0, timestamp=TS)
    assert est.confidence == taxonomy.CONFIDENCE_UNKNOWN
    assert est.estimated_margin is None
    assert est.replay_safe is True


def test_undefined_risk_with_no_spot_and_no_premium_is_unknown(chain, spot):
    trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    from dataclasses import replace
    trade_no_credit = replace(trade, expected_credit_debit=None)
    est = engine.estimate_margin(trade_no_credit, LOT_SIZE, None, timestamp=TS)
    assert est.confidence == taxonomy.CONFIDENCE_UNKNOWN
    assert est.estimated_margin is None


# --- Deliverable 3: production path ----------------------------------------

def test_production_certified_margin_is_exact():
    live = MarginRequirement(margin_per_lot=125000.0, verified=True, source="fyers_span_margin_certified", as_of=None)
    est = engine.estimate_margin(live_margin_requirement=live, timestamp=TS)
    assert est.methodology == taxonomy.METHODOLOGY_LIVE_BROKER_SPAN
    assert est.confidence == taxonomy.CONFIDENCE_EXACT
    assert est.estimated_margin == 125000.0
    assert est.replay_safe is False
    assert est.data_source == taxonomy.DATA_SOURCE_LIVE_BROKER_SPAN


def test_production_uncertified_margin_is_approximate_never_silently_upgraded():
    live = MarginRequirement(margin_per_lot=125000.0, verified=False, source="fyers_span_margin", as_of=None)
    est = engine.estimate_margin(live_margin_requirement=live, timestamp=TS)
    assert est.confidence == taxonomy.CONFIDENCE_APPROXIMATE


def test_production_missing_margin_is_unknown_not_guessed():
    live = MarginRequirement(margin_per_lot=None, verified=False, source="unavailable", as_of=None)
    est = engine.estimate_margin(live_margin_requirement=live, timestamp=TS)
    assert est.confidence == taxonomy.CONFIDENCE_UNKNOWN
    assert est.estimated_margin is None


# --- Deliverable 8: replay never calls live broker APIs; both share the
# same interface ------------------------------------------------------------

def test_replay_path_never_touches_live_broker_requirement(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.data_source != taxonomy.DATA_SOURCE_LIVE_BROKER_SPAN
    assert est.replay_safe is True


def test_both_paths_return_the_identical_model_type(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    replay_est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    live = MarginRequirement(margin_per_lot=125000.0, verified=True, source="x", as_of=None)
    prod_est = engine.estimate_margin(live_margin_requirement=live, timestamp=TS)
    assert type(replay_est) is type(prod_est)
    assert set(vars(replay_est)) == set(vars(prod_est))


# --- Deliverable 6: explainability ------------------------------------------

def test_explanation_answers_all_required_questions(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    assert est.explanation.why_this_estimate_exists
    assert est.explanation.why_exact_margin_is_or_isnt_available
    assert est.explanation.assumptions_required


def test_reasoning_never_cites_historical_performance(chain, spot):
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit")
    for family in ("LONG_DIRECTIONAL", "IRON_CONDOR", "SHORT_DIRECTIONAL"):
        trade = _construct(chain, spot, family)
        est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
        text = " ".join(est.explanation.why_this_estimate_exists + est.explanation.assumptions_required).lower()
        for word in forbidden:
            assert word not in text


# --- Determinism / immutability / batch-streaming --------------------------

def test_determinism_identical_input_identical_id(chain, spot):
    trade1 = _construct(chain, spot, "IRON_CONDOR")
    trade2 = _construct(chain, spot, "IRON_CONDOR")
    est1 = engine.estimate_margin(trade1, LOT_SIZE, spot, timestamp=TS)
    est2 = engine.estimate_margin(trade2, LOT_SIZE, spot, timestamp=TS)
    assert est1.assessment_id == est2.assessment_id


def test_assessment_is_immutable(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    with pytest.raises(Exception):
        est.confidence = "X"


def test_batch_and_streaming_are_byte_identical(chain, spot):
    trades = [_construct(chain, spot, f) for f in ("LONG_DIRECTIONAL", "IRON_CONDOR", "SHORT_DIRECTIONAL")]
    requests = [dict(trade=t, lot_size=LOT_SIZE, spot=spot, timestamp=TS) for t in trades]
    batch = estimate_margins_batch(requests)
    stream = MarginBridgeStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query --------------------------------------------------

def test_serialization_round_trip(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    d = mb_serialization.assessment_to_dict(est)
    assert d["assessment_id"] == est.assessment_id
    assert d["confidence"] == taxonomy.CONFIDENCE_EXACT


def test_query_helpers(chain, spot):
    est1 = engine.estimate_margin(_construct(chain, spot, "IRON_CONDOR"), LOT_SIZE, spot, timestamp=TS)
    est2 = engine.estimate_margin(_construct(chain, spot, "SHORT_DIRECTIONAL"), LOT_SIZE, spot, timestamp=TS)
    assessments = (est1, est2)
    assert mb_query.by_id(assessments, est1.assessment_id) is est1
    assert est1 in mb_query.by_confidence(assessments, taxonomy.CONFIDENCE_EXACT)
    assert est2 in mb_query.by_confidence(assessments, taxonomy.CONFIDENCE_APPROXIMATE)
    assert set(mb_query.replay_safe_only(assessments)) == {est1, est2}


def test_journal_is_append_only(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    est = engine.estimate_margin(trade, LOT_SIZE, spot, timestamp=TS)
    j = MarginBridgeJournal()
    j.record_assessment(est, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(est, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) --------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_margin_bridge")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.broker", "bujji.msi_portfolio_construction", "bujji.msi_position_lifecycle")
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

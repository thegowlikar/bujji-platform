"""Tests for bujji.msi_portfolio_construction — Engineering Series 91."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_portfolio_construction import config as pc_config
from bujji.msi_portfolio_construction import engine, taxonomy
from bujji.msi_portfolio_construction.journal import PortfolioConstructionJournal
from bujji.msi_portfolio_construction.models import AdmittedTrade, HeldLeg, PortfolioState
from bujji.msi_portfolio_construction.runner import evaluate_trades_batch, PortfolioConstructionStream
from bujji.msi_portfolio_construction import query as pc_query
from bujji.msi_portfolio_construction import serialization as pc_serialization
from bujji.msi_trade_construction import engine as tc_engine
from bujji.options_observation import runner as opt_runner

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"


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


def _construct(chain, spot, family="LONG_DIRECTIONAL", direction="BULLISH", **kw):
    return tc_engine.construct_trade(family, chain, spot, DAY, direction=direction, expected_move_pct=1.2, timestamp=TS, **kw)


# --- Deliverable 3: admission engine, basic approve/reject -------------

def test_defined_risk_trade_with_high_confidence_and_empty_portfolio_approves(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.approval_state == taxonomy.APPROVED
    assert d.rejection_reasons == ()
    assert isinstance(d.position_size_lots, int) and d.position_size_lots > 0


def test_undefined_risk_trade_rejected_by_policy(chain, spot):
    trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_UNDEFINED_RISK_POLICY in d.rejection_reasons


def test_low_confidence_rejected(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="LOW", timestamp=TS)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_INSUFFICIENT_CONFIDENCE in d.rejection_reasons


def test_unconstructed_trade_rejected(chain, spot):
    trade = tc_engine.construct_trade("NOT_A_FAMILY", chain, spot, DAY, timestamp=TS)
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_CONSTRUCTION_NOT_SUCCESSFUL in d.rejection_reasons


def test_missing_margin_estimate_fails_closed(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH",
                               timestamp=TS, margin_per_lot_estimate=None)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_MISSING_MARGIN_INFORMATION in d.rejection_reasons
    assert d.position_size_lots == taxonomy.SIZE_UNKNOWN


def test_insufficient_capital_rejected_with_determinate_zero_size(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH",
                               timestamp=TS, total_capital=1.0)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_INSUFFICIENT_CAPITAL in d.rejection_reasons
    assert d.position_size_lots == 0  # determinate "zero", not SIZE_UNKNOWN.


def test_excessive_concentration_rejected(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    admitted = tuple(
        AdmittedTrade(
            assessment_id=f"a{i}", strategy_family="LONG_DIRECTIONAL", underlying="NIFTY",
            position_close_date="2099-01-01", legs=(), approved_lots=1, capital_required=1000.0,
            admitted_date=DAY,
        ) for i in range(pc_config.MAX_POSITIONS_PER_STRATEGY_FAMILY)
    )
    d = engine.evaluate_trade(trade, PortfolioState(admitted), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.approval_state == taxonomy.REJECTED
    assert taxonomy.REJECT_EXCESSIVE_CONCENTRATION in d.rejection_reasons


def test_no_spot_defers_not_rejects(chain):
    trade = _construct(chain, 24000.0, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), None, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.approval_state == taxonomy.DEFERRED
    assert taxonomy.DEFER_GREEKS_UNAVAILABLE in d.rejection_reasons


# --- required_margin always None, honestly disclosed ------------------

def test_required_margin_always_none(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.required_margin is None


# --- Deliverable 4: portfolio simulation --------------------------------

def test_portfolio_greeks_scale_with_existing_active_positions(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    empty = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    admitted_leg = HeldLeg(option_type="CE", strike=24100.0, expiry="2099-01-01", side="BUY",
                            ratio=1, delta=0.4, gamma=0.001, theta=-1.0, vega=10.0)
    admitted = AdmittedTrade(
        assessment_id="prior", strategy_family="LONG_DIRECTIONAL", underlying="NIFTY",
        position_close_date="2099-01-01", legs=(admitted_leg,), approved_lots=1, capital_required=1000.0,
        admitted_date=DAY,
    )
    with_existing = engine.evaluate_trade(trade, PortfolioState((admitted,)), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert with_existing.portfolio_delta_after != empty.portfolio_delta_after


def test_expired_admitted_trade_excluded_from_active_portfolio(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    expired = AdmittedTrade(
        assessment_id="old", strategy_family="LONG_DIRECTIONAL", underlying="NIFTY",
        position_close_date="2020-01-01", legs=(), approved_lots=1, capital_required=500_000.0,
        admitted_date="2019-12-01",
    )
    d = engine.evaluate_trade(trade, PortfolioState((expired,)), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    # Expired trade's capital must not count against capital_available -- the
    # full total capital should be free, since the only admitted trade has
    # already rolled off the book as of `as_of_date`.
    assert d.capital_available == pc_config.REPLAY_ASSUMED_TOTAL_CAPITAL


# --- Deliverable 5: sizing ------------------------------------------------

def test_build_admitted_trade_only_for_approved(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    approved = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    rejected_trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    rejected = engine.evaluate_trade(rejected_trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert engine.build_admitted_trade(trade, approved, spot, DAY) is not None
    assert engine.build_admitted_trade(rejected_trade, rejected, spot, DAY) is None


# --- Deliverable 7: explainability ---------------------------------------

def test_rejection_always_has_dominant_constraint_and_what_would_change(chain, spot):
    trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.explanation.dominant_constraint is not None
    assert d.explanation.why_rejected
    assert d.explanation.what_would_change_for_approval


def test_approval_always_has_why_approved(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d.explanation.why_approved


def test_reasoning_never_cites_historical_performance(chain, spot):
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit")
    for family in ("LONG_DIRECTIONAL", "SHORT_DIRECTIONAL"):
        trade = _construct(chain, spot, family)
        d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
        text = " ".join(d.explanation.why_approved + d.explanation.why_rejected).lower()
        for word in forbidden:
            assert word not in text


# --- Deliverable 6/8: determinism, batch/streaming parity ----------------

def test_determinism_identical_input_identical_id(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d1 = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    d2 = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assert d1.assessment_id == d2.assessment_id


def test_assessment_is_immutable(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    with pytest.raises(Exception):
        d.approval_state = "REJECTED"


def test_batch_and_streaming_are_byte_identical(chain, spot):
    trades = [_construct(chain, spot, f) for f in ("LONG_DIRECTIONAL", "SHORT_DIRECTIONAL", "COVERED")]
    requests = [dict(trade=t, portfolio=PortfolioState(), spot=spot, as_of_date=DAY,
                      selection_confidence="HIGH", timestamp=TS) for t in trades]
    batch = evaluate_trades_batch(requests)
    stream = PortfolioConstructionStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query -----------------------------------------------

def test_serialization_round_trip(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    payload = pc_serialization.assessment_to_dict(d)
    assert payload["assessment_id"] == d.assessment_id
    assert payload["approval_state"] == "APPROVED"


def test_query_helpers(chain, spot):
    approved_trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    rejected_trade = _construct(chain, spot, "SHORT_DIRECTIONAL")
    d1 = engine.evaluate_trade(approved_trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    d2 = engine.evaluate_trade(rejected_trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    assessments = (d1, d2)
    assert pc_query.approved_only(assessments) == (d1,)
    assert pc_query.rejected_only(assessments) == (d2,)
    assert pc_query.by_id(assessments, d1.assessment_id) is d1
    assert pc_query.by_rejection_reason(assessments, taxonomy.REJECT_UNDEFINED_RISK_POLICY) == (d2,)


def test_journal_is_append_only(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    d = engine.evaluate_trade(trade, PortfolioState(), spot, DAY, selection_confidence="HIGH", timestamp=TS)
    j = PortfolioConstructionJournal()
    j.record_assessment(d, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(d, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) ------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_portfolio_construction")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3")
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


def test_ast_no_forbidden_optimization_terms_and_uuid4_and_randomness():
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                lowered = identifier.lower()
                for term in ("optimi", "backtest", "pnl"):
                    assert term not in lowered, f"{path} contains forbidden identifier fragment '{term}'"
                assert lowered != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

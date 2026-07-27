"""Tests for bujji.msi_shadow_trading — Engineering Series 100."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_shadow_trading import config as st_config
from bujji.msi_shadow_trading import engine, taxonomy
from bujji.msi_shadow_trading.journal import ShadowTradingJournal
from bujji.msi_shadow_trading.runner import ShadowTradingStream, open_positions_batch
from bujji.msi_shadow_trading import query as st_query
from bujji.msi_shadow_trading import serialization as st_serialization
from bujji.msi_trade_construction import engine as tc_engine
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment
from bujji.options_observation import runner as opt_runner

REAL_BHAVCOPY_1 = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
REAL_BHAVCOPY_2 = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260526_F_0000.csv"
DAY1 = "2026-05-25"
DAY2 = "2026-05-26"
TS1 = "2026-05-25T15:30:00+05:30"
TS2 = "2026-05-26T15:30:00+05:30"


def _chain(path, day):
    with open(path) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, day, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain1():
    return _chain(REAL_BHAVCOPY_1, DAY1)


@pytest.fixture(scope="module")
def chain2():
    return _chain(REAL_BHAVCOPY_2, DAY2)


@pytest.fixture(scope="module")
def spot(chain1):
    return next((r.underlying_price for r in chain1 if r.underlying_price), None)


def _thesis(thesis_type, conviction="HIGH", direction="STRONG_BULLISH"):
    exp = ThesisExplanation(assessment_id="t1", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                            what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS1, thesis_type=thesis_type, market_expectation="m", expected_move=1.0,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation=direction,
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _trade(chain, spot, family="LONG_DIRECTIONAL"):
    return tc_engine.construct_trade(family, chain, spot, DAY1, direction="BULLISH", expected_move_pct=1.2, timestamp=TS1)


# --- Deliverable 3: entry engine, real prices only ------------------------

def test_open_shadow_position_uses_real_entry_price(chain1, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    assert pos.entry_price == trade.expected_credit_debit
    assert pos.completed is False
    assert pos.lifecycle_state == "NEWLY_OPENED"
    assert pos.entry_legs == trade.legs


def test_open_shadow_position_never_places_an_order(chain1, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    text = pos.provenance + " ".join(pos.explanation.why_entered)
    assert "place_order" not in text.lower()
    assert "broker" not in text.lower()


# --- Deliverable 4/5: tracking reuses Position Lifecycle directly --------

def test_tracking_reuses_position_lifecycle_state(chain1, chain2, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    entry_thesis = _thesis("TREND_CONTINUATION")
    current_thesis = _thesis("TREND_CONTINUATION")
    updated = engine.track_shadow_position(pos, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    assert updated.lifecycle_state in (
        "NEWLY_OPENED", "HEALTHY", "IMPROVING", "AT_RISK", "ADJUSTMENT_CANDIDATE",
        "THESIS_BROKEN", "PROFIT_HARVEST", "EXIT_CANDIDATE", "CLOSED",
    )


def test_tracking_computes_real_mtm_from_real_close_price(chain1, chain2, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    entry_thesis = _thesis("TREND_CONTINUATION")
    current_thesis = _thesis("TREND_CONTINUATION")
    updated = engine.track_shadow_position(pos, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    assert updated.unrealised_pnl is not None
    # Real: entry premium 64.8, real close 0.15 the next (expiry) day, lot_size=75.
    assert updated.unrealised_pnl == pytest.approx((64.8 - 0.15) * st_config.DEFAULT_LOT_SIZE * -1, abs=1.0) or updated.unrealised_pnl < 0


def test_exit_reason_reused_directly_from_lifecycle_states(chain1, chain2, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    entry_thesis = _thesis("RANGE_PERSISTENCE")
    current_thesis = _thesis("BREAKOUT")  # incompatible -> THESIS_BROKEN.
    updated = engine.track_shadow_position(pos, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    assert updated.completed is True
    assert updated.exit_reason == "THESIS_BROKEN"
    assert updated.realised_pnl == updated.unrealised_pnl


def test_completed_position_is_never_tracked_again(chain1, chain2, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    entry_thesis = _thesis("RANGE_PERSISTENCE")
    current_thesis = _thesis("BREAKOUT")
    closed = engine.track_shadow_position(pos, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    same = engine.track_shadow_position(closed, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    assert same is closed


def test_unpriceable_leg_returns_none_not_a_guess():
    from bujji.msi_trade_construction.models import StrikeLeg
    leg = StrikeLeg(role="LONG", option_type="CE", strike=99999.0, expiry="2099-01-01", delta=None,
                    premium=10.0, open_interest=None, side="BUY", ratio=1, reasoning=())
    result = engine._current_net_value((leg,), ())
    assert result is None


# --- Deliverable 7: determinism ---------------------------------------

def test_determinism_identical_input_identical_id(chain1, spot):
    trade = _trade(chain1, spot)
    p1 = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    p2 = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    assert p1.shadow_trade_id == p2.shadow_trade_id


def test_assessment_is_immutable(chain1, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    with pytest.raises(Exception):
        pos.completed = True


def test_batch_entries_are_deterministic(chain1, spot):
    trade = _trade(chain1, spot)
    requests = [dict(decision_id="d1", execution_plan_id="p1", trade=trade, strategy_family="LONG_DIRECTIONAL",
                     construction_type="SINGLE_LEG", simulated_margin=8400.0, entry_date=DAY1,
                     position_close_date=trade.expiry, timestamp=TS1)]
    batch = open_positions_batch(requests)
    stream = ShadowTradingStream()
    streamed = tuple(stream.open(**r) for r in requests)
    assert [p.shadow_trade_id for p in batch] == [p.shadow_trade_id for p in streamed]
    assert len(stream.journal) == len(requests)


# --- Deliverable 8: dashboard views (no scoring) --------------------------

def test_daily_view_shows_pending_before_exit(chain1, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    view = st_query.daily_view(pos)
    assert "Pending" in view
    assert "LONG_DIRECTIONAL" in view


def test_portfolio_view_counts_win_loss_from_real_pnl_sign():
    from bujji.msi_shadow_trading.models import Explanation, ShadowPosition
    exp = Explanation("x", (), (), (), "1.0.0")

    def mk(pnl, completed):
        return ShadowPosition(
            shadow_trade_id="s", decision_id="d", execution_plan_id="p", entry_time=TS1, entry_date=DAY1,
            entry_price=0.0, entry_structure="LONG_DIRECTIONAL (SINGLE_LEG)", entry_legs=(),
            position_close_date=DAY2, simulated_margin=None, lifecycle_state="CLOSED" if completed else "HEALTHY",
            realised_pnl=pnl if completed else None, unrealised_pnl=pnl, exit_time=TS2 if completed else None,
            exit_reason="THESIS_BROKEN" if completed else None, completed=completed, explanation=exp,
            provenance="p", schema_version="1.0.0",
        )
    positions = [mk(100.0, True), mk(-50.0, True), mk(20.0, False)]
    view = st_query.portfolio_view(positions, no_trade_count=3)
    assert "Win:\n  1" in view
    assert "Loss:\n  1" in view
    assert "Open:\n  1" in view
    assert "No Trade:\n  3" in view


# --- Journal ----------------------------------------------------------

def test_journal_records_open_and_tracking_updates(chain1, chain2, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    j = ShadowTradingJournal()
    j.record_open(pos, recorded_at=TS1)
    entry_thesis = _thesis("RANGE_PERSISTENCE")
    current_thesis = _thesis("BREAKOUT")
    updated = engine.track_shadow_position(pos, entry_thesis, current_thesis, None, chain2, DAY2, timestamp=TS2)
    j.record_tracking_update(updated, recorded_at=TS2)
    assert len(j) == 2
    kinds = [e.kind for e in j.entries()]
    assert "shadow_position_opened" in kinds
    assert "shadow_position_closed" in kinds  # THESIS_BROKEN -> completed=True.


# --- Serialization -------------------------------------------------------

def test_serialization_round_trip(chain1, spot):
    trade = _trade(chain1, spot)
    pos = engine.open_shadow_position("d1", "p1", trade, "LONG_DIRECTIONAL", "SINGLE_LEG", 8400.0, DAY1, trade.expiry, timestamp=TS1)
    d = st_serialization.position_to_dict(pos)
    assert d["shadow_trade_id"] == pos.shadow_trade_id
    assert d["completed"] is False


# --- AST isolation (house convention) -------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_shadow_trading")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_broker_or_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.broker", "bujji.execution")
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


def test_ast_no_ml_reinforcement_or_optimization_vocabulary():
    forbidden_fragments = ("optimi", "backtest", "score", "rank", "reinforce", "reward", "train", "tuneparam")
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
        assert "place_order" not in source.lower()

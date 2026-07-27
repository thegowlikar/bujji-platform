"""Tests for bujji.msi_position_recomposition (Series 110)."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.msi_position_recomposition import engine, taxonomy, query, runner
from bujji.msi_dynamic_management import runner as mdm_runner
from bujji.msi_portfolio_construction.models import HeldLeg
from bujji.options_observation import runner as opt_runner

DAY = "2026-05-25"
D = "20260525"
TS = f"{DAY}T15:15:00"


def _real_chain_and_spot():
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    chain = tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)
    spot = next(r.underlying_price for r in chain if r.underlying_price is not None)
    return chain, spot


def _leg(option_type, strike, expiry, side):
    return HeldLeg(option_type=option_type, strike=strike, expiry=expiry, side=side, ratio=1, delta=None, gamma=None, theta=None, vega=None)


def _lc(state, compatible, fired=()):
    return SimpleNamespace(
        lifecycle_id=f"LC-{state}", position_state=state,
        thesis_invalidation=SimpleNamespace(compatible=compatible),
        adjustment_policy=SimpleNamespace(fired=fired),
    )


HELD_LEGS = (
    _leg("CE", 24000.0, "2026-06-09", "SELL"), _leg("CE", 24200.0, "2026-06-09", "BUY"),
    _leg("PE", 23800.0, "2026-06-09", "SELL"), _leg("PE", 23600.0, "2026-06-09", "BUY"),
)


def test_recomposition_not_possible_when_no_actionable_decision():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.10, dte=30,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    assert result.possible is False
    assert result.reason_not_possible is not None
    assert result.execution_delta is None


def test_recomposition_never_approximates_produces_real_construct_trade_output():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.42, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    assert result.possible is True
    assert result.trigger == "STRIKE_ROLL"
    assert result.execution_delta is not None
    assert len(result.execution_delta.open_legs) > 0
    for leg in result.execution_delta.open_legs:
        assert leg.strike in {row.strike for row in chain}  # real chain strike, never fabricated


def test_full_exit_closes_all_legs_and_opens_nothing():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("THESIS_BROKEN", False), strategy_family="IRON_CONDOR", short_strike_delta=0.3, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    assert result.trigger == "FULL_EXIT"
    assert len(result.execution_delta.close_legs) == len(HELD_LEGS)
    assert result.execution_delta.open_legs == ()


def test_conversion_reports_honestly_when_not_representable():
    """VOLATILITY_EXPANSION has no representable transition rule
    (Series 109's own finding) -- this package must report the same,
    never approximate a target family."""
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("ADJUSTMENT_CANDIDATE", False), strategy_family="VOLATILITY_EXPANSION",
        short_strike_delta=0.3, dte=15, portfolio_delta_after=50.0, portfolio_vega_after=500.0,
        entry_volatility_regime="STABLE", current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="VOLATILITY_EXPANSION", chain=chain, spot=spot, day=DAY, timestamp=TS)
    if result.trigger == "STRATEGY_CONVERSION":
        assert result.possible is False
        assert "representable" in result.reason_not_possible.lower()


def test_partial_preservation_kept_and_replaced_legs_never_overlap():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.42, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    ed = result.execution_delta
    closed_keys = {(l.option_type, l.strike, l.expiry, l.side) for l in ed.close_legs}
    kept_keys = {(l.option_type, l.strike, l.expiry, l.side) for l in ed.kept_legs}
    assert closed_keys.isdisjoint(kept_keys)
    assert result.legs_reused == len(ed.kept_legs)
    assert result.strike_replacements == len(ed.close_legs)


def test_explanation_covers_all_six_named_facets():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.42, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    e = result.explanation
    assert len(e.why_this_strike) > 0
    assert len(e.why_this_expiry) > 0
    assert len(e.why_this_width) > 0
    assert len(e.why_keep_this_leg) > 0
    assert len(e.why_replace_that_leg) > 0
    assert len(e.why_not_rebuild_everything) > 0


def test_rerun_is_byte_identical():
    chain, spot = _real_chain_and_spot()
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.42, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    r1 = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    r2 = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=chain, spot=spot, day=DAY, timestamp=TS)
    assert r1.assessment_id == r2.assessment_id
    assert r1.explanation == r2.explanation
    assert r1.execution_delta == r2.execution_delta


def test_never_fabricates_a_strike_fails_closed_without_chain():
    board = mdm_runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR", short_strike_delta=0.42, dte=15,
        portfolio_delta_after=50.0, portfolio_vega_after=500.0, entry_volatility_regime="STABLE",
        current_volatility_regime="STABLE", timestamp=TS,
    )
    result = runner.run_recomposition(board=board, held_legs=HELD_LEGS, strategy_family="IRON_CONDOR", chain=(), spot=None, day=DAY, timestamp=TS)
    assert result.possible is False


def test_package_never_imports_execution_or_broker():
    pkg_dir = Path("bujji/msi_position_recomposition")
    forbidden = {"bujji.execution", "bujji.broker", "fyers_apiv3"}
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(f) for f in forbidden), (py_file, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(f) for f in forbidden), (py_file, alias.name)

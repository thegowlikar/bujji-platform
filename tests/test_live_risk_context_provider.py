import ast
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.msi_trade_construction.models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
from bujji.trading_brain.risk_governor.risk_budget_governor import RiskPolicy
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
from bujji.trading_brain.risk_governor.risk_governor_pipeline import GovernorPipelineContext
from bujji.trading_brain.risk_governor import live_risk_context_provider as lrcp

BASE_TS = datetime(2026, 8, 2, 9, 15, 0, tzinfo=timezone.utc)


def make_clock(ts=BASE_TS):
    return lambda: ts


def _leg(role, option_type, strike, side, ratio=1):
    return StrikeLeg(role=role, option_type=option_type, strike=strike, expiry="2026-08-06", delta=0.2,
                      premium=50.0, open_interest=10000.0, side=side, ratio=ratio, reasoning=("t",))


def make_proposal(constructed=True, strategy_family="IRON_CONDOR", expected_credit_debit=15000.0,
                   assessment_id="ASSESS-1"):
    legs = (_leg("SHORT_CE", "CE", 25000.0, "SELL"), _leg("SHORT_PE", "PE", 24000.0, "SELL"),
             _leg("LONG_CE", "CE", 25200.0, "BUY"), _leg("LONG_PE", "PE", 23800.0, "BUY")) if constructed else ()
    expiry_decision = ExpiryDecision(chosen_expiry="2026-08-06" if constructed else None, dte=4 if constructed else None,
                                      candidate_expiries=("2026-08-06",), rejected_expiries=(), reasoning=("t",))
    explanation = Explanation(assessment_id=assessment_id, why_this_expiry=("t",), why_these_strikes=("t",),
                               why_not_neighbouring_strikes=(), dominant_constraints=(), schema_version="1.0.0")
    return TradeConstructionAssessment(
        assessment_id=assessment_id, timestamp=BASE_TS.isoformat(), strategy_family=strategy_family,
        constructed=constructed, rejection_reason=None if constructed else "NO_VALID_EXPIRY",
        expiry="2026-08-06" if constructed else None, expiry_decision=expiry_decision, legs=legs,
        entry_reference_prices={}, expected_credit_debit=expected_credit_debit if constructed else None,
        risk_profile="DEFINED", required_margin=None, margin_unavailable_reason=None,
        supporting_assessment_ids=(), explanation=explanation, provenance="TEST", schema_version="1.0.0",
    )


def capital_snapshot(**overrides):
    defaults = dict(total_capital=1_000_000.0, available_capital=1_000_000.0, used_margin=200_000.0,
                     open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
                     peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS)
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _mint_and_construct(journal, plan_id, strategy_id, clock):
    mint = mint_position_group_id(journal, plan_id, strategy_id, "NIFTY", clock=clock)
    pg = mint.position_group_id
    coid = f"{pg}-L1"
    journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C0": coid}, "requested_quantities": {coid: 25},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=clock,
    )
    return pg, coid


def leg_kwargs_for(coid):
    return dict(
        contracts_by_client_order_id={coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
        sides_by_client_order_id={coid: "SELL"},
        reference_prices_by_client_order_id={coid: 50.0},
    )


def provider_kwargs(journal, coid=None, **overrides):
    defaults = dict(
        journal=journal,
        margin_provider=SimulatedMarginProvider(),
        capital_snapshot_provider=lambda: capital_snapshot(),
        memory=AdaptiveRiskMemory(),
        market_regime_provider=lambda: "SIDEWAYS",
        calibration_store=None,
        cache_ttl_seconds=None,
    )
    defaults.update(overrides)
    return defaults


def build_kwargs(coid, clock, **overrides):
    leg_kwargs = leg_kwargs_for(coid) if coid else dict(
        contracts_by_client_order_id={}, sides_by_client_order_id={}, reference_prices_by_client_order_id={},
    )
    defaults = dict(
        proposal=make_proposal(), desired_quantity=5, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        instrument_type="OPTIDX", product_type="MARGIN",
        risk_by_position_group_id=None,
        capital_safety_thresholds=None, portfolio_risk_thresholds=None, risk_policy=RiskPolicy(),
        position_health_thresholds=None, clock=clock,
    )
    defaults.update(leg_kwargs)
    defaults.update(overrides)
    return defaults


# --------------------------------------------------------------------- #
# Healthy path / missing producers
# --------------------------------------------------------------------- #

def test_healthy_path(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg, coid = _mint_and_construct(journal, "PLAN-1", "S1", clock)
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, coid))
    result = provider.build_context(**build_kwargs(coid, clock, risk_by_position_group_id={}))
    assert isinstance(result, GovernorPipelineContext)


def test_missing_margin_provider_raises_becomes_unavailable(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg, coid = _mint_and_construct(journal, "PLAN-1", "S1", clock)

    class BrokenMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            raise RuntimeError("margin service unreachable")

    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, coid, margin_provider=BrokenMarginProvider()))
    result = provider.build_context(**build_kwargs(coid, clock))
    assert isinstance(result, lrcp.ContextUnavailable)
    assert result.object_name == "margin_snapshot"


def test_missing_capital_provider_raises_becomes_unavailable(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")

    def broken_capital():
        raise RuntimeError("account API down")

    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None, capital_snapshot_provider=broken_capital))
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    assert isinstance(result, lrcp.ContextUnavailable)
    assert result.object_name == "capital_snapshot"


def test_missing_regime_provider_is_not_a_failure(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None, market_regime_provider=None))
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    assert isinstance(result, GovernorPipelineContext)
    assert result.market_regime is None


def test_regime_provider_raising_becomes_unavailable(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")

    def broken_regime():
        raise RuntimeError("regime feed down")

    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None, market_regime_provider=broken_regime))
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    assert isinstance(result, lrcp.ContextUnavailable)
    assert result.object_name == "market_regime"


def test_missing_journal_position_group_fails_via_builder(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None))
    # No positions minted, but caller claims a risk map entry for one --
    # E.2's own referential-integrity check fires, surfaced as ContextUnavailable.
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={"GHOST": 1000.0}))
    assert isinstance(result, lrcp.ContextUnavailable)
    assert result.producer == "GovernorContextBuilder"


def test_missing_memory_is_not_a_failure(tmp_path):
    clock = make_clock()
    journal = PositionGroupJournal(tmp_path / "pg.db")
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None, memory=AdaptiveRiskMemory()))
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    assert isinstance(result, GovernorPipelineContext)
    assert result.memory_entries == ()


# --------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------- #

def test_stale_cache_triggers_rebuild(tmp_path):
    call_count = {"n": 0}
    journal = PositionGroupJournal(tmp_path / "pg.db")
    real_margin_provider = SimulatedMarginProvider()

    class CountingMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            call_count["n"] += 1
            return real_margin_provider.get_portfolio_margin_with_explanation(legs, clock)

    provider = lrcp.LiveRiskContextProvider(
        **provider_kwargs(journal, None, margin_provider=CountingMarginProvider(), cache_ttl_seconds=10.0)
    )
    t0 = make_clock(BASE_TS)
    t_stale = make_clock(BASE_TS + timedelta(seconds=20))
    r1 = provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    r2 = provider.build_context(**build_kwargs(None, t_stale, risk_by_position_group_id={}))
    assert isinstance(r1, GovernorPipelineContext) and isinstance(r2, GovernorPipelineContext)
    assert call_count["n"] == 2  # rebuilt because the cache had gone stale


def test_fresh_cache_reused_without_rebuild(tmp_path):
    call_count = {"n": 0}
    journal = PositionGroupJournal(tmp_path / "pg.db")
    real_margin_provider = SimulatedMarginProvider()

    class CountingMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            call_count["n"] += 1
            return real_margin_provider.get_portfolio_margin_with_explanation(legs, clock)

    provider = lrcp.LiveRiskContextProvider(
        **provider_kwargs(journal, None, margin_provider=CountingMarginProvider(), cache_ttl_seconds=100.0)
    )
    t0 = make_clock(BASE_TS)
    t_fresh = make_clock(BASE_TS + timedelta(seconds=5))
    provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    provider.build_context(**build_kwargs(None, t_fresh, risk_by_position_group_id={}))
    assert call_count["n"] == 1  # second call reused the still-fresh cache


def test_invalidate_forces_rebuild(tmp_path):
    call_count = {"n": 0}
    journal = PositionGroupJournal(tmp_path / "pg.db")
    real_margin_provider = SimulatedMarginProvider()

    class CountingMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            call_count["n"] += 1
            return real_margin_provider.get_portfolio_margin_with_explanation(legs, clock)

    provider = lrcp.LiveRiskContextProvider(
        **provider_kwargs(journal, None, margin_provider=CountingMarginProvider(), cache_ttl_seconds=1000.0)
    )
    t0 = make_clock(BASE_TS)
    provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    provider.invalidate()
    provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    assert call_count["n"] == 2


def test_no_ttl_configured_never_caches(tmp_path):
    call_count = {"n": 0}
    journal = PositionGroupJournal(tmp_path / "pg.db")
    real_margin_provider = SimulatedMarginProvider()

    class CountingMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            call_count["n"] += 1
            return real_margin_provider.get_portfolio_margin_with_explanation(legs, clock)

    provider = lrcp.LiveRiskContextProvider(
        **provider_kwargs(journal, None, margin_provider=CountingMarginProvider(), cache_ttl_seconds=None)
    )
    t0 = make_clock(BASE_TS)
    provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    provider.build_context(**build_kwargs(None, t0, risk_by_position_group_id={}))
    assert call_count["n"] == 2


# --------------------------------------------------------------------- #
# Determinism / immutability
# --------------------------------------------------------------------- #

def test_determinism(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg, coid = _mint_and_construct(journal, "PLAN-1", "S1", make_clock())
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, coid))
    clock = make_clock()
    r1 = provider.build_context(**build_kwargs(coid, clock, risk_by_position_group_id={}))
    provider.invalidate()
    r2 = provider.build_context(**build_kwargs(coid, clock, risk_by_position_group_id={}))
    assert isinstance(r1, GovernorPipelineContext) and isinstance(r2, GovernorPipelineContext)
    assert r1.position_snapshot.position_group_id == r2.position_snapshot.position_group_id
    assert r1.strategy_type == r2.strategy_type


def test_cached_result_is_frozen_immutable(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None, cache_ttl_seconds=100.0))
    clock = make_clock()
    result = provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    with pytest.raises(Exception):
        result.desired_quantity = 999


def test_explain_context_shows_provenance(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    provider = lrcp.LiveRiskContextProvider(**provider_kwargs(journal, None))
    clock = make_clock()
    provider.build_context(**build_kwargs(None, clock, risk_by_position_group_id={}))
    text = provider.explain_context()
    assert "margin_snapshot" in text
    assert "capital_snapshot" in text
    assert "SimulatedMarginProvider" in text


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_duplicated_numeric_calculations():
    tree = ast.parse(open(lrcp.__file__).read())
    arithmetic_found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            operands_look_numeric = any(
                isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float))
                for operand in (node.left, node.right)
            )
            if operands_look_numeric:
                arithmetic_found.append(ast.dump(node.op))
    assert arithmetic_found == [], arithmetic_found


def test_no_threshold_constants():
    tree = ast.parse(open(lrcp.__file__).read())
    numeric_constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            numeric_constants.append(node.value.value)
    assert numeric_constants == [], numeric_constants


def test_no_broker_or_bujji_capital_imports():
    tree = ast.parse(open(lrcp.__file__).read())
    forbidden = ("broker", "fyers", "order", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "")
            assert not any(f in mod.lower() for f in forbidden)
            assert not mod.startswith("bujji.capital")


def test_no_import_of_d_star_decision_functions_or_margin_formulas():
    tree = ast.parse(open(lrcp.__file__).read())
    forbidden_names = (
        "evaluate_trade_capital_safety", "classify_capital_safety", "aggregate_portfolio_risk",
        "classify_portfolio_risk", "assess_trade_risk_budget", "calculate_safe_position_size",
        "recommend_risk_action", "recommend_adaptive_risk_adjustment", "run_risk_governor_pipeline",
        "_compute_book", "build_span_margin_request", "compute_capital_metrics",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names


def test_no_force_override_bypass_parameter():
    sig = inspect.signature(lrcp.LiveRiskContextProvider.build_context)
    for name in sig.parameters:
        assert "force" not in name.lower()
        assert "override" not in name.lower()
        assert "bypass" not in name.lower()

import ast
import inspect
from datetime import datetime, timezone

import pytest

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.msi_trade_construction.models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
from bujji.trading_brain.risk_governor.risk_budget_governor import RiskPolicy
from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot
from bujji.trading_brain.risk_governor.adaptive_risk_memory import (
    AdaptiveRiskMemory, OUTCOME_WIN, build_risk_memory_entry,
)
from bujji.trading_brain.risk_governor.margin_calibration_runner import (
    MarginCalibrationSample, MarginCalibrationStore,
)
from bujji.trading_brain.risk_governor.risk_governor_pipeline import GovernorPipelineContext
from bujji.trading_brain.risk_governor import governor_context_builder as gcb

BASE_TS = datetime(2026, 8, 2, 9, 15, 0, tzinfo=timezone.utc)
clock = lambda: BASE_TS


def _leg(role, option_type, strike, side, ratio=1):
    return StrikeLeg(role=role, option_type=option_type, strike=strike, expiry="2026-08-06", delta=0.2,
                      premium=50.0, open_interest=10000.0, side=side, ratio=ratio, reasoning=("t",))


def make_proposal(constructed=True, strategy_family="IRON_CONDOR", expected_credit_debit=15000.0,
                   required_margin=None, rejection_reason=None, assessment_id="ASSESS-1"):
    legs = (_leg("SHORT_CE", "CE", 25000.0, "SELL"), _leg("SHORT_PE", "PE", 24000.0, "SELL"),
             _leg("LONG_CE", "CE", 25200.0, "BUY"), _leg("LONG_PE", "PE", 23800.0, "BUY")) if constructed else ()
    expiry_decision = ExpiryDecision(chosen_expiry="2026-08-06" if constructed else None, dte=4 if constructed else None,
                                      candidate_expiries=("2026-08-06",), rejected_expiries=(), reasoning=("t",))
    explanation = Explanation(assessment_id=assessment_id, why_this_expiry=("t",), why_these_strikes=("t",),
                               why_not_neighbouring_strikes=(), dominant_constraints=(), schema_version="1.0.0")
    return TradeConstructionAssessment(
        assessment_id=assessment_id, timestamp=BASE_TS.isoformat(), strategy_family=strategy_family,
        constructed=constructed, rejection_reason=rejection_reason, expiry="2026-08-06" if constructed else None,
        expiry_decision=expiry_decision, legs=legs, entry_reference_prices={},
        expected_credit_debit=expected_credit_debit if constructed else None, risk_profile="DEFINED",
        required_margin=required_margin, margin_unavailable_reason=None, supporting_assessment_ids=(),
        explanation=explanation, provenance="TEST", schema_version="1.0.0",
    )


def capital_snapshot(**overrides):
    defaults = dict(total_capital=1_000_000.0, available_capital=1_000_000.0, used_margin=200_000.0,
                     open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
                     peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS)
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _mint_and_construct(journal, plan_id, strategy_id, n_legs=1, underlying="NIFTY"):
    mint = mint_position_group_id(journal, plan_id, strategy_id, underlying, clock=clock)
    pg = mint.position_group_id
    coid = f"{pg}-L1"
    contract_map = {f"C{i}": f"{pg}-L{i}" for i in range(n_legs)}
    requested = {f"{pg}-L{i}": 25 for i in range(n_legs)}
    journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": contract_map, "requested_quantities": requested,
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=clock,
    )
    return pg


def margin_kwargs(pg_ids, per_group_risk=5_000.0):
    risk_by_position_group_id = {pg: per_group_risk for pg in pg_ids}
    margin_snapshot = MarginSnapshot(required_margin=40_000.0, margin_verified=True, margin_source="SIM", as_of=BASE_TS, quote=None)
    margin_explanation = MarginExplanation(
        total_required_margin=40_000.0, total_long_exposure=10_000.0, total_short_exposure=30_000.0,
        covered_exposure=0.0, naked_exposure=30_000.0, contributing_legs=(),
        highest_margin_contributor=None, risk_flags=(), risk_classification="LOW_RISK", explanation_source="SIM",
    )
    return dict(margin_snapshot=margin_snapshot, margin_explanation=margin_explanation,
                risk_by_position_group_id=risk_by_position_group_id)


def build_kwargs(journal, **overrides):
    defaults = dict(
        proposal=make_proposal(),
        desired_quantity=5,
        requested_risk=5000.0,
        capital_snapshot=capital_snapshot(),
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        capital_safety_thresholds=None,
        margin_snapshot=None,
        margin_explanation=None,
        risk_by_position_group_id=None,
        portfolio_risk_thresholds=None,
        risk_policy=RiskPolicy(),
        position_health_thresholds=None,
        market_regime="SIDEWAYS",
        memory=AdaptiveRiskMemory(),
        clock=clock,
    )
    defaults.update(overrides)
    return defaults


# --------------------------------------------------------------------- #
# Healthy account
# --------------------------------------------------------------------- #

def test_healthy_account_builds_context(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg1 = _mint_and_construct(journal, "PLAN-1", "S1")
    pg2 = _mint_and_construct(journal, "PLAN-2", "S2")
    pg3 = _mint_and_construct(journal, "PLAN-3", "S3")
    pg4 = _mint_and_construct(journal, "PLAN-4", "S4")
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, **margin_kwargs([pg1, pg2, pg3, pg4])))
    assert isinstance(result, GovernorPipelineContext)
    assert len(result.position_groups) == 4


def test_healthy_account_no_positions_no_margin_needed(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal))
    assert isinstance(result, GovernorPipelineContext)
    assert result.position_groups == []


# --------------------------------------------------------------------- #
# Missing / invalid data
# --------------------------------------------------------------------- #

def test_missing_margin_snapshot_with_active_positions_fails(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    _mint_and_construct(journal, "PLAN-1", "S1")
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal))
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "MarginSnapshot"


def test_missing_portfolio_no_active_positions_no_margin_ok(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, risk_by_position_group_id={}))
    assert isinstance(result, GovernorPipelineContext)


def test_orphaned_risk_map_reference_fails(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg1 = _mint_and_construct(journal, "PLAN-1", "S1")
    builder = gcb.GovernorContextBuilder(journal)
    kwargs = build_kwargs(journal, **margin_kwargs([pg1]))
    kwargs["risk_by_position_group_id"] = {**kwargs["risk_by_position_group_id"], "PG-NONEXISTENT": 1000.0}
    result = builder.build(**kwargs)
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "risk_by_position_group_id"
    assert result.field_name == "PG-NONEXISTENT"


def test_missing_strategy_proposal_not_constructed_fails(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    builder = gcb.GovernorContextBuilder(journal)
    kwargs = build_kwargs(journal, proposal=make_proposal(constructed=False, rejection_reason="NO_VALID_EXPIRY"))
    result = builder.build(**kwargs)
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "TradeConstructionAssessment"


def test_missing_memory_is_not_a_failure(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, memory=AdaptiveRiskMemory()))
    assert isinstance(result, GovernorPipelineContext)
    assert result.memory_entries == ()


def test_memory_with_unknown_strategy_type_fails(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    memory = AdaptiveRiskMemory()
    memory.append_observation(build_risk_memory_entry(
        "e1", "NOT_A_REAL_FAMILY", "SIDEWAYS", "LOW_VOL", "SAFE", "RISK_HEALTHY", 5, 5,
        OUTCOME_WIN, 0.01, 0.03, 2, "PROFIT_TARGET", clock=clock,
    ))
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, memory=memory))
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "AdaptiveRiskMemory"


def test_duplicate_position_group_ids_fails(tmp_path, monkeypatch):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg1 = _mint_and_construct(journal, "PLAN-1", "S1")
    # read_all_group_ids() is a SELECT DISTINCT, so duplicates cannot
    # occur through the journal's own real path -- this directly
    # exercises the Builder's own defensive guard against a corrupted
    # or adversarial read path returning a duplicate id.
    monkeypatch.setattr(journal, "read_all_group_ids", lambda: [pg1, pg1])
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, **margin_kwargs([pg1])))
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "PositionGroupJournal"


def test_invalid_calibration_reference_fails(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    store = MarginCalibrationStore()
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, calibration_store=store, calibration_sample_id="MISSING-SAMPLE"))
    assert isinstance(result, gcb.ContextBuildFailure)
    assert result.object_name == "MarginCalibrationStore"


def test_valid_calibration_reference_passes(tmp_path):
    from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
    from bujji.trading_brain.risk_governor.margin_comparison_engine import compare_margin
    from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot

    journal = PositionGroupJournal(tmp_path / "pg.db")
    store = MarginCalibrationStore()
    sim_snapshot = SimulatedMarginProvider().get_portfolio_margin([], clock=clock)
    broker_snapshot = BrokerMarginSnapshot(available_margin=1000000.0, used_margin=0.0, required_margin=0.0,
                                            timestamp=BASE_TS, source="FYERS_READ_ONLY", available=True)
    report = compare_margin(sim_snapshot, broker_snapshot, clock=clock)
    sample = MarginCalibrationSample(
        sample_id="SAMPLE-1", timestamp=BASE_TS, strategy_type="IRON_CONDOR", position_description="test",
        margin_leg_input=(), simulated_margin_snapshot=sim_snapshot, broker_margin_snapshot=broker_snapshot,
        comparison_report=report,
    )
    store.record_sample(sample)
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, calibration_store=store, calibration_sample_id="SAMPLE-1"))
    assert isinstance(result, GovernorPipelineContext)


# --------------------------------------------------------------------- #
# Large portfolio / determinism / immutability
# --------------------------------------------------------------------- #

def test_large_portfolio_1000_positions(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg_ids = [_mint_and_construct(journal, f"PLAN-{i}", f"S{i % 13}") for i in range(1000)]
    builder = gcb.GovernorContextBuilder(journal)
    result = builder.build(**build_kwargs(journal, **margin_kwargs(pg_ids, per_group_risk=1.0)))
    assert isinstance(result, GovernorPipelineContext)
    assert len(result.position_groups) == 1000


def test_determinism(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    pg1 = _mint_and_construct(journal, "PLAN-1", "S1")
    builder = gcb.GovernorContextBuilder(journal)
    kwargs = build_kwargs(journal, **margin_kwargs([pg1]))
    r1 = builder.build(**kwargs)
    r2 = builder.build(**kwargs)
    assert isinstance(r1, GovernorPipelineContext) and isinstance(r2, GovernorPipelineContext)
    assert [s.position_group_id for s in r1.position_groups] == [s.position_group_id for s in r2.position_groups]


def test_proposal_immutability(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    proposal = make_proposal()
    before = proposal
    builder = gcb.GovernorContextBuilder(journal)
    builder.build(**build_kwargs(journal, proposal=proposal))
    assert proposal is before


# --------------------------------------------------------------------- #
# No duplicated calculations / forbidden imports
# --------------------------------------------------------------------- #

def test_no_duplicated_numeric_calculations():
    tree = ast.parse(open(gcb.__file__).read())
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
    tree = ast.parse(open(gcb.__file__).read())
    numeric_constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            numeric_constants.append(node.value.value)
    assert numeric_constants == [], numeric_constants


def test_no_broker_execution_or_order_imports():
    tree = ast.parse(open(gcb.__file__).read())
    forbidden = ("broker", "fyers", "order", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert not any(f in mod for f in forbidden)


def test_no_import_of_defined_risk_formulas_or_capital_check():
    # No duplicated logic from D.* decision functions or Gate C's
    # margin computation formulas -- the Builder only imports data
    # models/constants and thin, already-existing helper functions.
    tree = ast.parse(open(gcb.__file__).read())
    forbidden_names = (
        "assess_defined_risk", "assess_capital", "assess_portfolio_limits",
        "evaluate_trade_capital_safety", "classify_capital_safety",
        "aggregate_portfolio_risk", "classify_portfolio_risk",
        "assess_trade_risk_budget", "calculate_safe_position_size",
        "recommend_risk_action", "recommend_adaptive_risk_adjustment",
        "run_risk_governor_pipeline",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names, alias.name


def test_no_force_override_bypass_parameter():
    sig = inspect.signature(gcb.GovernorContextBuilder.build)
    for name in sig.parameters:
        assert "force" not in name.lower()
        assert "override" not in name.lower()
        assert "bypass" not in name.lower()

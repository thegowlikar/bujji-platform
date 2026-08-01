import ast
import inspect
from datetime import datetime

import pytest

from bujji.msi_trade_construction.models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
from bujji.trading_brain.risk_governor.risk_budget_governor import RiskPolicy
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot
from bujji.trading_brain.risk_governor.adaptive_risk_memory import OUTCOME_LOSS, OUTCOME_WIN, build_risk_memory_entry
from bujji.trading_brain.risk_governor import strategy_risk_pipeline as srp
from bujji.trading_brain.risk_governor import strategy_risk_adapter as sra

BASE_TS = datetime(2026, 8, 2, 9, 15, 0)
clock = lambda: BASE_TS


def _leg(role="SHORT_CE", option_type="CE", strike=25000.0, side="SELL", ratio=1):
    return StrikeLeg(
        role=role, option_type=option_type, strike=strike, expiry="2026-08-06", delta=0.2, premium=50.0,
        open_interest=10000.0, side=side, ratio=ratio, reasoning=("test leg",),
    )


def make_proposal(constructed=True, legs=None, strategy_family="IRON_CONDOR", expected_credit_debit=15000.0,
                   required_margin=None, rejection_reason=None, assessment_id="ASSESS-1"):
    if legs is None:
        legs = (_leg("SHORT_CE", "CE", 25000.0, "SELL"), _leg("SHORT_PE", "PE", 24000.0, "SELL"),
                 _leg("LONG_CE", "CE", 25200.0, "BUY"), _leg("LONG_PE", "PE", 23800.0, "BUY")) if constructed else ()
    expiry_decision = ExpiryDecision(
        chosen_expiry="2026-08-06" if constructed else None, dte=4 if constructed else None,
        candidate_expiries=("2026-08-06",), rejected_expiries=(), reasoning=("nearest weekly",),
    )
    explanation = Explanation(
        assessment_id=assessment_id, why_this_expiry=("nearest weekly",), why_these_strikes=("delta-targeted",),
        why_not_neighbouring_strikes=(), dominant_constraints=(), schema_version="1.0.0",
    )
    return TradeConstructionAssessment(
        assessment_id=assessment_id, timestamp=BASE_TS.isoformat(), strategy_family=strategy_family,
        constructed=constructed, rejection_reason=rejection_reason,
        expiry="2026-08-06" if constructed else None, expiry_decision=expiry_decision, legs=legs,
        entry_reference_prices={}, expected_credit_debit=expected_credit_debit if constructed else None,
        risk_profile="DEFINED", required_margin=required_margin, margin_unavailable_reason=None,
        supporting_assessment_ids=(), explanation=explanation, provenance="TEST", schema_version="1.0.0",
    )


def capital_snapshot(**overrides):
    defaults = dict(
        total_capital=1_000_000.0, available_capital=1_000_000.0, used_margin=200_000.0,
        open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS,
    )
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _group(pg_id, strategy_id, qty=25):
    coid = f"{pg_id}-L1"
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state="OPEN", strategy_id=strategy_id,
        legs={coid: LegState(client_order_id=coid, contract_id="C", requested_quantity=None,
                              submit_status="LEG_ACKED",
                              fill=LegFillState(client_order_id=coid, cumulative_filled_quantity=qty))},
    )


def diversified_portfolio_kwargs():
    groups = [_group(f"PG-{i}", f"S{i}") for i in range(1, 5)]
    risk_by_position_group_id = {f"PG-{i}": 5_000.0 for i in range(1, 5)}
    margin_snapshot = MarginSnapshot(required_margin=40_000.0, margin_verified=True, margin_source="SIM", as_of=BASE_TS, quote=None)
    margin_explanation = MarginExplanation(
        total_required_margin=40_000.0, total_long_exposure=10_000.0, total_short_exposure=30_000.0,
        covered_exposure=0.0, naked_exposure=30_000.0, contributing_legs=(),
        highest_margin_contributor=None, risk_flags=(), risk_classification="LOW_RISK", explanation_source="SIM",
    )
    return dict(position_groups=groups, margin_snapshot=margin_snapshot, margin_explanation=margin_explanation,
                risk_by_position_group_id=risk_by_position_group_id)


def run_kwargs(**overrides):
    defaults = dict(
        proposal=make_proposal(),
        desired_quantity=5,
        requested_risk=5000.0,
        capital_snapshot=capital_snapshot(),
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        capital_safety_thresholds=None,
        portfolio_risk_thresholds=None,
        risk_policy=RiskPolicy(),
        position_health_thresholds=None,
        market_regime="SIDEWAYS",
        memory_entries=(),
        clock=clock,
    )
    defaults.update(diversified_portfolio_kwargs())
    defaults.update(overrides)
    return defaults


def good_history(n=20):
    return tuple(build_risk_memory_entry(f"e{i}", "IRON_CONDOR", "SIDEWAYS", "LOW_VOL", "SAFE", "RISK_HEALTHY",
                                          5, 5, OUTCOME_WIN, 0.01, 0.03, 2, "PROFIT_TARGET", clock=clock) for i in range(n))


def bad_history(n=20):
    return tuple(build_risk_memory_entry(f"b{i}", "IRON_CONDOR", "SIDEWAYS", "LOW_VOL", "SAFE", "RISK_HEALTHY",
                                          5, 5, OUTCOME_LOSS, 0.15, 0.00, 5, "MAX_LOSS", clock=clock) for i in range(n))


# --------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------- #

def test_adapter_maps_strategy_type_from_strategy_family():
    proposal = make_proposal(strategy_family="IRON_FLY")
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.strategy_type == "IRON_FLY"
    assert ctx.position_snapshot.strategy_type == "IRON_FLY"


def test_adapter_maps_position_group_id_from_assessment_id():
    proposal = make_proposal(assessment_id="A-99")
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.position_snapshot.position_group_id == "A-99"


def test_adapter_entry_and_current_value_equal_expected_credit_debit():
    proposal = make_proposal(expected_credit_debit=12345.0)
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.position_snapshot.entry_value == 12345.0
    assert ctx.position_snapshot.current_value == 12345.0
    assert ctx.position_snapshot.unrealized_pnl == 0.0


def test_adapter_missing_credit_debit_leaves_entry_value_none():
    proposal = make_proposal()
    proposal = TradeConstructionAssessment(**{**proposal.__dict__, "expected_credit_debit": None})
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.position_snapshot.entry_value is None


def test_adapter_margin_consumed_passes_through_none_honestly():
    proposal = make_proposal(required_margin=None)
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.position_snapshot.margin_consumed is None


def test_adapter_unknown_strategy_family_still_maps_verbatim_no_translation_table():
    proposal = make_proposal(strategy_family="SOME_FUTURE_FAMILY")
    ctx = sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                            ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                            None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert ctx.strategy_type == "SOME_FUTURE_FAMILY"


def test_adapter_raises_on_not_constructed():
    proposal = make_proposal(constructed=False, rejection_reason="NO_VALID_EXPIRY")
    with pytest.raises(sra.InvalidStrategyProposalError):
        sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                          ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                          None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)


def test_adapter_raises_on_empty_legs_even_if_constructed_true():
    proposal = make_proposal()
    proposal = TradeConstructionAssessment(**{**proposal.__dict__, "legs": ()})
    with pytest.raises(sra.InvalidStrategyProposalError):
        sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                          ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                          None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)


def test_adapter_does_not_mutate_original_proposal():
    proposal = make_proposal()
    before = proposal
    sra.adapt_strategy_proposal_to_governor_context(proposal, 5, 5000.0, capital_snapshot(),
                                                      ProposedTradeEffect(0.0, 0.0), None, [], None, None,
                                                      None, None, RiskPolicy(), None, "SIDEWAYS", (), clock)
    assert proposal is before
    assert proposal.strategy_family == "IRON_CONDOR"


# --------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------- #

def test_healthy_approval():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs())
    assert decision.blocking_stage is None
    assert decision.approved_quantity > 0
    assert decision.final_quantity == decision.approved_quantity
    assert decision.governor_result is not None
    assert decision.proposal.strategy_family == "IRON_CONDOR"


def test_capital_block():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(capital_snapshot=capital_snapshot(daily_pnl=-60000.0)))
    assert decision.blocking_stage == "CAPITAL"
    assert decision.approved_quantity == 0
    assert decision.final_quantity == 0


def test_portfolio_block():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(position_groups=[], margin_snapshot=None,
                                                            margin_explanation=None, risk_by_position_group_id=None))
    assert decision.blocking_stage == "PORTFOLIO"
    assert decision.approved_quantity == 0


def test_budget_reduction_blocked_when_requested_risk_exceeds_budget():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(requested_risk=999999.0))
    assert decision.blocking_stage == "BUDGET"
    assert decision.approved_quantity == 0


def test_adaptive_reduction():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(memory_entries=bad_history()))
    assert decision.blocking_stage is None
    assert decision.adaptive_adjustment in ("REDUCE_SIZE_10", "REDUCE_SIZE_20", "REDUCE_SIZE_30")
    assert decision.approved_quantity < decision.original_quantity


def test_adaptive_increase():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(memory_entries=good_history(), desired_quantity=1, requested_risk=1000.0))
    assert decision.blocking_stage is None
    assert decision.adaptive_adjustment in ("INCREASE_SIZE_10", "INCREASE_SIZE_20")
    assert decision.approved_quantity <= decision.governor_result.budget_decision.sizing.maximum_quantity


def test_invalid_proposal_stops_before_governor():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(proposal=make_proposal(constructed=False, rejection_reason="NO_VALID_EXPIRY")))
    assert decision.blocking_stage == srp.STAGE_PROPOSAL
    assert decision.governor_result is None
    assert decision.approved_quantity == 0
    assert decision.final_quantity == 0


def test_original_quantity_preserved_even_when_reduced():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(memory_entries=bad_history(), desired_quantity=10))
    assert decision.original_quantity == 10
    assert decision.approved_quantity != decision.original_quantity


# --------------------------------------------------------------------- #
# Explainability
# --------------------------------------------------------------------- #

def test_trace_correctness_includes_proposal_step_then_governor_steps():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs())
    stages = [s.stage for s in decision.decision_trace.steps]
    assert stages[0] == srp.STAGE_PROPOSAL
    assert stages[1:] == ["CAPITAL", "PORTFOLIO", "BUDGET", "ADMISSION", "ADAPTIVE"]


def test_reason_propagation_blocked_quotes_governor_detail():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(capital_snapshot=capital_snapshot(daily_pnl=-60000.0)))
    assert "BLOCKED at CAPITAL" in decision.explanation


def test_explain_integrated_decision_includes_strategy_identity_and_no_new_reasoning():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs())
    text = srp.explain_integrated_decision(decision)
    assert decision.proposal.strategy_family in text
    assert decision.proposal.assessment_id in text
    assert decision.explanation in text  # no duplicated text -- the underlying explanation is quoted, not rewritten


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_broker_execution_or_order_imports():
    for module in (srp, sra):
        tree = ast.parse(open(module.__file__).read())
        forbidden = ("broker", "fyers", "order", "execution")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name.lower() for f in forbidden), (module.__name__, alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert not any(f in mod for f in forbidden), (module.__name__, node.module)


def test_governor_executes_exactly_once():
    import bujji.trading_brain.risk_governor.strategy_risk_pipeline as pipeline_module
    call_count = {"n": 0}
    orig = pipeline_module.run_risk_governor_pipeline

    def wrapper(*a, **kw):
        call_count["n"] += 1
        return orig(*a, **kw)

    pipeline_module.run_risk_governor_pipeline = wrapper
    try:
        srp.run_strategy_risk_pipeline(**run_kwargs())
    finally:
        pipeline_module.run_risk_governor_pipeline = orig
    assert call_count["n"] == 1


def test_proposal_never_changes_object_identity():
    kwargs = run_kwargs()
    proposal_before = kwargs["proposal"]
    decision = srp.run_strategy_risk_pipeline(**kwargs)
    assert decision.proposal is proposal_before


def test_deterministic_execution():
    kwargs = run_kwargs(memory_entries=bad_history())
    d1 = srp.run_strategy_risk_pipeline(**kwargs)
    d2 = srp.run_strategy_risk_pipeline(**kwargs)
    assert d1.blocking_stage == d2.blocking_stage
    assert d1.approved_quantity == d2.approved_quantity


def test_no_force_override_bypass_parameter():
    for fn in (srp.run_strategy_risk_pipeline, sra.adapt_strategy_proposal_to_governor_context):
        sig = inspect.signature(fn)
        for name in sig.parameters:
            assert "force" not in name.lower()
            assert "override" not in name.lower()
            assert "bypass" not in name.lower()


def test_no_duplicated_threshold_constants_in_pipeline_or_adapter():
    for module in (srp, sra):
        tree = ast.parse(open(module.__file__).read())
        numeric_constants = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
                numeric_constants.append(node.value.value)
        assert numeric_constants == [], (module.__name__, numeric_constants)


def test_approved_quantity_never_exceeds_governor_final_quantity():
    decision = srp.run_strategy_risk_pipeline(**run_kwargs(memory_entries=good_history(), desired_quantity=1, requested_risk=1000.0))
    if decision.governor_result is not None:
        assert decision.approved_quantity <= decision.governor_result.final_quantity or decision.approved_quantity == decision.governor_result.final_quantity

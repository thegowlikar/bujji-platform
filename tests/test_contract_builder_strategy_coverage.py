"""Tests — Engineering Series 63: Contract Builder strategy coverage."""
import os
from datetime import datetime

import pytest

from bujji.trading_brain.capital_brain.models import CapitalDecision
from bujji.trading_brain.nifty_contract_builder import engine, models, taxonomy
from bujji.trading_brain.strategy_selector.models import StrategyDecision
from bujji.trading_brain.strategy_selector import registry as strategy_registry

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)
MIC_V2_AVAILABLE = os.path.exists("/opt/bujji-mic-v2/.venv/bin/python")


def _strategy(**overrides):
    base = dict(
        decision_id="SD-0000000000000001",
        selected_strategy="PREMIUM_VWAP_STRADDLE",
        selection_status="SELECTED",
        selection_confidence="VERY_HIGH",
        selection_reason="stub selection reason",
        supporting_conditions=(),
        rejecting_conditions=(),
        alternative_candidates=(),
        all_evaluations=(),
        decision_trace="stub decision trace",
        market_state_assessment_id="MSA-0000000000000001",
        timestamp="2026-01-01T09:00:00",
        version="1.0.0",
    )
    base.update(overrides)
    return StrategyDecision(**base)


def _capital(**overrides):
    base = dict(
        decision_id="CD-0000000000000001",
        capital_intent="STANDARD",
        allocation_status="APPROVED",
        allocation_reason="stub allocation reason",
        allocation_constraints=("NONE",),
        required_controls=("NONE",),
        confidence="VERY_HIGH",
        decision_trace="stub decision trace",
        risk_assessment_id="RA-0000000000000001",
        timestamp="2026-01-01T09:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return CapitalDecision(**base)


def _spot(value=25148.0):
    return models.NiftySpotSnapshot(spot=value, as_of="2026-01-01T09:20:00")


def _chain():
    strikes_types = [(25150, "CE"), (25150, "PE"), (25200, "CE"), (25250, "CE"), (25100, "PE"), (25050, "PE")]
    entries = [
        models.NiftyOptionChainEntry(strike=strike, option_type=opt, expiry="2026-07-31", contract_symbol=f"NSE:X{strike}{opt}")
        for strike, opt in strikes_types
    ]
    return models.NiftyOptionChainSnapshot(expiries=("2026-07-31",), entries=tuple(entries), as_of="2026-01-01T09:20:00")


# ---------------------------------------------------------------------------
# Complete strategy coverage audit
# ---------------------------------------------------------------------------


def test_every_registered_strategy_is_classified_supported_or_unsupported():
    registered_ids = {s.strategy_id for s in strategy_registry.ALL_STRATEGIES}
    accounted_for = set(taxonomy.SUPPORTED_STRATEGIES) | set(taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES)
    assert registered_ids == accounted_for


def test_coverage_matrix_has_eleven_strategies():
    assert len(taxonomy.SUPPORTED_STRATEGIES) + len(taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES) == 11


def test_covered_call_is_registered_but_unsupported():
    assert "COVERED_CALL" in {s.strategy_id for s in strategy_registry.ALL_STRATEGIES}
    assert "COVERED_CALL" in taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES
    assert "COVERED_CALL" not in taxonomy.SUPPORTED_STRATEGIES


def test_all_supported_strategies_have_templates():
    for strategy_id in taxonomy.SUPPORTED_STRATEGIES:
        assert strategy_id in engine.TEMPLATES


def test_all_unsupported_strategies_have_no_template():
    for strategy_id in taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES:
        assert strategy_id not in engine.TEMPLATES


# ---------------------------------------------------------------------------
# COVERED_CALL and sibling strategies: explicit, documented rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_id", list(taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES))
def test_unsupported_registered_strategies_get_explicit_out_of_scope_reason(strategy_id):
    result = engine.build_contracts(
        _strategy(selected_strategy=strategy_id), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert result.status == "FAILED"
    assert result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"
    assert result.contracts == ()


def test_covered_call_no_longer_produces_unknown_strategy():
    result = engine.build_contracts(
        _strategy(selected_strategy="COVERED_CALL"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert result.failure_reason != "UNKNOWN_STRATEGY"
    assert result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"


def test_genuinely_unregistered_strategy_still_produces_unknown_strategy():
    result = engine.build_contracts(
        _strategy(selected_strategy="NOT_A_REAL_STRATEGY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert result.status == "FAILED"
    assert result.failure_reason == "UNKNOWN_STRATEGY"
    assert result.contracts == ()


def test_out_of_scope_reason_has_a_documented_description():
    description = taxonomy.FAILURE_REASON_DESCRIPTIONS[taxonomy.FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE]
    assert "deliberate" in description.lower()
    assert "v1" in description.lower()


# ---------------------------------------------------------------------------
# Deterministic leg generation (supported strategies unaffected)
# ---------------------------------------------------------------------------


def test_deterministic_leg_generation_for_supported_strategy_unchanged():
    a = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    b = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    assert a.contracts == b.contracts
    assert a.status == b.status == "CONSTRUCTED"


def test_deterministic_rejection_for_unsupported_strategy():
    a = engine.build_contracts(_strategy(selected_strategy="COVERED_CALL"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    b = engine.build_contracts(_strategy(selected_strategy="COVERED_CALL"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    assert a.failure_reason == b.failure_reason
    assert a.status == b.status == "FAILED"


# ---------------------------------------------------------------------------
# Invalid underlying / missing expiry / missing contract handling
# ---------------------------------------------------------------------------


def test_invalid_underlying_spot_handling_unaffected_by_this_sprint():
    result = engine.build_contracts(_strategy(), _capital(), _spot(0.0), _chain(), clock=FIXED_CLOCK)
    assert result.status == "FAILED"
    assert result.failure_reason == "INVALID_SPOT"


def test_missing_expiry_handling_unaffected_by_this_sprint():
    empty_chain = models.NiftyOptionChainSnapshot(expiries=(), entries=(), as_of="x")
    result = engine.build_contracts(_strategy(), _capital(), _spot(), empty_chain, clock=FIXED_CLOCK)
    assert result.status == "FAILED"


def test_missing_strategy_decision_handling_unaffected_by_this_sprint():
    result = engine.build_contracts(None, _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    assert result.status == "FAILED"
    assert result.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Replay / runtime / historical qualification compatibility
# ---------------------------------------------------------------------------


def test_replay_compatibility_no_new_module_imported_by_replay_package():
    import bujji.qualification.replay_runner as replay_runner_module
    import inspect

    source = inspect.getsource(replay_runner_module)
    assert "covered_call" not in source.lower()


def test_runtime_compatibility_shadow_mode_never_fabricates_completed_outcome():
    from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
    from bujji.production_runtime.startup import startup
    from bujji.production_runtime.runtime import run_shadow, PipelineInput

    root, sreport = startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)
    assert sreport.ready

    # A COVERED_CALL selection can't be forced through Strategy Selector
    # directly (frozen, unmodified) -- this test instead confirms the
    # Contract Builder's own new classification surfaces correctly when
    # called with a StrategyDecision naming COVERED_CALL, exactly as it
    # would if Strategy Selector ever chose it.
    result = engine.build_contracts(
        _strategy(selected_strategy="COVERED_CALL"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert result.status == "FAILED"
    assert result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_historical_qualification_2026_07_09_regression():
    """Regression requirement: re-run the exact Historical Qualification
    Campaign v2 scenario for 2026-07-09. The previous UNKNOWN_STRATEGY
    failure must be replaced by an explicit, documented rejection --
    never a silent fallback, never a fabricated completion.
    """
    from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
    from bujji.production_runtime.startup import startup
    from bujji.production_runtime.runtime import run_shadow, PipelineInput

    # Real classification observed for 2026-07-09 in Campaign v2 (4 real
    # trading days of accumulated MIC v2 history).
    pi = PipelineInput(
        market_context="TRENDING_DOWN",
        market_opinion="BEARISH",
        context_stability="MOSTLY_STABLE",
        calibration="INSUFFICIENT_HISTORY",
        governance="REJECTED",
        lifecycle="UNKNOWN",
        contract="UNKNOWN",
    )
    root, sreport = startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)
    sd = run_shadow(root, pi, spot_snapshot=_spot(), option_chain=_chain(), clock=FIXED_CLOCK)

    assert sd.strategy_decision.selected_strategy == "COVERED_CALL"
    assert sd.contract_construction_result.failure_reason != "UNKNOWN_STRATEGY"
    assert sd.contract_construction_result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"
    assert sd.execution_session.execution_state == "FAILED_VALIDATION"


# ---------------------------------------------------------------------------
# No broker interaction, no runtime mutation
# ---------------------------------------------------------------------------


def test_no_broker_interaction():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(engine))
    body_without_docstrings = [
        n for n in ast.walk(tree)
        if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))
    ]
    imports = [n for n in body_without_docstrings if isinstance(n, (ast.Import, ast.ImportFrom))]
    calls = [n for n in body_without_docstrings if isinstance(n, ast.Call)]

    for node in imports:
        module_name = getattr(node, "module", None) or ",".join(a.name for a in node.names)
        assert "broker" not in (module_name or "").lower()

    for node in calls:
        func_repr = ast.dump(node.func)
        assert "broker" not in func_repr.lower()
        assert "dispatch" not in func_repr.lower()
        assert "authenticate" not in func_repr.lower()


def test_no_runtime_mutation_strategy_decision_untouched():
    strategy = _strategy(selected_strategy="COVERED_CALL")
    before = repr(strategy)
    engine.build_contracts(strategy, _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
    assert repr(strategy) == before

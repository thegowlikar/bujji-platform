"""Tests — Engineering Series 54: three runtime modes."""
from datetime import datetime

import pytest

from bujji.production_runtime.composition_root import build_composition_root
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_PRODUCTION_READY, RUNTIME_MODE_READ_ONLY, RUNTIME_MODE_SHADOW
from bujji.production_runtime.runtime import (
    PipelineInput,
    ReadOnlyResult,
    ShadowResult,
    run_read_only,
    run_shadow,
    verify_production_ready_construction,
)

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)

VALID_INPUT = PipelineInput(
    market_context="TRENDING_UP",
    market_opinion="BULLISH",
    context_stability="STABLE",
    calibration="CALIBRATED",
    governance="APPROVED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)


@pytest.fixture
def root():
    return build_composition_root(RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="paper"))


def test_read_only_runs_full_decision_pipeline_and_stops(root):
    result = run_read_only(root, VALID_INPUT, clock=FIXED_CLOCK)
    assert isinstance(result, ReadOnlyResult)
    assert result.mode == RUNTIME_MODE_READ_ONLY
    assert "Stopped before Order Construction" in result.trace


def test_read_only_never_touches_execution_session(root):
    result = run_read_only(root, VALID_INPUT, clock=FIXED_CLOCK)
    assert not hasattr(result, "execution_session")


def test_read_only_is_deterministic(root):
    a = run_read_only(root, VALID_INPUT, clock=FIXED_CLOCK)
    b = run_read_only(root, VALID_INPUT, clock=FIXED_CLOCK)
    assert a.strategy_decision.selected_strategy == b.strategy_decision.selected_strategy
    assert a.capital_decision.capital_intent == b.capital_decision.capital_intent


def test_shadow_runs_full_pipeline_with_no_market_data_fails_gracefully(root):
    result = run_shadow(root, VALID_INPUT, spot_snapshot=None, option_chain=None, clock=FIXED_CLOCK)
    assert isinstance(result, ShadowResult)
    assert result.mode == RUNTIME_MODE_SHADOW
    assert result.order_submitted is False


def test_shadow_never_submits_a_live_broker_order(root):
    result = run_shadow(root, VALID_INPUT, spot_snapshot=None, option_chain=None, clock=FIXED_CLOCK)
    from bujji.broker.paper import PaperBroker

    assert isinstance(root.broker, PaperBroker)
    assert "never a live broker order" in result.trace


def test_shadow_dispatch_target_is_paper_broker_not_fyers(root):
    assert type(root.broker).__name__ == "PaperBroker"


def test_shadow_is_deterministic_given_same_inputs(root):
    a = run_shadow(root, VALID_INPUT, spot_snapshot=None, option_chain=None, clock=FIXED_CLOCK)
    b = run_shadow(root, VALID_INPUT, spot_snapshot=None, option_chain=None, clock=FIXED_CLOCK)
    assert a.execution_session.execution_state == b.execution_session.execution_state
    assert a.runtime_authorization.decision == b.runtime_authorization.decision


def test_production_ready_mode_constructs_real_fyers_broker_without_connecting():
    prod_root = build_composition_root(
        RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="fyers")
    )
    assert verify_production_ready_construction(prod_root) is True
    from bujji.broker.fyers import FyersBroker

    assert isinstance(prod_root.broker, FyersBroker)


def test_production_ready_verification_false_when_broker_missing():
    prod_root = build_composition_root(RuntimeConfig(broker_name="paper"))
    object.__setattr__(prod_root, "broker", None)
    assert verify_production_ready_construction(prod_root) is False


def test_qualification_fingerprint_flows_from_config_not_recomputed(root):
    result = run_shadow(root, VALID_INPUT, spot_snapshot=None, option_chain=None, clock=FIXED_CLOCK)
    assert root.trading_config.qualification_fingerprint == root.config.qualification_fingerprint

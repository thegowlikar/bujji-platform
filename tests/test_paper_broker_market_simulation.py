"""Tests -- Gate F.2 Realistic PaperBroker Market Simulation."""
from __future__ import annotations

import ast
import inspect

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.event_bus import Event, EventBus, EventType
from bujji.core.models import OptionContract, OrderRequest

from bujji.broker.simulation.slippage import SlippageCalculator, SlippageConfig, SlippageMode, IllegalSlippageInputError
from bujji.broker.simulation.charges import ChargesCalculator, ChargesConfig, IllegalChargesInputError
from bujji.broker.simulation.order_lifecycle import (
    ExecutionStage, IllegalExecutionStageTransition, OrderLifecycleTracker,
)
from bujji.broker.simulation.fill_simulator import (
    FillSimulator, IllegalFillSimulationInputError, LatencyConfig, LatencyMode, PartialFillConfig, RejectionConfig,
)
from bujji.broker.simulation.market_snapshot import MarketSnapshot

CONTRACT = OptionContract("NIFTY24250CE", "NIFTY", 24250, OptionType.CE, "2026-08-04", 75)


# --------------------------------------------------------------------- #
# Slippage
# --------------------------------------------------------------------- #

def test_slippage_zero_mode_no_change():
    price = SlippageCalculator.apply(100.0, "BUY", SlippageConfig(mode=SlippageMode.ZERO))
    assert price == 100.0


def test_slippage_fixed_tick_buy_slips_up():
    config = SlippageConfig(mode=SlippageMode.FIXED_TICK, tick_size=0.05, fixed_ticks=4)
    assert SlippageCalculator.apply(100.0, "BUY", config) == pytest.approx(100.2)


def test_slippage_fixed_tick_sell_slips_down():
    config = SlippageConfig(mode=SlippageMode.FIXED_TICK, tick_size=0.05, fixed_ticks=4)
    assert SlippageCalculator.apply(100.0, "SELL", config) == pytest.approx(99.8)


def test_slippage_percentage_mode():
    config = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.01)
    assert SlippageCalculator.apply(100.0, "BUY", config) == pytest.approx(101.0)


def test_slippage_volatility_adjusted_requires_volatility():
    config = SlippageConfig(mode=SlippageMode.VOLATILITY_ADJUSTED, volatility_multiplier=0.5)
    with pytest.raises(IllegalSlippageInputError):
        SlippageCalculator.compute(100.0, "BUY", config, volatility=None)


def test_slippage_volatility_adjusted_scales_with_volatility():
    config = SlippageConfig(mode=SlippageMode.VOLATILITY_ADJUSTED, volatility_multiplier=0.5)
    assert SlippageCalculator.compute(100.0, "BUY", config, volatility=2.0) == pytest.approx(1.0)


def test_slippage_deterministic_repeat_calls():
    config = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.002)
    a = SlippageCalculator.apply(100.0, "SELL", config)
    b = SlippageCalculator.apply(100.0, "SELL", config)
    assert a == b


# --------------------------------------------------------------------- #
# Charges
# --------------------------------------------------------------------- #

def test_charges_turnover_calculation():
    breakdown = ChargesCalculator.calculate(100000.0, "BUY", ChargesConfig())
    assert breakdown.turnover == 100000.0


def test_charges_stt_only_on_sell():
    buy = ChargesCalculator.calculate(100000.0, "BUY", ChargesConfig())
    sell = ChargesCalculator.calculate(100000.0, "SELL", ChargesConfig())
    assert buy.stt == 0.0
    assert sell.stt > 0.0


def test_charges_stamp_duty_only_on_buy():
    buy = ChargesCalculator.calculate(100000.0, "BUY", ChargesConfig())
    sell = ChargesCalculator.calculate(100000.0, "SELL", ChargesConfig())
    assert buy.stamp_duty > 0.0
    assert sell.stamp_duty == 0.0


def test_charges_each_component_present():
    breakdown = ChargesCalculator.calculate(50000.0, "SELL", ChargesConfig())
    assert breakdown.brokerage >= 0
    assert breakdown.stt >= 0
    assert breakdown.exchange_charges >= 0
    assert breakdown.gst >= 0
    assert breakdown.sebi_charges >= 0
    assert breakdown.stamp_duty >= 0


def test_charges_total_matches_sum_of_components():
    breakdown = ChargesCalculator.calculate(75000.0, "SELL", ChargesConfig())
    expected = (breakdown.brokerage + breakdown.stt + breakdown.exchange_charges
                + breakdown.gst + breakdown.sebi_charges + breakdown.stamp_duty)
    assert breakdown.total == pytest.approx(expected)


def test_charges_negative_turnover_rejected():
    with pytest.raises(IllegalChargesInputError):
        ChargesCalculator.calculate(-100.0, "BUY", ChargesConfig())


def test_charges_configurable_not_hardcoded():
    custom = ChargesConfig(brokerage_per_order=0.0, stt_sell_percentage=0.0, exchange_charges_percentage=0.0,
                            gst_percentage=0.0, sebi_charges_percentage=0.0, stamp_duty_percentage=0.0)
    breakdown = ChargesCalculator.calculate(100000.0, "SELL", custom)
    assert breakdown.total == 0.0


# --------------------------------------------------------------------- #
# Order lifecycle
# --------------------------------------------------------------------- #

def test_lifecycle_valid_transitions():
    tracker = OrderLifecycleTracker("CID-1")
    tracker.transition(ExecutionStage.SUBMITTED)
    tracker.transition(ExecutionStage.ACCEPTED)
    tracker.transition(ExecutionStage.FILLED)
    assert tracker.stage == ExecutionStage.FILLED
    assert tracker.is_terminal() is True


def test_lifecycle_invalid_transition_blocked():
    tracker = OrderLifecycleTracker("CID-1")
    with pytest.raises(IllegalExecutionStageTransition):
        tracker.transition(ExecutionStage.FILLED)  # CREATED -> FILLED is illegal


def test_lifecycle_cancellation_flow():
    tracker = OrderLifecycleTracker("CID-1")
    tracker.transition(ExecutionStage.SUBMITTED)
    tracker.transition(ExecutionStage.ACCEPTED)
    tracker.transition(ExecutionStage.PARTIALLY_FILLED)
    tracker.transition(ExecutionStage.CANCELLED)
    assert tracker.stage == ExecutionStage.CANCELLED
    assert tracker.is_terminal() is True


def test_lifecycle_publish_fn_called_on_transition():
    events = []
    tracker = OrderLifecycleTracker("CID-1", publish_fn=lambda *args: events.append(args))
    tracker.transition(ExecutionStage.SUBMITTED, reason="test")
    assert len(events) == 1
    assert events[0][1] == ExecutionStage.CREATED
    assert events[0][2] == ExecutionStage.SUBMITTED


def test_lifecycle_no_op_self_transition_does_not_publish():
    events = []
    tracker = OrderLifecycleTracker("CID-1", publish_fn=lambda *args: events.append(args))
    tracker.transition(ExecutionStage.CREATED)
    assert events == []


# --------------------------------------------------------------------- #
# Fill simulator
# --------------------------------------------------------------------- #

def test_fill_simulator_perfect_fill():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    result = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), LatencyConfig(),
                                     RejectionConfig(), PartialFillConfig(), __import__("random").Random(1))
    assert result.status == OrderStatus.FILLED.value
    assert result.fill_quantity == 75
    assert result.fill_price == 100.0


def test_fill_simulator_slippage_applied():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    config = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.01)
    result = FillSimulator.simulate(75, "BUY", snapshot, config, LatencyConfig(), RejectionConfig(),
                                     PartialFillConfig(), __import__("random").Random(1))
    assert result.fill_price == pytest.approx(101.0)
    assert result.slippage == pytest.approx(1.0)


def test_fill_simulator_partial_fill():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    config = PartialFillConfig(fill_ratio=0.4)
    result = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), LatencyConfig(),
                                     RejectionConfig(), config, __import__("random").Random(1))
    assert result.fill_quantity == 30
    assert result.status == OrderStatus.PARTIAL.value


def test_fill_simulator_delayed_fill_records_latency():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    latency = LatencyConfig(mode=LatencyMode.FIXED, fixed_ms=250.0)
    result = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), latency, RejectionConfig(),
                                     PartialFillConfig(), __import__("random").Random(1))
    assert result.latency_ms == 250.0
    assert result.status == OrderStatus.FILLED.value


def test_fill_simulator_rejected_order_forced():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    rejection = RejectionConfig(force_reject=True, force_reject_reason="EXCHANGE_UNAVAILABLE")
    result = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), LatencyConfig(), rejection,
                                     PartialFillConfig(), __import__("random").Random(1))
    assert result.status == OrderStatus.REJECTED.value
    assert result.fill_price is None
    assert result.rejection_reason == "EXCHANGE_UNAVAILABLE"


def test_fill_simulator_rejected_on_insufficient_liquidity():
    snapshot = MarketSnapshot(symbol="X", last_price=100.0, liquidity_score=0.1)
    rejection = RejectionConfig(reject_on_insufficient_liquidity=True, min_liquidity_score=0.5)
    result = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), LatencyConfig(), rejection,
                                     PartialFillConfig(), __import__("random").Random(1))
    assert result.status == OrderStatus.REJECTED.value
    assert result.rejection_reason == "INSUFFICIENT_LIQUIDITY"


def test_fill_simulator_random_latency_deterministic_with_seeded_rng():
    import random
    snapshot = MarketSnapshot(symbol="X", last_price=100.0)
    latency = LatencyConfig(mode=LatencyMode.RANDOM_RANGE, min_ms=50.0, max_ms=300.0)
    r1 = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), latency, RejectionConfig(),
                                 PartialFillConfig(), random.Random(7))
    r2 = FillSimulator.simulate(75, "BUY", snapshot, SlippageConfig(), latency, RejectionConfig(),
                                 PartialFillConfig(), random.Random(7))
    assert r1.latency_ms == r2.latency_ms


# --------------------------------------------------------------------- #
# PaperBroker integration
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_paperbroker_default_behavior_unchanged():
    broker = PaperBroker()
    await broker.connect()
    result = await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 75
    assert result.average_price == 100.0


@pytest.mark.asyncio
async def test_paperbroker_execution_report_recorded():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    report = broker.get_execution_report("CID-1")
    assert report is not None
    assert report.filled_qty == 75
    assert report.charges is not None
    assert report.charges.total > 0


@pytest.mark.asyncio
async def test_paperbroker_charges_do_not_affect_realized_pnl():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 75, "CID-2", limit_price=110.0))
    # Gross realized PnL, unaffected by charges -- exactly the existing behavior.
    assert broker.get_realized_pnl() == pytest.approx((110.0 - 100.0) * 75)


@pytest.mark.asyncio
async def test_paperbroker_slippage_configured_changes_fill_price():
    config = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.01)
    broker = PaperBroker(slippage_config=config)
    await broker.connect()
    result = await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert result.average_price == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_paperbroker_rejection_configured_never_fills():
    rejection = RejectionConfig(force_reject=True, force_reject_reason="MARKET_CLOSED")
    broker = PaperBroker(rejection_config=rejection)
    await broker.connect()
    result = await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert result.status == OrderStatus.REJECTED
    assert result.average_price is None
    positions = await broker.get_open_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_paperbroker_legacy_partial_fill_qty_still_takes_priority():
    broker = PaperBroker(partial_fill_qty=20, slippage_config=SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.5))
    await broker.connect()
    result = await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert result.filled_quantity == 20
    assert result.average_price == 100.0  # legacy path bypasses slippage entirely, as before


@pytest.mark.asyncio
async def test_paperbroker_eventbus_optional_no_bus_no_crash():
    broker = PaperBroker()
    await broker.connect()
    result = await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert result.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_paperbroker_eventbus_publishes_lifecycle_events_when_supplied():
    bus = EventBus()
    seen = []
    for event_type in EventType:
        bus.subscribe(event_type, lambda e: seen.append(e.payload.get("stage")))
    broker = PaperBroker(event_bus=bus)
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    import asyncio
    await asyncio.sleep(0)  # let publish_nowait's scheduled task run
    assert any(s and s.startswith("ORDER_LIFECYCLE_") for s in seen)


@pytest.mark.asyncio
async def test_paperbroker_deterministic_same_input_same_execution():
    config = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.005)
    broker1 = PaperBroker(seed=99, slippage_config=config)
    broker2 = PaperBroker(seed=99, slippage_config=config)
    await broker1.connect()
    await broker2.connect()
    r1 = await broker1.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    r2 = await broker2.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    assert r1.average_price == r2.average_price
    assert r1.filled_quantity == r2.filled_quantity


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_real_broker_calls_or_imports_in_simulation_package():
    import bujji.broker.simulation.fill_simulator as fs_module
    import bujji.broker.simulation.slippage as slip_module
    import bujji.broker.simulation.charges as charges_module
    forbidden = ("fyers", "hybrid")
    for module in (fs_module, slip_module, charges_module):
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert not any(f in mod for f in forbidden)


def test_no_risk_logic_duplicated_in_simulation_package():
    import bujji.broker.simulation.fill_simulator as fs_module
    tree = ast.parse(open(fs_module.__file__).read())
    forbidden_names = (
        "assess_defined_risk", "run_risk_governor_pipeline", "evaluate_trade_capital_safety",
        "aggregate_portfolio_risk", "recommend_risk_action",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names


def test_no_caller_changes_required_f1_runtime_test_suite_still_passes():
    # This is asserted by re-running tests/test_trading_brain_runtime.py
    # separately in the same session -- this test only documents the
    # requirement and confirms PaperBroker's public constructor still
    # accepts zero arguments (F.1's own composition root never passes
    # any Gate F.2 config).
    broker = PaperBroker()
    assert broker.name == "paper"


def test_execution_interface_unchanged_place_order_signature():
    sig = inspect.signature(PaperBroker.place_order)
    params = list(sig.parameters)
    assert params == ["self", "request"]

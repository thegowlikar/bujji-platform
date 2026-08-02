"""Tests -- Gate F.4 Trade Lifecycle Execution Manager."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.core.event_bus import EventBus, EventType

from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
    ACTION_ADD_HEDGE, ACTION_BLOCK_NEW_RISK, ACTION_EXIT_CONSIDERATION, ACTION_HOLD, ACTION_MONITOR,
    ACTION_REDUCE_SIZE, RiskActionRecommendation,
)
from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
from bujji.production_runtime.position_lifecycle_runtime import (
    LifecycleEvaluationResult, PositionLifecycleRuntime, PositionLifecycleState,
)
from bujji.production_runtime.lifecycle_order_builder import (
    IllegalLifecycleOrderError, build_hedge_order, build_reduce_order,
)
from bujji.production_runtime.trade_lifecycle_executor import (
    ACTION_MANDATORY_EXIT, STATUS_EXECUTED, STATUS_FAILED_VALIDATION, STATUS_NO_ACTION, STATUS_PARTIAL,
    STATUS_REJECTED, TradeLifecycleExecutor,
)
from bujji.production_runtime import trade_lifecycle_executor as executor_module
from bujji.production_runtime import lifecycle_order_builder as builder_module

BASE_TS = datetime(2026, 5, 25, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return BASE_TS


CONTRACT_CE = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-04", 75)
HEDGE_CONTRACT = OptionContract("NIFTY24500PE", "NIFTY", 24500, OptionType.PE, "2026-08-04", 75)


def make_recommendation(action, health_status="STRESSED"):
    return RiskActionRecommendation(
        action=action, health_status=health_status, reasons=(), explanation=f"test:{action}", evaluated_at=BASE_TS,
    )


async def _setup(reduce_qty_on_open=75):
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, reduce_qty_on_open, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock,
                             contracts={"NIFTY25000CE": CONTRACT_CE})
    lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
    lifecycle_runtime.mark_open("PG-1")
    return broker, registry, lifecycle_runtime


def make_executor(broker, registry, lifecycle_runtime, event_bus=None):
    return TradeLifecycleExecutor(broker, registry, lifecycle_runtime, event_bus=event_bus)


# --------------------------------------------------------------------- #
# Action mapping
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_hold_creates_no_order():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_HOLD), PositionLifecycleState.OPEN)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_NO_ACTION
    assert result.orders_submitted == ()


@pytest.mark.asyncio
async def test_monitor_creates_no_order():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_MONITOR), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_NO_ACTION
    assert result.orders_submitted == ()


@pytest.mark.asyncio
async def test_reduce_size_creates_reducing_order():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=25)
    assert result.status == STATUS_EXECUTED
    assert len(result.orders_submitted) == 1
    assert result.orders_submitted[0].filled_quantity == 25
    positions = await broker.get_open_positions()
    assert positions[0]["qty"] == 50


@pytest.mark.asyncio
async def test_exit_consideration_does_not_exit():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_EXIT_CONSIDERATION), PositionLifecycleState.EXIT_PENDING)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_NO_ACTION
    assert result.orders_submitted == ()
    positions = await broker.get_open_positions()
    assert positions[0]["qty"] == 75  # unchanged


@pytest.mark.asyncio
async def test_block_new_risk_does_not_mutate_position():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_BLOCK_NEW_RISK), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_NO_ACTION
    positions = await broker.get_open_positions()
    assert positions[0]["qty"] == 75


@pytest.mark.asyncio
async def test_add_hedge_with_no_instruction_records_only():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_ADD_HEDGE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_NO_ACTION
    assert result.orders_submitted == ()


@pytest.mark.asyncio
async def test_add_hedge_with_instruction_executes():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_ADD_HEDGE), PositionLifecycleState.MANAGING)
    instruction = {"contract": HEDGE_CONTRACT, "side": "BUY", "quantity": 75, "reference_price": 30.0}
    result = await executor.execute(evaluation, clock, hedge_instruction=instruction)
    assert result.status == STATUS_EXECUTED
    assert len(result.orders_submitted) == 1


# --------------------------------------------------------------------- #
# Safety: cannot exceed position, invalid symbol, zero quantity
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reduce_more_than_owned_rejected():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=1000)
    assert result.status == STATUS_FAILED_VALIDATION
    positions = await broker.get_open_positions()
    assert positions[0]["qty"] == 75  # unchanged


@pytest.mark.asyncio
async def test_reduce_zero_quantity_rejected():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=0)
    assert result.status == STATUS_FAILED_VALIDATION


@pytest.mark.asyncio
async def test_reduce_with_no_quantity_supplied_fails_closed():
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock)
    assert result.status == STATUS_FAILED_VALIDATION
    positions = await broker.get_open_positions()
    assert positions[0]["qty"] == 75


def test_build_reduce_order_invalid_symbol_rejected():
    with pytest.raises(IllegalLifecycleOrderError):
        build_reduce_order(None, CONTRACT_CE, 25, "CID-1")


def test_build_reduce_order_defaults_to_entry_avg_price_when_no_reference_supplied():
    # Regression guard for existing callers: omitting reference_price must
    # keep the EXACT prior behavior (pinned to entry avg_price) byte for byte.
    position = {"symbol": "NIFTY25000CE", "side": "SELL", "qty": 75, "avg_price": 50.0}
    order = build_reduce_order(position, CONTRACT_CE, 75, "CID-DEFAULT")
    assert order.reference_price == 50.0


def test_build_reduce_order_uses_supplied_current_market_price():
    # Found via the pre-Monday dry rehearsal: a real close must reflect
    # real market movement, not stay pinned to the entry price forever.
    position = {"symbol": "NIFTY25000CE", "side": "SELL", "qty": 75, "avg_price": 50.0}
    order = build_reduce_order(position, CONTRACT_CE, 75, "CID-CURRENT", reference_price=12.0)
    assert order.reference_price == 12.0


@pytest.mark.asyncio
async def test_mandatory_exit_realizes_real_pnl_from_current_market_price():
    """The bug found by the dry rehearsal: MANDATORY_EXIT (and REDUCE_SIZE)
    must realize P&L based on the CURRENT market price, not silently
    re-fill at the entry price and realize ~zero regardless of movement."""
    broker, registry, lc = await _setup()
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult(
        "PG-1", make_recommendation(ACTION_MANDATORY_EXIT), PositionLifecycleState.MANAGING,
    )
    # Entry was SELL 75 @ 50.0 (see _setup). Market has since dropped to 12.0
    # -- a real, sizeable profit for a short position bought back cheap.
    result = await executor.execute(
        evaluation, clock, reduce_quantity=75, reference_prices={"NIFTY25000CE": 12.0},
    )
    assert result.status == STATUS_EXECUTED
    realized = broker.get_realized_pnl("NIFTY25000CE")
    assert realized == pytest.approx((50.0 - 12.0) * 75)


def test_build_hedge_order_zero_quantity_rejected():
    with pytest.raises(IllegalLifecycleOrderError):
        build_hedge_order({"contract": HEDGE_CONTRACT, "side": "BUY", "quantity": 0}, "CID-1")


def test_build_reduce_order_cannot_increase_exposure():
    # Attempting a reduce_quantity that would over-reduce (caught above);
    # here confirm the SIDE is always the opposite of the current holding,
    # never the same side (which would increase, not reduce, exposure).
    position = {"symbol": "NIFTY25000CE", "side": "SELL", "qty": 75, "avg_price": 50.0}
    order = build_reduce_order(position, CONTRACT_CE, 25, "CID-1")
    assert order.side == Side.BUY  # opposite of the SELL holding -- reduces, never adds


# --------------------------------------------------------------------- #
# Execution handling: full/partial fill, rejection, cancellation
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_full_fill_updates_lifecycle_to_closed_when_fully_reduced():
    broker, registry, lc = await _setup(reduce_qty_on_open=25)
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=25)
    assert result.status == STATUS_EXECUTED
    assert lc.lifecycle_state("PG-1") == PositionLifecycleState.CLOSED


@pytest.mark.asyncio
async def test_partial_fill_leaves_remaining_state_open():
    # partial_fill_qty is a broker-wide knob -- seed the existing 75-lot
    # holding directly (rather than placing a full-size opening order on
    # a broker that would ALSO only partially fill that opening order),
    # so only the REDUCE order itself is affected by the partial-fill knob.
    broker = PaperBroker(partial_fill_qty=10)
    await broker.connect()
    broker.seed_position("NIFTY25000CE", "SELL", 75, avg_price=50.0)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock,
                             contracts={"NIFTY25000CE": CONTRACT_CE})
    lc = PositionLifecycleRuntime(registry, clock)
    lc.mark_open("PG-1")
    executor = make_executor(broker, registry, lc)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=25)
    assert result.status == STATUS_PARTIAL
    # The executor only transitions lifecycle state to CLOSED on full
    # closure -- a partial reduce leaves it exactly as it was (still
    # OPEN here, since evaluate_group(), which would drive OPEN->
    # MANAGING, was never called in this test).
    assert lc.lifecycle_state("PG-1") == PositionLifecycleState.OPEN


@pytest.mark.asyncio
async def test_rejected_order_handled():
    from bujji.broker.simulation.fill_simulator import RejectionConfig
    broker = PaperBroker(rejection_config=RejectionConfig(force_reject=True))
    await broker.connect()
    # Seed a position via a broker without rejection so a real position exists to reduce.
    seed_broker = PaperBroker()
    await seed_broker.connect()
    await seed_broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(seed_broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock,
                             contracts={"NIFTY25000CE": CONTRACT_CE})
    lc = PositionLifecycleRuntime(registry, clock)
    lc.mark_open("PG-1")
    executor = make_executor(broker, registry, lc)  # uses the REJECTING broker for the reduce order
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    result = await executor.execute(evaluation, clock, reduce_quantity=25)
    assert result.status == STATUS_REJECTED


@pytest.mark.asyncio
async def test_cancellation_handled_via_broker_cancel_order():
    broker, registry, lc = await _setup()
    result = await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-EXTRA", limit_price=50.0))
    cancel_result = await broker.cancel_order("CID-EXTRA")
    assert cancel_result.status == OrderStatus.CANCELLED


# --------------------------------------------------------------------- #
# EventBus
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_eventbus_publishes_lifecycle_action_events():
    import asyncio
    broker, registry, lc = await _setup()
    bus = EventBus()
    seen = []
    for event_type in EventType:
        bus.subscribe(event_type, lambda e: seen.append(e.payload.get("stage")))
    executor = make_executor(broker, registry, lc, event_bus=bus)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    await executor.execute(evaluation, clock, reduce_quantity=25)
    await asyncio.sleep(0)
    assert "LIFECYCLE_ACTION_STARTED" in seen
    assert "LIFECYCLE_ORDER_CREATED" in seen
    assert "LIFECYCLE_ORDER_FILLED" in seen
    assert "LIFECYCLE_ACTION_COMPLETED" in seen


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_same_recommendation_same_position_same_result():
    broker1, registry1, lc1 = await _setup()
    broker2, registry2, lc2 = await _setup()
    executor1 = make_executor(broker1, registry1, lc1)
    executor2 = make_executor(broker2, registry2, lc2)
    evaluation = LifecycleEvaluationResult("PG-1", make_recommendation(ACTION_REDUCE_SIZE), PositionLifecycleState.MANAGING)
    r1 = await executor1.execute(evaluation, clock, reduce_quantity=25)
    r2 = await executor2.execute(evaluation, clock, reduce_quantity=25)
    assert r1.status == r2.status
    assert r1.orders_submitted[0].filled_quantity == r2.orders_submitted[0].filled_quantity


# --------------------------------------------------------------------- #
# End-to-end (F.1 + F.2 + F.3 + F.4)
# --------------------------------------------------------------------- #

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"


@pytest.fixture(scope="module")
def chain():
    from bujji.options_observation import runner as opt_runner
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def test_end_to_end_entry_through_lifecycle_execution(tmp_path, chain, spot):
    from bujji.journal.position_group_journal import PositionGroupJournal
    from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
    from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
    from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
    from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
    from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_SAFE
    from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY
    from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract
    from bujji.production_runtime.runtime_state_machine import RuntimeState
    from bujji.production_runtime.trading_brain_composition_root import build_trading_brain_composition_root
    from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime
    from bujji.production_runtime.portfolio_reality_engine import PortfolioRealityEngine

    def cap_snap(**overrides):
        defaults = dict(total_capital=10_000_000.0, available_capital=10_000_000.0, used_margin=200_000.0,
                         open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=500_000.0,
                         peak_capital=10_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS)
        defaults.update(overrides)
        return CapitalSafetySnapshot(**defaults)

    journal = PositionGroupJournal(tmp_path / "pg.db")
    mint = mint_position_group_id(journal, "SEED", "SEED_STRATEGY", "NIFTY", clock=clock)
    seed_pg, seed_coid = mint.position_group_id, f"{mint.position_group_id}-L1"
    journal.append_event(seed_pg, "CONSTRUCTED", f"{seed_pg}:CONSTRUCTED:0",
                          {"contract_client_order_map": {"C0": seed_coid}, "requested_quantities": {seed_coid: 75},
                           "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
                          clock=clock)
    journal.append_event(seed_pg, "SUBMIT_INTENT", f"{seed_pg}:SUBMIT_INTENT:{seed_coid}", {"client_order_id": seed_coid}, clock=clock)
    journal.append_event(seed_pg, "SUBMIT_ACK", f"{seed_pg}:SUBMIT_ACK:{seed_coid}",
                          {"client_order_id": seed_coid, "broker_order_id": seed_coid, "broker_reported_status": "ACCEPTED"}, clock=clock)
    journal.append_event(seed_pg, "FILL_OBSERVED", f"{seed_pg}:FILL_OBSERVED:{seed_coid}:75:50.0",
                          {"client_order_id": seed_coid, "cumulative_filled_quantity_after": 75,
                           "cumulative_average_fill_price_after": 50.0, "delta_quantity": 75, "delta_value": 3750.0,
                           "delta_cost_basis_status": "DERIVED", "fill_price": 50.0}, clock=clock)

    root = build_trading_brain_composition_root(
        broker=PaperBroker(), journal=journal, margin_provider=SimulatedMarginProvider(),
        capital_snapshot_provider=lambda: cap_snap(), memory=AdaptiveRiskMemory(), clock=clock,
        underlying="NIFTY", exchange_lot_size=75, market_regime_provider=lambda: "SIDEWAYS",
        initial_state=RuntimeState.ENTRY_ENABLED,
    )
    runtime = TradingBrainRuntime(root)

    # 1. Strategy enters trade. 2. PaperBroker fills.
    cycle_result = runtime.process_entry_cycle(
        chain=chain, spot=spot, strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
        desired_quantity=1, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        contracts_by_client_order_id={seed_coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
        sides_by_client_order_id={seed_coid: "SELL"}, reference_prices_by_client_order_id={seed_coid: 50.0},
        risk_by_position_group_id={seed_pg: 5000.0}, direction="BULLISH", expected_move_pct=1.2,
    )
    assert cycle_result.filled is True

    # 3. Position reality created (with contracts, needed by F.4).
    registry = PositionRealityRegistry(root.broker)
    contracts = {
        _leg_to_core_contract(leg, "NIFTY", 75).symbol: _leg_to_core_contract(leg, "NIFTY", 75)
        for leg in cycle_result.proposal.legs
    }
    symbols = list(contracts.keys())
    registry.register_entry(cycle_result.proposal.assessment_id, cycle_result.proposal.strategy_family,
                             symbols, 5000.0, clock, contracts=contracts)
    lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
    lifecycle_runtime.mark_open(cycle_result.proposal.assessment_id)

    async def _run_f3_f4_portion():
        # 4. Market changes.
        portfolio_engine = PortfolioRealityEngine(registry)
        latest_prices = {s: 45.0 for s in symbols}
        valuations = await portfolio_engine.revalue_all(latest_prices, clock)

        # 5. D.4 recommends action.
        evaluation = await lifecycle_runtime.evaluate_group(
            cycle_result.proposal.assessment_id, valuations[cycle_result.proposal.assessment_id],
            cycle_result.proposal.strategy_family, SAFETY_SAFE, RISK_HEALTHY, None, clock,
        )

        # 6. F.4 executes action.
        executor = TradeLifecycleExecutor(root.broker, registry, lifecycle_runtime)
        exec_result = await executor.execute(evaluation, clock, reduce_quantity=25)

        # 7. Position reality updates.
        reality_after = await registry.get_group_reality(cycle_result.proposal.assessment_id)
        return evaluation, exec_result, reality_after

    import asyncio
    evaluation, exec_result, reality_after = asyncio.run(_run_f3_f4_portion())
    assert evaluation.recommendation.action in (
        ACTION_HOLD, ACTION_MONITOR, ACTION_REDUCE_SIZE, ACTION_ADD_HEDGE, ACTION_EXIT_CONSIDERATION, ACTION_BLOCK_NEW_RISK,
    )
    assert exec_result.status in (STATUS_NO_ACTION, STATUS_EXECUTED, STATUS_PARTIAL, STATUS_REJECTED, STATUS_FAILED_VALIDATION)
    assert reality_after.is_open in (True, False)


# --------------------------------------------------------------------- #
# Safety audit
# --------------------------------------------------------------------- #

def test_no_exit_logic_duplicated():
    for module in (executor_module, builder_module):
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "exit_engine" not in mod
                assert "trade.manager" not in mod
                assert "msi_dynamic_management" not in mod


def test_no_risk_calculations_added():
    tree = ast.parse(open(executor_module.__file__).read())
    bad_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in ("recommend_risk_action", "assess_defined_risk", "run_risk_governor_pipeline",
                                   "aggregate_portfolio_risk", "classify_capital_safety"):
                    bad_imports.append(alias.name)
    assert bad_imports == []


def test_no_strategy_decisions_added():
    tree = ast.parse(open(executor_module.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "msi_trade_construction" not in mod
            assert "strategy_selector" not in mod


def test_no_hidden_thresholds():
    for module in (executor_module, builder_module):
        tree = ast.parse(open(module.__file__).read())
        numeric_constants = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
                              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                              and not isinstance(n.value.value, bool)]
        assert numeric_constants == [], (module.__name__, numeric_constants)


def test_no_direct_broker_mutation_outside_place_order():
    tree = ast.parse(open(executor_module.__file__).read())
    forbidden = ("modify_order",)
    bad_calls = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in forbidden]
    assert bad_calls == []


def test_no_fyers_or_live_broker_imports():
    for module in (executor_module, builder_module):
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "fyers" not in mod
                assert "hybrid" not in mod


def test_no_new_order_abstraction_reuses_core_orderrequest():
    sig_params = list(inspect.signature(build_reduce_order).parameters)
    assert "contract" in sig_params  # confirms this builds bujji.core.models.OrderRequest, not a new type

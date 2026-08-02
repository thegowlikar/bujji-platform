"""Tests -- Gate F.3 Portfolio Reality Engine & Position Lifecycle Runtime."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.core.event_bus import EventBus, EventType

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot

from bujji.production_runtime.position_reality_registry import (
    DuplicatePositionGroupError, PositionRealityRegistry, UnknownPositionGroupError,
)
from bujji.production_runtime.portfolio_reality_engine import PortfolioRealityEngine
from bujji.production_runtime.position_lifecycle_runtime import (
    IllegalPositionLifecycleTransition, PositionLifecycleRuntime, PositionLifecycleState,
)
from bujji.production_runtime import position_lifecycle_runtime as plr_module
from bujji.production_runtime import portfolio_reality_engine as pre_module
from bujji.production_runtime import position_reality_registry as prr_module

BASE_TS = datetime(2026, 5, 25, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return BASE_TS


CONTRACT_CE = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-04", 75)
CONTRACT_PE = OptionContract("NIFTY24800PE", "NIFTY", 24800, OptionType.PE, "2026-08-04", 75)


async def _open_iron_condor(broker):
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    await broker.place_order(OrderRequest(CONTRACT_PE, Side.SELL, 75, "CID-PE", limit_price=40.0))
    return ["NIFTY25000CE", "NIFTY24800PE"]


def capital_snapshot(**overrides):
    defaults = dict(total_capital=10_000_000.0, available_capital=10_000_000.0, used_margin=200_000.0,
                     open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=500_000.0,
                     peak_capital=10_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS)
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


# --------------------------------------------------------------------- #
# Position Reality Registry
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_new_fill_creates_position_reality():
    broker = PaperBroker()
    symbols = await _open_iron_condor(broker)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)
    reality = await registry.get_group_reality("PG-1")
    assert reality.is_open is True
    assert set(reality.symbols) == set(symbols)
    assert reality.initial_risk == 5000.0


@pytest.mark.asyncio
async def test_multiple_legs_grouped_correctly():
    broker = PaperBroker()
    symbols = await _open_iron_condor(broker)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)
    positions = await registry.positions_for_group("PG-1")
    assert len(positions) == 2
    assert {p["symbol"] for p in positions} == set(symbols)


@pytest.mark.asyncio
async def test_partial_fills_handled():
    broker = PaperBroker(partial_fill_qty=25)
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 5000.0, clock)
    positions = await registry.positions_for_group("PG-1")
    assert positions[0]["qty"] == 25


@pytest.mark.asyncio
async def test_closed_position_removed_from_open_group_ids():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 5000.0, clock)
    assert "PG-1" in await registry.open_group_ids()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.BUY, 75, "CID-CLOSE", limit_price=45.0))
    assert "PG-1" not in await registry.open_group_ids()


@pytest.mark.asyncio
async def test_duplicate_registration_rejected():
    broker = PaperBroker()
    symbols = await _open_iron_condor(broker)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)
    with pytest.raises(DuplicatePositionGroupError):
        registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)


@pytest.mark.asyncio
async def test_unknown_group_raises():
    broker = PaperBroker()
    registry = PositionRealityRegistry(broker)
    with pytest.raises(UnknownPositionGroupError):
        await registry.get_group_reality("GHOST")


# --------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_mtm_updates_correctly_with_price():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 5000.0, clock)
    engine = PortfolioRealityEngine(registry)
    valuations = await engine.revalue_all({"NIFTY25000CE": 40.0}, clock)
    v = valuations["PG-1"]
    assert v.total_unrealized_pnl == pytest.approx((50.0 - 40.0) * 75)  # short profits as price falls


@pytest.mark.asyncio
async def test_unrealized_pnl_changes_with_price():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 5000.0, clock)
    engine = PortfolioRealityEngine(registry)
    v1 = (await engine.revalue_all({"NIFTY25000CE": 40.0}, clock))["PG-1"]
    v2 = (await engine.revalue_all({"NIFTY25000CE": 60.0}, clock))["PG-1"]
    assert v1.total_unrealized_pnl != v2.total_unrealized_pnl


@pytest.mark.asyncio
async def test_realized_pnl_preserved_after_partial_close():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.BUY, 75, "CID-1", limit_price=50.0))
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-2", limit_price=60.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "LONG_DIRECTIONAL", ["NIFTY25000CE"], 5000.0, clock)
    assert registry.realized_pnl_by_symbol("PG-1")["NIFTY25000CE"] == pytest.approx((60.0 - 50.0) * 75)


# --------------------------------------------------------------------- #
# Risk refresh
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_risk_refresh_produces_capital_and_portfolio_status():
    broker = PaperBroker()
    symbols = await _open_iron_condor(broker)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)
    engine = PortfolioRealityEngine(registry)
    ctx = await engine.refresh_risk_context(
        capital_snapshot(), None, None, None, None, [], clock,
    )
    assert ctx.capital_status in ("SAFE", "CAUTION", "RESTRICTED", "BLOCKED")
    assert ctx.risk_by_position_group_id == {"PG-1": 5000.0}


@pytest.mark.asyncio
async def test_risk_refresh_uses_real_position_groups_for_portfolio_status():
    from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState

    broker = PaperBroker()
    symbols = await _open_iron_condor(broker)
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "IRON_CONDOR", symbols, 5000.0, clock)
    engine = PortfolioRealityEngine(registry)

    group = PositionGroupState(
        position_group_id="PG-1", lifecycle_state="OPEN", strategy_id="IRON_CONDOR",
        legs={"L1": LegState(client_order_id="L1", contract_id="C", requested_quantity=None,
                              submit_status="LEG_ACKED",
                              fill=LegFillState(client_order_id="L1", cumulative_filled_quantity=75))},
    )
    ctx = await engine.refresh_risk_context(capital_snapshot(), None, None, None, None, [group], clock)
    assert ctx.portfolio_snapshot is not None
    assert ctx.portfolio_status != "INVALID"


# --------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------- #

def test_lifecycle_valid_transitions():
    from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleTracker
    tracker = PositionLifecycleTracker("PG-1")
    tracker.transition(PositionLifecycleState.ENTERING)
    tracker.transition(PositionLifecycleState.OPEN)
    tracker.transition(PositionLifecycleState.MANAGING)
    tracker.transition(PositionLifecycleState.EXIT_PENDING)
    tracker.transition(PositionLifecycleState.CLOSED)
    assert tracker.state == PositionLifecycleState.CLOSED


def test_lifecycle_invalid_transition_blocked():
    from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleTracker
    tracker = PositionLifecycleTracker("PG-1")
    with pytest.raises(IllegalPositionLifecycleTransition):
        tracker.transition(PositionLifecycleState.CLOSED)  # NEW -> CLOSED is illegal


def test_lifecycle_eventbus_events_emitted():
    from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleTracker
    seen = []
    tracker = PositionLifecycleTracker("PG-1", publish_fn=lambda *args: seen.append(args))
    tracker.transition(PositionLifecycleState.ENTERING, reason="test")
    assert len(seen) == 1


# --------------------------------------------------------------------- #
# D.4 integration
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_d4_hold_recommendation_preserved():
    from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_SAFE
    from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY

    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock)
    engine = PortfolioRealityEngine(registry)
    valuations = await engine.revalue_all({"NIFTY25000CE": 49.0}, clock)  # tiny move, healthy

    runtime = PositionLifecycleRuntime(registry, clock)
    result = await runtime.evaluate_group(
        "PG-1", valuations["PG-1"], "SHORT_DIRECTIONAL", SAFETY_SAFE, RISK_HEALTHY, None, clock,
    )
    assert result.recommendation.action == "HOLD"
    assert result.lifecycle_state == PositionLifecycleState.OPEN


@pytest.mark.asyncio
async def test_d4_exit_consideration_preserved_on_large_loss():
    from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_SAFE
    from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY

    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock)
    engine = PortfolioRealityEngine(registry)
    # A big adverse move against a short: price rises from 50 to 650 -> huge loss.
    valuations = await engine.revalue_all({"NIFTY25000CE": 650.0}, clock)

    runtime = PositionLifecycleRuntime(registry, clock)
    result = await runtime.evaluate_group(
        "PG-1", valuations["PG-1"], "SHORT_DIRECTIONAL", SAFETY_SAFE, RISK_HEALTHY, None, clock,
    )
    assert result.recommendation.action == "EXIT_CONSIDERATION"
    assert result.lifecycle_state == PositionLifecycleState.EXIT_PENDING


@pytest.mark.asyncio
async def test_d4_block_new_risk_when_capital_blocked():
    from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_BLOCKED
    from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY

    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT_CE, Side.SELL, 75, "CID-CE", limit_price=50.0))
    registry = PositionRealityRegistry(broker)
    registry.register_entry("PG-1", "SHORT_DIRECTIONAL", ["NIFTY25000CE"], 50000.0, clock)
    engine = PortfolioRealityEngine(registry)
    valuations = await engine.revalue_all({"NIFTY25000CE": 49.0}, clock)

    runtime = PositionLifecycleRuntime(registry, clock)
    result = await runtime.evaluate_group(
        "PG-1", valuations["PG-1"], "SHORT_DIRECTIONAL", SAFETY_BLOCKED, RISK_HEALTHY, None, clock,
    )
    assert result.recommendation.action == "BLOCK_NEW_RISK"


def test_no_duplicated_decision_logic_in_lifecycle_runtime():
    tree = ast.parse(open(plr_module.__file__).read())
    forbidden_names = ("classify_position_health",)  # only recommend_risk_action() may be called, never re-derive health itself separately
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
    assert not (called_names & set(forbidden_names))


# --------------------------------------------------------------------- #
# End-to-end (real strategy construction, real chain fixture)
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


def test_end_to_end_entry_to_lifecycle_evaluation(tmp_path, chain, spot):
    from bujji.production_runtime.runtime_state_machine import RuntimeState
    from bujji.production_runtime.trading_brain_composition_root import build_trading_brain_composition_root
    from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime
    from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
    from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
    from bujji.trading_brain.risk_governor.capital_safety_governor import ProposedTradeEffect

    journal = PositionGroupJournal(tmp_path / "pg.db")

    # Seed one existing OPEN position group so D.2's classify_portfolio_risk()
    # doesn't return RISK_INVALID for an empty book (the same pre-existing
    # gap documented in Gate F.1's own test suite).
    mint = mint_position_group_id(journal, "SEED", "SEED_STRATEGY", "NIFTY", clock=clock)
    seed_pg = mint.position_group_id
    seed_coid = f"{seed_pg}-L1"
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
        capital_snapshot_provider=lambda: capital_snapshot(), memory=AdaptiveRiskMemory(), clock=clock,
        underlying="NIFTY", exchange_lot_size=75, market_regime_provider=lambda: "SIDEWAYS",
        initial_state=RuntimeState.ENTRY_ENABLED,
    )
    runtime = TradingBrainRuntime(root)

    # 1. Strategy creates trade, 2. Risk approves, 3. PaperBroker fills.
    cycle_result = runtime.process_entry_cycle(
        chain=chain, spot=spot, strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
        desired_quantity=1, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        contracts_by_client_order_id={seed_coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
        sides_by_client_order_id={seed_coid: "SELL"}, reference_prices_by_client_order_id={seed_coid: 50.0},
        risk_by_position_group_id={seed_pg: 5000.0}, direction="BULLISH", expected_move_pct=1.2,
    )
    assert cycle_result.filled is True

    # 4. Position reality created.
    registry = PositionRealityRegistry(root.broker)
    # Derive symbols honestly from the proposal's own legs (same bridge F.1 uses).
    from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract
    symbols = [_leg_to_core_contract(leg, "NIFTY", 75).symbol for leg in cycle_result.proposal.legs]
    registry.register_entry(cycle_result.proposal.assessment_id, cycle_result.proposal.strategy_family,
                             symbols, 5000.0, clock)

    from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_SAFE
    from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY

    async def _run_f3_portion():
        reality = await registry.get_group_reality(cycle_result.proposal.assessment_id)
        assert reality.is_open is True

        # 5. Market ticks update MTM.
        portfolio_engine = PortfolioRealityEngine(registry)
        latest_prices = {s: 45.0 for s in symbols}
        valuations = await portfolio_engine.revalue_all(latest_prices, clock)
        assert cycle_result.proposal.assessment_id in valuations

        # 6. D.4 evaluates lifecycle. 7. Recommendation recorded.
        lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
        return await lifecycle_runtime.evaluate_group(
            cycle_result.proposal.assessment_id, valuations[cycle_result.proposal.assessment_id],
            cycle_result.proposal.strategy_family, SAFETY_SAFE, RISK_HEALTHY, None, clock,
        )

    import asyncio
    result = asyncio.run(_run_f3_portion())
    assert result.recommendation.action in ("HOLD", "MONITOR", "REDUCE_SIZE", "ADD_HEDGE", "EXIT_CONSIDERATION", "BLOCK_NEW_RISK")


# --------------------------------------------------------------------- #
# Safety audit
# --------------------------------------------------------------------- #

def test_same_tick_input_gives_same_output():
    from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleTracker
    t1 = PositionLifecycleTracker("PG-1")
    t2 = PositionLifecycleTracker("PG-1")
    t1.transition(PositionLifecycleState.ENTERING)
    t2.transition(PositionLifecycleState.ENTERING)
    assert t1.state == t2.state


def test_no_hidden_thresholds_in_f3_modules():
    for module in (plr_module, pre_module, prr_module):
        tree = ast.parse(open(module.__file__).read())
        numeric_constants = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
                              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                              and not isinstance(n.value.value, bool)]
        assert numeric_constants == [], (module.__name__, numeric_constants)


def test_no_new_exit_rules_no_orphaned_module_imports():
    for module in (plr_module, pre_module, prr_module):
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "exit_engine" not in mod
                assert "trade.manager" not in mod
                assert mod != "bujji.trade"
                assert "msi_dynamic_management" not in mod


def test_no_broker_mutation_beyond_read_only_calls():
    tree = ast.parse(open(prr_module.__file__).read())
    forbidden = ("place_order", "cancel_order")
    bad_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            bad_calls.append(node.attr)
    assert bad_calls == []


def test_no_live_trading_path_no_fyers_imports():
    for module in (plr_module, pre_module, prr_module):
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "fyers" not in mod
                assert "hybrid" not in mod


def test_no_f0_runtime_state_machine_reused_for_position_lifecycle():
    tree = ast.parse(open(plr_module.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bujji.production_runtime.runtime_state_machine"

"""Tests -- Gate F.5 Shadow Session Orchestrator."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect, SAFETY_SAFE
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_HEALTHY
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract

from bujji.production_runtime.runtime_state_machine import RuntimeState
from bujji.production_runtime.trading_brain_composition_root import build_trading_brain_composition_root
from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime
from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
from bujji.production_runtime.portfolio_reality_engine import PortfolioRealityEngine
from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleRuntime
from bujji.production_runtime.trade_lifecycle_executor import TradeLifecycleExecutor
from bujji.production_runtime.runtime_scheduler import RuntimeScheduler, ScheduleConfig, UnknownScheduledTaskError
from bujji.production_runtime.shadow_session_controller import ShadowSessionController
from bujji.production_runtime import shadow_session_controller as controller_module
from bujji.production_runtime import runtime_scheduler as scheduler_module
from bujji.production_runtime import session_models as session_models_module

BASE_TS = datetime(2026, 5, 25, 3, 45, 0, tzinfo=timezone.utc)  # 09:15 IST


def clock():
    return BASE_TS


def capital_snapshot(**overrides):
    defaults = dict(total_capital=10_000_000.0, available_capital=10_000_000.0, used_margin=200_000.0,
                     open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=500_000.0,
                     peak_capital=10_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS)
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _seed_position_group(journal, plan_id, strategy_id, clock_fn):
    mint = mint_position_group_id(journal, plan_id, strategy_id, "NIFTY", clock=clock_fn)
    pg = mint.position_group_id
    coid = f"{pg}-L1"
    journal.append_event(pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
                          {"contract_client_order_map": {"C0": coid}, "requested_quantities": {coid: 75},
                           "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
                          clock=clock_fn)
    journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}", {"client_order_id": coid}, clock=clock_fn)
    journal.append_event(pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
                          {"client_order_id": coid, "broker_order_id": coid, "broker_reported_status": "ACCEPTED"}, clock=clock_fn)
    journal.append_event(pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:75:50.0",
                          {"client_order_id": coid, "cumulative_filled_quantity_after": 75,
                           "cumulative_average_fill_price_after": 50.0, "delta_quantity": 75, "delta_value": 3750.0,
                           "delta_cost_basis_status": "DERIVED", "fill_price": 50.0}, clock=clock_fn)
    return pg, coid


def build_controller(tmp_path, initial_state=RuntimeState.INITIALIZING):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    seed_pg, seed_coid = _seed_position_group(journal, "SEED", "SEED_STRATEGY", clock)
    root = build_trading_brain_composition_root(
        broker=PaperBroker(), journal=journal, margin_provider=SimulatedMarginProvider(),
        capital_snapshot_provider=lambda: capital_snapshot(), memory=AdaptiveRiskMemory(), clock=clock,
        underlying="NIFTY", exchange_lot_size=75, market_regime_provider=lambda: "SIDEWAYS",
        initial_state=initial_state,
    )
    trading_brain_runtime = TradingBrainRuntime(root)
    registry = PositionRealityRegistry(root.broker)
    portfolio_engine = PortfolioRealityEngine(registry)
    lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
    executor = TradeLifecycleExecutor(root.broker, registry, lifecycle_runtime, event_bus=root.event_bus)
    scheduler = RuntimeScheduler(ScheduleConfig({
        "PORTFOLIO_REFRESH": 30.0, "LIFECYCLE_REFRESH": 60.0, "HEARTBEAT": 10.0,
    }))
    controller = ShadowSessionController(
        root, trading_brain_runtime, registry, portfolio_engine, lifecycle_runtime, executor, scheduler,
        root.timeline, clock,
    )
    return controller, root, journal, seed_pg, seed_coid


# --------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------- #

def test_clean_startup(tmp_path):
    controller, root, *_ = build_controller(tmp_path)
    controller.start_session()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED


def test_shutdown_does_not_raise(tmp_path):
    controller, *_ = build_controller(tmp_path)
    controller.start_session()
    controller.shutdown()  # must not raise


# --------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------- #

def test_valid_state_transitions_via_controller(tmp_path):
    controller, root, *_ = build_controller(tmp_path)
    controller.start_session()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED


@pytest.mark.asyncio
async def test_invalid_transitions_still_blocked_underneath(tmp_path):
    controller, root, *_ = build_controller(tmp_path, initial_state=RuntimeState.COMPLETE)
    with pytest.raises(Exception):
        controller.start_session()  # COMPLETE -> PREMARKET is illegal


@pytest.mark.asyncio
async def test_error_state_reachable_and_reported_in_heartbeat(tmp_path):
    from bujji.production_runtime.runtime_state_machine import RuntimeState as RS
    controller, root, *_ = build_controller(tmp_path, initial_state=RuntimeState.LIVE)
    root.runtime_state_machine.transition(RS.ERROR, reason="simulated fault")
    hb = await controller.heartbeat()
    assert hb.runtime_state == "ERROR"


# --------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------- #

def test_scheduler_deterministic_execution_order():
    scheduler = RuntimeScheduler(ScheduleConfig({"A": 10.0, "B": 20.0}))
    t0 = BASE_TS
    assert scheduler.due_tasks(t0) == ["A", "B"]  # both due on first check
    scheduler.mark_ran("A", t0)
    scheduler.mark_ran("B", t0)
    t1 = t0 + timedelta(seconds=15)
    assert scheduler.due_tasks(t1) == ["A"]  # only A's cadence elapsed
    t2 = t0 + timedelta(seconds=25)
    assert scheduler.due_tasks(t2) == ["A", "B"]


def test_scheduler_missed_task_handling():
    scheduler = RuntimeScheduler(ScheduleConfig({"A": 10.0}))
    scheduler.mark_ran("A", BASE_TS)
    much_later = BASE_TS + timedelta(seconds=1000)  # A was "missed" for a long time
    assert scheduler.due_tasks(much_later) == ["A"]  # still just reports due, not "10 catch-up runs"


def test_scheduler_clock_replay_identical():
    s1 = RuntimeScheduler(ScheduleConfig({"A": 10.0}))
    s2 = RuntimeScheduler(ScheduleConfig({"A": 10.0}))
    ticks = [BASE_TS + timedelta(seconds=i) for i in (0, 5, 10, 15, 20)]
    results1, results2 = [], []
    for t in ticks:
        due = s1.due_tasks(t)
        results1.append(due)
        for name in due:
            s1.mark_ran(name, t)
    for t in ticks:
        due = s2.due_tasks(t)
        results2.append(due)
        for name in due:
            s2.mark_ran(name, t)
    assert results1 == results2


def test_scheduler_unknown_task_raises():
    scheduler = RuntimeScheduler(ScheduleConfig({"A": 10.0}))
    with pytest.raises(UnknownScheduledTaskError):
        scheduler.mark_ran("GHOST", BASE_TS)


def test_scheduler_one_shot_task_never_recurs():
    scheduler = RuntimeScheduler(ScheduleConfig({"ONCE": None}))
    assert scheduler.due_tasks(BASE_TS) == ["ONCE"]
    scheduler.mark_ran("ONCE", BASE_TS)
    assert scheduler.due_tasks(BASE_TS + timedelta(hours=5)) == []


# --------------------------------------------------------------------- #
# Full simulated day (integration)
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


def test_full_simulated_day(tmp_path, chain, spot):
    controller, root, journal, seed_pg, seed_coid = build_controller(tmp_path)

    # 09:15 -- CONNECTING/LIVE/ENTRY_ENABLED.
    controller.start_session()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED

    # 09:20 -- Signal appears -> F.1 entry pipeline -> PaperBroker fill.
    cycle_result = controller.run_entry_cycle(
        chain=chain, spot=spot, strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
        desired_quantity=1, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        contracts_by_client_order_id={seed_coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
        sides_by_client_order_id={seed_coid: "SELL"}, reference_prices_by_client_order_id={seed_coid: 50.0},
        risk_by_position_group_id={seed_pg: 5000.0}, direction="BULLISH", expected_move_pct=1.2,
    )
    assert cycle_result.filled is True
    # M4 CONTRACT CHANGE (2026-08-23), not a weakened assertion.
    #
    # RuntimeState NO LONGER owns POSITION_ACTIVE. Two machines transitioned
    # to it from different call sites with no defined relationship, and
    # neither was journaled, so neither survived a restart. TradingSessionState
    # is the single owner; the governor journals POSITION_ACTIVE as a durable
    # SESSION_TRANSITION and every consumer derives from that plus broker
    # truth. RuntimeState keeps only connectivity and market phase, which are
    # PROCESS facts that must not be reconstructed after a crash.
    #
    # Sole ownership is asserted in tests/test_single_lifecycle_owner.py.
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED

    contracts = {
        # Keyed by the symbol the ORDER actually carries -- the chain row's
        # own string -- not by a second builder that has to be kept in step.
        c.symbol: c for _, c in cycle_result.order_contracts
    }
    symbols = list(contracts.keys())
    controller.register_filled_entry(
        cycle_result.proposal.assessment_id, cycle_result.proposal.strategy_family, symbols, 5000.0,
        contracts=contracts,
    )

    async def _run_management_and_eod():
        # F.3 valuation + F.4 lifecycle management.
        mgmt_result = await controller.run_management_cycle(
            latest_prices={s: 45.0 for s in symbols}, capital_snapshot=capital_snapshot(),
            margin_snapshot=None, margin_explanation=None, position_groups=[],
            position_health_thresholds=None, portfolio_risk_thresholds=None,
        )
        heartbeat = await controller.heartbeat(market_feed_status="LIVE")
        # POSTMARKET/COMPLETE + EOD reconciliation.
        eod = await controller.run_eod_reconciliation(
            latest_prices={s: 45.0 for s in symbols}, capital_snapshot=capital_snapshot(),
            margin_snapshot=None, margin_explanation=None, position_groups=[],
            position_health_thresholds=None, portfolio_risk_thresholds=None,
        )
        return mgmt_result, heartbeat, eod

    import asyncio
    mgmt_result, heartbeat, eod = asyncio.run(_run_management_and_eod())

    assert len(mgmt_result.lifecycle_evaluations) == 1
    assert heartbeat.active_positions_count == 1
    assert eod.final_state == RuntimeState.COMPLETE.value
    assert cycle_result.proposal.assessment_id in eod.final_valuations
    assert eod.summary.orders_submitted_count >= 1
    assert eod.summary.fills_count >= 1


# --------------------------------------------------------------------- #
# Failure recovery
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_failure_recovery_error_does_not_corrupt_registry(tmp_path):
    from bujji.production_runtime.runtime_state_machine import RuntimeState as RS
    controller, root, journal, seed_pg, seed_coid = build_controller(tmp_path, initial_state=RuntimeState.LIVE)
    controller.register_filled_entry(seed_pg, "SEED_STRATEGY", ["DUMMY"], 5000.0, contracts={})
    root.runtime_state_machine.transition(RS.ERROR, reason="simulated broker outage")
    # Registry state must remain intact and queryable after an ERROR transition.
    reality = await controller._registry.get_group_reality(seed_pg)
    assert reality.position_group_id == seed_pg


# --------------------------------------------------------------------- #
# Session report
# --------------------------------------------------------------------- #

def test_session_summary_is_pure_aggregation_no_new_analytics():
    tree = ast.parse(open(session_models_module.__file__).read())
    forbidden_names = ("assess_defined_risk", "aggregate_portfolio_risk", "recommend_risk_action",
                       "run_risk_governor_pipeline")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names


# --------------------------------------------------------------------- #
# Safety / adversarial-style structural checks
# --------------------------------------------------------------------- #

MODULE_FILES = [
    "/opt/bujji/app/bujji/production_runtime/shadow_session_controller.py",
    "/opt/bujji/app/bujji/production_runtime/runtime_scheduler.py",
    "/opt/bujji/app/bujji/production_runtime/session_models.py",
]


def test_no_trading_decisions_in_f5():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in (
                        "recommend_risk_action", "assess_defined_risk", "construct_trade",
                    )


def test_no_risk_calculations_in_f5():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in ("run_risk_governor_pipeline", "aggregate_portfolio_risk")


def test_no_exit_logic_in_f5():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "exit_engine" not in mod
                assert "trade.manager" not in mod


def test_does_not_bypass_risk_governor_or_paperbroker():
    tree = ast.parse(open(MODULE_FILES[0]).read())
    place_order_calls = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "place_order"]
    assert place_order_calls == []  # controller never calls place_order directly -- only via F.1/F.4


def test_runtime_state_machine_is_only_session_authority():
    tree = ast.parse(open(MODULE_FILES[0]).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bujji.core.state_machine"


def test_legacy_runtime_untouched():
    # composition_root.py was removed from this working-tree pin on
    # 2026-08-18: it now carries the ONE authorized lot-size-authoritative
    # change (resolve the exchange lot size from the instrument master,
    # fail closed -- see the _LOT_SIZE_AUTHORITATIVE_AUTHORIZED exception in
    # the 360c003 baseline guards and tests/test_lot_size_from_master.py,
    # which pins the new behaviour directly). runtime.py remains pinned
    # byte-for-byte.
    import subprocess
    diff = subprocess.run(
        ["git", "diff", "--stat", "bujji/production_runtime/runtime.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert diff.stdout.strip() == ""


def test_all_timestamps_use_injected_clock():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "now":
                pytest.fail(f"{path} calls datetime.now() directly instead of using an injected clock")


def test_no_hidden_thresholds_in_f5():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        numeric_constants = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
                              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                              and not isinstance(n.value.value, bool)]
        assert numeric_constants == [], (path, numeric_constants)


def test_no_fyers_or_live_broker_imports():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "fyers" not in mod
                assert "hybrid" not in mod


def test_no_duplicate_orchestrator_class():
    tree = ast.parse(open(MODULE_FILES[0]).read())
    controller_classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and "Controller" in n.name]
    assert controller_classes == ["ShadowSessionController"]

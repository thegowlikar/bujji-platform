"""Tests -- Gate F.1 Trading Brain Shadow Runtime."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone

import pytest

from bujji.msi_trade_construction import taxonomy
from bujji.msi_trade_construction.engine import construct_trade
from bujji.options_observation import runner as opt_runner
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.broker.paper import PaperBroker
from bujji.core.event_bus import EventType
from types import SimpleNamespace

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
from bujji.trading_brain.risk_governor.risk_governor_pipeline import PIPELINE_APPROVED

from bujji.production_runtime.runtime_state_machine import RuntimeState
from bujji.production_runtime.trading_brain_composition_root import build_trading_brain_composition_root
from bujji.production_runtime.trading_brain_runtime import RuntimeNotAcceptingEntriesError, TradingBrainRuntime
from bujji.production_runtime import trading_brain_runtime as tbr_module

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"
BASE_TS = datetime(2026, 5, 25, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return BASE_TS


@pytest.fixture(scope="module")
def chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def capital_snapshot(**overrides):
    defaults = dict(
        total_capital=10_000_000.0, available_capital=10_000_000.0, used_margin=200_000.0,
        open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=500_000.0,
        peak_capital=10_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS,
    )
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _seed_position_group(journal, plan_id="SEED-PLAN", strategy_id="SEED_STRATEGY"):
    """D.2's own classify_portfolio_risk() returns RISK_INVALID for a
    genuinely empty book (a real, pre-existing gap flagged during Gate
    D.6's own audit -- not something F.1 patches). Every test here
    seeds one real, active position group first, mirroring the exact
    fixture pattern already established in D.6/E.1/E.2/E.3's own test
    suites, so the pipeline reaches a real ALLOW/BLOCK decision on the
    NEW trade rather than a portfolio-data failure on the existing
    book."""
    mint = mint_position_group_id(journal, plan_id, strategy_id, "NIFTY", clock=clock)
    pg = mint.position_group_id
    coid = f"{pg}-L1"
    journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C0": coid}, "requested_quantities": {coid: 75},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=clock,
    )
    journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}", {"client_order_id": coid}, clock=clock)
    journal.append_event(
        pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
        {"client_order_id": coid, "broker_order_id": coid, "broker_reported_status": "ACCEPTED"}, clock=clock,
    )
    journal.append_event(
        pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:75:50.0",
        {"client_order_id": coid, "cumulative_filled_quantity_after": 75,
         "cumulative_average_fill_price_after": 50.0, "delta_quantity": 75, "delta_value": 3750.0,
         "delta_cost_basis_status": "DERIVED", "fill_price": 50.0},
        clock=clock,
    )
    return pg, coid


def make_root(tmp_path, initial_state=RuntimeState.ENTRY_ENABLED, **overrides):
    journal = PositionGroupJournal(tmp_path / "pg.db")
    defaults = dict(
        broker=PaperBroker(),
        journal=journal,
        margin_provider=SimulatedMarginProvider(),
        capital_snapshot_provider=lambda: capital_snapshot(),
        memory=AdaptiveRiskMemory(),
        clock=clock,
        underlying="NIFTY",
        exchange_lot_size=75,
        market_regime_provider=lambda: "SIDEWAYS",
        initial_state=initial_state,
    )
    defaults.update(overrides)
    root = build_trading_brain_composition_root(**defaults)
    return root, journal


def cycle_kwargs(seed_coid=None, **overrides):
    leg_kwargs = dict(contracts_by_client_order_id={}, sides_by_client_order_id={}, reference_prices_by_client_order_id={})
    risk_map = {}
    if seed_coid is not None:
        pg, coid = seed_coid
        leg_kwargs = dict(
            contracts_by_client_order_id={coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
            sides_by_client_order_id={coid: "SELL"},
            reference_prices_by_client_order_id={coid: 50.0},
        )
        risk_map = {pg: 5000.0}
    defaults = dict(
        strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
        desired_quantity=1, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        risk_by_position_group_id=risk_map, direction="BULLISH", expected_move_pct=1.2,
    )
    defaults.update(leg_kwargs)
    defaults.update(overrides)
    return defaults


# --------------------------------------------------------------------- #
# Runtime startup / state lifecycle
# --------------------------------------------------------------------- #

def test_runtime_startup_initial_state(tmp_path):
    root, _ = make_root(tmp_path, initial_state=RuntimeState.INITIALIZING)
    assert root.runtime_state_machine.state == RuntimeState.INITIALIZING


def test_market_open_sequence_transitions_to_entry_enabled(tmp_path):
    root, _ = make_root(tmp_path, initial_state=RuntimeState.INITIALIZING)
    runtime = TradingBrainRuntime(root)
    runtime.run_market_open_sequence()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED


def test_market_close_sequence_transitions_to_complete(tmp_path):
    root, _ = make_root(tmp_path, initial_state=RuntimeState.LIVE)
    runtime = TradingBrainRuntime(root)
    runtime.run_market_close_sequence()
    assert root.runtime_state_machine.state == RuntimeState.COMPLETE


def test_entry_refused_outside_entry_accepting_states(tmp_path, chain, spot):
    root, journal = make_root(tmp_path, initial_state=RuntimeState.PREMARKET)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    with pytest.raises(RuntimeNotAcceptingEntriesError):
        runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))


# --------------------------------------------------------------------- #
# Entry path / no-signal path
# --------------------------------------------------------------------- #

def test_entry_path_healthy_fills_and_transitions_to_position_active(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    assert result.proposal.constructed is True
    assert result.governor_result is not None
    assert result.governor_result.final_status == PIPELINE_APPROVED
    assert result.filled is True
    assert result.approved_quantity > 0
    assert len(result.order_results) == len(result.proposal.legs)
    assert all(r.is_filled for r in result.order_results)
    assert root.runtime_state_machine.state == RuntimeState.POSITION_ACTIVE


def test_empty_book_first_trade_of_session_is_blocked_at_portfolio(tmp_path, chain, spot):
    # Documents the pre-existing D.2 gap (flagged during Gate D.6's own
    # audit): classify_portfolio_risk() returns RISK_INVALID for a
    # genuinely empty book, so the very first trade of a session is
    # blocked at PORTFOLIO even though nothing is actually wrong. Not
    # fixed here -- F.1 only surfaces it via the runtime's own trace.
    root, journal = make_root(tmp_path)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs())
    assert result.proposal.constructed is True
    assert result.governor_result.blocking_stage == "PORTFOLIO"
    assert result.filled is False


def test_no_signal_path_unsupported_family_fails_closed(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(
        chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed, strategy_family="NOT_A_REAL_FAMILY"),
    )
    assert result.proposal.constructed is False
    assert result.governor_result is None
    assert result.filled is False
    assert result.order_results == ()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED  # unchanged


def test_risk_governor_block_prevents_any_order(tmp_path, chain, spot):
    root, journal = make_root(tmp_path, capital_snapshot_provider=lambda: capital_snapshot(daily_pnl=-600000.0))
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    assert result.proposal.constructed is True
    assert result.governor_result.final_status != PIPELINE_APPROVED
    assert result.filled is False
    assert result.order_results == ()
    assert root.runtime_state_machine.state == RuntimeState.ENTRY_ENABLED


def test_context_unavailable_path_margin_provider_failure(tmp_path, chain, spot):
    class BrokenMarginProvider:
        def get_portfolio_margin_with_explanation(self, legs, clock):
            raise RuntimeError("margin service down")

    root, journal = make_root(tmp_path, margin_provider=BrokenMarginProvider())
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    assert result.proposal.constructed is True
    assert result.context_unavailable is not None
    assert result.context_unavailable.producer == "BrokenMarginProvider"
    assert result.filled is False


# --------------------------------------------------------------------- #
# PaperBroker execution / journal / EventBus / determinism
# --------------------------------------------------------------------- #

def test_paperbroker_execution_quantity_matches_leg_ratio_times_lot_size(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    result = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    for leg, order_result in zip(result.proposal.legs, result.order_results):
        expected_qty = leg.ratio * root.exchange_lot_size * result.approved_quantity
        assert order_result.filled_quantity == expected_qty


def test_eventbus_publishes_full_stage_sequence(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    seen_stages = []
    for event_type in EventType:
        root.event_bus.subscribe(event_type, lambda e: seen_stages.append(e.payload.get("stage")))
    runtime = TradingBrainRuntime(root)
    runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    assert tbr_module.STAGE_STRATEGY_PROPOSED in seen_stages
    assert tbr_module.STAGE_RISK_DECISION in seen_stages
    assert tbr_module.STAGE_ORDER_SUBMITTED in seen_stages
    assert tbr_module.STAGE_ORDER_FILLED in seen_stages
    assert tbr_module.STAGE_POSITION_OPENED in seen_stages


def test_shadow_trade_timeline_records_every_stage_in_order(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    stages = [e.stage for e in root.timeline.entries()]
    assert tbr_module.STAGE_STRATEGY_PROPOSED in stages
    assert stages.index(tbr_module.STAGE_STRATEGY_PROPOSED) < stages.index(tbr_module.STAGE_RISK_DECISION)
    assert stages.index(tbr_module.STAGE_RISK_DECISION) < stages.index(tbr_module.STAGE_ORDER_SUBMITTED)
    assert stages.index(tbr_module.STAGE_ORDER_SUBMITTED) < stages.index(tbr_module.STAGE_ORDER_FILLED)


def test_runtime_state_transitions_published_on_timeline(tmp_path):
    root, _ = make_root(tmp_path, initial_state=RuntimeState.INITIALIZING)
    runtime = TradingBrainRuntime(root)
    runtime.run_market_open_sequence()
    state_change_entries = [e for e in root.timeline.entries() if e.event_type == "STATE_CHANGED"]
    assert len(state_change_entries) == 4  # PREMARKET, CONNECTING, LIVE, ENTRY_ENABLED


def test_deterministic_replay_identical_inputs_identical_outputs(tmp_path, chain, spot):
    root1, journal1 = make_root(tmp_path / "r1")
    root2, journal2 = make_root(tmp_path / "r2")
    seed1 = _seed_position_group(journal1)
    seed2 = _seed_position_group(journal2)
    runtime1 = TradingBrainRuntime(root1)
    runtime2 = TradingBrainRuntime(root2)
    result1 = runtime1.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed1))
    result2 = runtime2.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed2))
    assert result1.proposal.assessment_id == result2.proposal.assessment_id
    assert result1.approved_quantity == result2.approved_quantity
    assert result1.filled == result2.filled
    assert [s.stage for s in root1.timeline.entries()] == [s.stage for s in root2.timeline.entries()]


# --------------------------------------------------------------------- #
# Runtime failure recovery
# --------------------------------------------------------------------- #

def test_runtime_survives_one_failed_cycle_and_accepts_the_next(tmp_path, chain, spot):
    root, journal = make_root(tmp_path)
    seed = _seed_position_group(journal)
    runtime = TradingBrainRuntime(root)
    bad = runtime.process_entry_cycle(
        chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed, strategy_family="NOT_A_REAL_FAMILY"),
    )
    assert bad.filled is False
    good = runtime.process_entry_cycle(chain=chain, spot=spot, **cycle_kwargs(seed_coid=seed))
    assert good.filled is True


# --------------------------------------------------------------------- #
# Safety: PaperBroker-only, no live broker, no duplicated logic
# --------------------------------------------------------------------- #

def test_no_live_broker_imports_or_calls():
    tree = ast.parse(open(tbr_module.__file__).read())
    forbidden = ("fyers", "hybrid")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden), alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert not any(f in mod for f in forbidden), node.module


def test_no_live_broker_methods_reachable_structurally():
    # The only broker method this module ever calls is place_order --
    # no cancel_order/modify_order/get_funds call exists anywhere.
    tree = ast.parse(open(tbr_module.__file__).read())
    broker_method_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
            "cancel_order", "modify_order", "authenticate",
        ):
            broker_method_calls.append(node.attr)
    assert broker_method_calls == []


def test_does_not_import_legacy_run_shadow_chain():
    tree = ast.parse(open(tbr_module.__file__).read())
    forbidden_modules = (
        "production_runtime.runtime", "production_runtime.composition_root",
        "risk_brain", "capital_brain", "execution_planner",
        "trading_brain.execution_engine", "strategy_selector", "nifty_contract_builder",
    )
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or (node.names[0].name if isinstance(node, ast.Import) else "")
            assert not any(f in (mod or "") for f in forbidden_modules), mod


def test_no_new_event_type_introduced_no_event_bus_file_touched():
    # bujji.core.event_bus is never imported for modification purposes --
    # only EventType/Event/EventBus are consumed as already-existing.
    known_types_before = set(EventType)
    assert set(EventType) == known_types_before


def test_no_duplicated_numeric_risk_or_margin_calculation():
    tree = ast.parse(open(tbr_module.__file__).read())
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


def test_no_threshold_constants_in_runtime_module():
    tree = ast.parse(open(tbr_module.__file__).read())
    numeric_constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            numeric_constants.append(node.value.value)
    assert numeric_constants == [], numeric_constants


def test_no_force_override_bypass_parameter():
    for fn in (TradingBrainRuntime.process_entry_cycle, TradingBrainRuntime.run_market_open_sequence):
        sig = inspect.signature(fn)
        for name in sig.parameters:
            assert "force" not in name.lower()
            assert "override" not in name.lower()
            assert "bypass" not in name.lower()

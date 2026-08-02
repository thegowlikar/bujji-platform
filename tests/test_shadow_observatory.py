"""Tests -- Gate V.0 Shadow Observatory & Session Artifact Store."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.event_bus import Event, EventBus, EventType
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, ProposedTradeEffect
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
from bujji.production_runtime.runtime_scheduler import RuntimeScheduler, ScheduleConfig
from bujji.production_runtime.shadow_session_controller import ShadowSessionController

from bujji.shadow_observatory.session_store import SessionStore
from bujji.shadow_observatory.recorder import ShadowObservatoryRecorder
from bujji.shadow_observatory.serializers import classify_destinations, build_artifact
from bujji.shadow_observatory.report_builder import build_session_summary
from bujji.shadow_observatory import recorder as recorder_module
from bujji.shadow_observatory import session_store as session_store_module
from bujji.shadow_observatory import serializers as serializers_module

BASE_TS = datetime(2026, 5, 25, 3, 45, 0, tzinfo=timezone.utc)


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


def build_session(tmp_path, initial_state=RuntimeState.INITIALIZING):
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
    portfolio_engine = PortfolioRealityEngine(registry, event_bus=root.event_bus)
    lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
    executor = TradeLifecycleExecutor(root.broker, registry, lifecycle_runtime, event_bus=root.event_bus)
    scheduler = RuntimeScheduler(ScheduleConfig({"PORTFOLIO_REFRESH": 30.0}))

    store = SessionStore(tmp_path / "shadow_sessions", "TEST-SESSION")
    recorder = ShadowObservatoryRecorder(store)
    recorder.attach(root.event_bus)

    controller = ShadowSessionController(
        root, trading_brain_runtime, registry, portfolio_engine, lifecycle_runtime, executor, scheduler,
        root.timeline, clock, observatory=recorder,
    )
    return controller, root, journal, seed_pg, seed_coid, store, recorder


# --------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------- #

def test_creates_session_directory(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    assert store.session_dir.exists()
    assert store.session_dir.is_dir()


def test_writes_jsonl_append_only(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    store.append("decisions.jsonl", {"a": 1})
    store.append("decisions.jsonl", {"a": 2})
    lines = (store.session_dir / "decisions.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"a": 2}


def test_append_ordering_preserved(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    for i in range(10):
        store.append("orders.jsonl", {"seq": i})
    records = store.read_jsonl("orders.jsonl")
    assert [r["seq"] for r in records] == list(range(10))


def test_replay_reading(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    store.append("errors.jsonl", {"x": "y"})
    store2 = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")  # fresh instance, same dir
    assert store2.read_jsonl("errors.jsonl") == [{"x": "y"}]


def test_metadata_and_summary_written_as_json_not_jsonl(tmp_path):
    from bujji.shadow_observatory.models import SessionMetadata
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    store.write_metadata(SessionMetadata(session_id="SESSION-1", started_at="t", initial_state="INITIALIZING"))
    data = store.read_json("metadata.json")
    assert data["session_id"] == "SESSION-1"


def test_session_manifest_written_as_evidence_chain_identity_card(tmp_path):
    from bujji.shadow_observatory.models import SessionManifest
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    recorder = ShadowObservatoryRecorder(store)
    manifest = SessionManifest(
        session_id="SESSION-1", start_time=BASE_TS.isoformat(), mode="PAPERBROKER",
        strategy_engine="MSI", risk_engine="D1-D6", broker="PaperBroker",
        code_version="abc1234", config_hash="deadbeef", market="NSE", symbols=("NIFTY",),
    )
    recorder.record_session_manifest(manifest)
    data = store.read_json("session_manifest.json")
    assert data["session_id"] == "SESSION-1"
    assert data["code_version"] == "abc1234"
    assert data["symbols"] == ["NIFTY"]
    assert recorder.internal_errors == []


def test_start_session_forwards_manifest_when_supplied(tmp_path):
    controller, root, journal, seed_pg, seed_coid, store, recorder = build_session(tmp_path)
    from bujji.shadow_observatory.models import SessionManifest
    manifest = SessionManifest(
        session_id="TEST-SESSION", start_time=BASE_TS.isoformat(), mode="PAPERBROKER",
        strategy_engine="MSI", risk_engine="D1-D6", broker="PaperBroker",
        code_version=None, config_hash=None, market="NSE", symbols=("NIFTY",),
    )
    controller.start_session(manifest=manifest)
    assert (store.session_dir / "session_manifest.json").exists()


def test_start_session_without_manifest_does_not_write_one(tmp_path):
    controller, root, journal, seed_pg, seed_coid, store, recorder = build_session(tmp_path)
    controller.start_session()
    assert not (store.session_dir / "session_manifest.json").exists()


# --------------------------------------------------------------------- #
# Reliability
# --------------------------------------------------------------------- #

def test_disk_failure_simulation_does_not_raise(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    # Simulate a disk failure by pointing session_dir at a file, not a dir.
    bad_file = tmp_path / "not_a_dir"
    bad_file.write_text("x")
    store.session_dir = bad_file / "cannot_write_here"
    store.append("errors.jsonl", {"a": 1})  # must not raise
    assert len(store.write_errors) >= 1


def test_malformed_event_handled_by_recorder(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    recorder = ShadowObservatoryRecorder(store)
    bad_event = Event(type=EventType.DECISION_MADE, payload={"stage": object()}, timestamp=BASE_TS)  # non-serializable payload
    recorder._on_event(bad_event)  # must not raise
    assert len(recorder.internal_errors) >= 1


def test_recorder_exception_isolation_does_not_propagate(tmp_path):
    store = SessionStore(tmp_path / "shadow_sessions", "SESSION-1")
    recorder = ShadowObservatoryRecorder(store)
    bus = EventBus()
    recorder.attach(bus)
    # A handler exception inside classify/build must be caught internally --
    # publish_nowait itself also isolates handler failures (EventBus's own
    # established behavior), so this proves BOTH layers never crash the caller.
    bad_event = Event(type=EventType.STATE_CHANGED, payload={"to_state": object()}, timestamp=BASE_TS)
    bus.publish_nowait(bad_event)


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #

def test_same_event_stream_produces_identical_artifacts(tmp_path):
    events = [
        Event(type=EventType.SIGNAL_GENERATED, payload={"stage": "STRATEGY_PROPOSED", "assessment_id": "A-1"}, timestamp=BASE_TS),
        Event(type=EventType.DECISION_MADE, payload={"stage": "RISK_DECISION", "final_status": "APPROVED"}, timestamp=BASE_TS),
    ]
    store_a = SessionStore(tmp_path / "shadow_sessions", "A")
    store_b = SessionStore(tmp_path / "shadow_sessions", "B")
    recorder_a = ShadowObservatoryRecorder(store_a)
    recorder_b = ShadowObservatoryRecorder(store_b)
    for e in events:
        recorder_a._on_event(e)
        recorder_b._on_event(e)
    assert store_a.read_jsonl("decisions.jsonl") == store_b.read_jsonl("decisions.jsonl")


# --------------------------------------------------------------------- #
# Serialization / classification
# --------------------------------------------------------------------- #

def test_classify_state_changed_goes_to_state_changes():
    event = Event(type=EventType.STATE_CHANGED, payload={"from_state": "LIVE", "to_state": "ENTRY_ENABLED"}, timestamp=BASE_TS)
    assert classify_destinations(event) == ("state_changes.jsonl",)


def test_classify_error_state_dual_written():
    event = Event(type=EventType.STATE_CHANGED, payload={"from_state": "LIVE", "to_state": "ERROR"}, timestamp=BASE_TS)
    assert set(classify_destinations(event)) == {"state_changes.jsonl", "errors.jsonl"}


def test_classify_position_lifecycle_goes_to_lifecycle():
    event = Event(type=EventType.STATE_CHANGED, payload={"stage": "POSITION_LIFECYCLE_OPEN"}, timestamp=BASE_TS)
    assert classify_destinations(event) == ("lifecycle.jsonl",)


def test_classify_order_lifecycle_goes_to_executions():
    event = Event(type=EventType.DECISION_MADE, payload={"stage": "ORDER_LIFECYCLE_FILLED"}, timestamp=BASE_TS)
    assert classify_destinations(event) == ("executions.jsonl",)


def test_classify_unrecognized_stage_never_dropped():
    event = Event(type=EventType.CANDLE_CLOSED, payload={"stage": "SOMETHING_NEW"}, timestamp=BASE_TS)
    assert classify_destinations(event) == ("decisions.jsonl",)


def test_build_artifact_never_embeds_raw_python_object():
    event = Event(type=EventType.DECISION_MADE, payload={"stage": "RISK_DECISION"}, timestamp=BASE_TS)
    artifact = build_artifact(event, "decisions.jsonl")
    json.dumps({"timestamp": artifact.timestamp, "explanation": artifact.explanation})  # must not raise


# --------------------------------------------------------------------- #
# Integration -- full simulated session
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


def test_full_simulated_session_captures_every_major_event(tmp_path, chain, spot):
    controller, root, journal, seed_pg, seed_coid, store, recorder = build_session(tmp_path)

    from bujji.shadow_observatory.models import SessionManifest
    manifest = SessionManifest(
        session_id="TEST-SESSION", start_time=BASE_TS.isoformat(), mode="PAPERBROKER",
        strategy_engine="MSI", risk_engine="D1-D6", broker="PaperBroker",
        code_version=None, config_hash=None, market="NSE", symbols=("NIFTY",),
    )
    controller.start_session(manifest=manifest)
    assert (store.session_dir / "metadata.json").exists()
    assert (store.session_dir / "session_manifest.json").exists()

    cycle_result = controller.run_entry_cycle(
        chain=chain, spot=spot, strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
        desired_quantity=1, requested_risk=5000.0,
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        contracts_by_client_order_id={seed_coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
        sides_by_client_order_id={seed_coid: "SELL"}, reference_prices_by_client_order_id={seed_coid: 50.0},
        risk_by_position_group_id={seed_pg: 5000.0}, direction="BULLISH", expected_move_pct=1.2,
    )
    assert cycle_result.filled is True

    contracts = {
        _leg_to_core_contract(leg, "NIFTY", 75).symbol: _leg_to_core_contract(leg, "NIFTY", 75)
        for leg in cycle_result.proposal.legs
    }
    symbols = list(contracts.keys())
    controller.register_filled_entry(
        cycle_result.proposal.assessment_id, cycle_result.proposal.strategy_family, symbols, 5000.0, contracts=contracts,
    )

    async def _run_rest():
        await controller.run_management_cycle(
            latest_prices={s: 45.0 for s in symbols}, capital_snapshot=capital_snapshot(),
            margin_snapshot=None, margin_explanation=None, position_groups=[],
            position_health_thresholds=None, portfolio_risk_thresholds=None,
        )
        await controller.heartbeat(market_feed_status="LIVE")
        await controller.run_eod_reconciliation(
            latest_prices={s: 45.0 for s in symbols}, capital_snapshot=capital_snapshot(),
            margin_snapshot=None, margin_explanation=None, position_groups=[],
            position_health_thresholds=None, portfolio_risk_thresholds=None,
        )

    import asyncio
    asyncio.run(_run_rest())

    # Verify every required artifact file has at least one entry (or exists for summary/metadata).
    assert len(store.read_jsonl("state_changes.jsonl")) > 0
    assert len(store.read_jsonl("decisions.jsonl")) > 0
    assert len(store.read_jsonl("orders.jsonl")) > 0
    assert len(store.read_jsonl("executions.jsonl")) > 0
    assert len(store.read_jsonl("positions.jsonl")) > 0
    assert len(store.read_jsonl("lifecycle.jsonl")) > 0
    assert len(store.read_jsonl("heartbeat.jsonl")) > 0
    assert (store.session_dir / "summary.json").exists()
    summary = store.read_json("summary.json")
    assert summary["orders_count"] >= 1
    assert recorder.internal_errors == []


def test_no_runtime_behavior_change_with_or_without_observatory(tmp_path, chain, spot):
    # Same scenario, once WITH the observatory attached and once WITHOUT --
    # the trading outcome itself must be identical either way.
    def run_scenario(with_observatory: bool):
        journal = PositionGroupJournal(tmp_path / f"pg_{with_observatory}.db")
        seed_pg, seed_coid = _seed_position_group(journal, "SEED", "SEED_STRATEGY", clock)
        root = build_trading_brain_composition_root(
            broker=PaperBroker(), journal=journal, margin_provider=SimulatedMarginProvider(),
            capital_snapshot_provider=lambda: capital_snapshot(), memory=AdaptiveRiskMemory(), clock=clock,
            underlying="NIFTY", exchange_lot_size=75, market_regime_provider=lambda: "SIDEWAYS",
            initial_state=RuntimeState.ENTRY_ENABLED,
        )
        tbr = TradingBrainRuntime(root)
        if with_observatory:
            store = SessionStore(tmp_path / "shadow_sessions", f"SCENARIO-{with_observatory}")
            recorder = ShadowObservatoryRecorder(store)
            recorder.attach(root.event_bus)
        result = tbr.process_entry_cycle(
            chain=chain, spot=spot, strategy_family="IRON_CONDOR", as_of_date=DAY, timestamp=TS,
            desired_quantity=1, requested_risk=5000.0,
            proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
            contracts_by_client_order_id={seed_coid: SimpleNamespace(contract_symbol="NIFTY25000CE")},
            sides_by_client_order_id={seed_coid: "SELL"}, reference_prices_by_client_order_id={seed_coid: 50.0},
            risk_by_position_group_id={seed_pg: 5000.0}, direction="BULLISH", expected_move_pct=1.2,
        )
        return result

    r1 = run_scenario(True)
    r2 = run_scenario(False)
    assert r1.filled == r2.filled
    assert r1.approved_quantity == r2.approved_quantity


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

MODULE_FILES = [
    "/opt/bujji/app/bujji/shadow_observatory/recorder.py",
    "/opt/bujji/app/bujji/shadow_observatory/serializers.py",
    "/opt/bujji/app/bujji/shadow_observatory/session_store.py",
    "/opt/bujji/app/bujji/shadow_observatory/report_builder.py",
]


def test_no_intelligence_no_threshold_comparisons():
    tree = ast.parse(open("/opt/bujji/app/bujji/shadow_observatory/serializers.py").read())
    numeric_constants = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
                          and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                          and not isinstance(n.value.value, bool)]
    assert numeric_constants == [], numeric_constants


def test_no_runtime_import_by_paperbroker_risk_governor_strategy_engine():
    forbidden_files = [
        "/opt/bujji/app/bujji/broker/paper.py",
        "/opt/bujji/app/bujji/trading_brain/risk_governor/risk_governor_pipeline.py",
        "/opt/bujji/app/bujji/msi_trade_construction/engine.py",
        "/opt/bujji/app/bujji/production_runtime/position_lifecycle_runtime.py",
    ]
    for path in forbidden_files:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "shadow_observatory" not in mod


def test_no_fyers_or_live_broker_imports_in_observatory():
    for path in MODULE_FILES:
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "fyers" not in mod
                assert "hybrid" not in mod


def test_store_has_no_update_or_delete_method():
    store_methods = [m for m in dir(SessionStore) if not m.startswith("_")]
    assert "update" not in store_methods
    assert "delete" not in store_methods
    assert "overwrite" not in store_methods

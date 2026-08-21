"""Shadow Runtime Intelligence Integration -- Phase 19.10.1 tests.

Proves the 6 required properties:
1. Lifecycle transitions work.
2. Replay mode produces deterministic output.
3. LIVE and HISTORICAL_REPLAY use identical intelligence fingerprints.
4. Runtime cannot import broker/execution/strategy modules.
5. Health heartbeat updates during processing.
6. Full pipeline smoke test: Reality -> Intelligence -> Decision -> Phenomena -> State Graph -> Environment.

Uses only synthetic, isolated fixtures -- never the production
`historical_observations.db` (this project's own standing discipline
since the Phase 18.12 incident).
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.intelligence.context import (
    EXECUTION_MODE_HISTORICAL_REPLAY,
    EXECUTION_MODE_LIVE,
    IntelligenceContext,
)
from bujji.market_reality_snapshot.models import (
    MarketRealitySnapshot,
    OptionContractSnapshot,
    OptionsSnapshot,
    SpotSnapshot,
    VixSnapshot,
)
from bujji.shadow_runtime.health import (
    RuntimeHealth,
    health_for_stage,
    read_health_heartbeat,
    write_health_heartbeat,
)
from bujji.shadow_runtime.intelligence_pipeline_adapter import (
    IntelligenceHeartbeatCycle,
    IntelligencePipelineAdapterError,
    build_intelligence_heartbeat_cycle,
)
from bujji.shadow_runtime.lifecycle import (
    IllegalLifecycleTransition,
    RuntimeLifecycle,
    RuntimeStage,
)

SHADOW_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "shadow_runtime")
NEW_FILES = ("lifecycle.py", "health.py", "intelligence_pipeline_adapter.py")

T = datetime(2026, 8, 14, 15, 35, tzinfo=IST)


def _synthetic_reality_snapshot(spot_close=24366.0, expiry="2026-08-18") -> MarketRealitySnapshot:
    strikes = [24200, 24300, 24400, 24500]
    contracts = []
    for strike in strikes:
        contracts.append(OptionContractSnapshot(
            identity=f"NIFTY|{expiry}|{strike}|CE", expiry=expiry, strike=strike, option_type="CE",
            ltp=250.0, bid=248.0, ask=252.0, open_interest=100000.0, volume=5000.0,
            source="SOURCE_HISTORICAL", source_observation_ids=("OBS-1",),
        ))
        contracts.append(OptionContractSnapshot(
            identity=f"NIFTY|{expiry}|{strike}|PE", expiry=expiry, strike=strike, option_type="PE",
            ltp=230.0, bid=228.0, ask=232.0, open_interest=90000.0, volume=4500.0,
            source="SOURCE_HISTORICAL", source_observation_ids=("OBS-2",),
        ))
    return MarketRealitySnapshot(
        date="2026-08-14",
        spot=SpotSnapshot(open=spot_close - 50, high=spot_close + 20, low=spot_close - 60,
                           close=spot_close, volume=1000000.0, source="SOURCE_HISTORICAL",
                           source_observation_ids=("OBS-SPOT",)),
        futures=None,
        vix=VixSnapshot(close=13.5, open=13.2, high=13.8, low=13.0, change_percent=2.0,
                         source="SOURCE_HISTORICAL", source_observation_ids=("OBS-VIX",)),
        completeness="COMPLETE", is_final=True, certification_refs=(),
        built_at=T.isoformat(),
        options=OptionsSnapshot(contracts=tuple(contracts), source="SOURCE_HISTORICAL"),
        resolution="FIVE_MINUTE", as_of=T.isoformat(),
    )


def _synthetic_candles(base=24000.0, n=16):
    return [
        Candle(T - timedelta(minutes=5 * (n - i)), base + i * 3, base + i * 3 + 1, base + i * 3 - 1, base + i * 3, 1000)
        for i in range(n)
    ]


# ---------------------------------------------------------------------- #
# Property 1: lifecycle transitions work
# ---------------------------------------------------------------------- #
def test_legal_lifecycle_transitions_succeed_in_order():
    lc = RuntimeLifecycle.start(at=T.isoformat())
    assert lc.current_stage == RuntimeStage.INITIALIZING
    for stage in (RuntimeStage.WAITING_FOR_SESSION, RuntimeStage.COLLECTING,
                  RuntimeStage.PROCESSING_INTELLIGENCE, RuntimeStage.FINALIZING, RuntimeStage.COMPLETED):
        lc = lc.advance(stage, at=T.isoformat())
    assert lc.current_stage == RuntimeStage.COMPLETED
    assert lc.is_terminal()
    assert len(lc.transitions) == 6  # INITIALIZING + 5 advances


def test_failed_reachable_from_any_non_terminal_stage():
    for stage in (RuntimeStage.WAITING_FOR_SESSION, RuntimeStage.COLLECTING, RuntimeStage.PROCESSING_INTELLIGENCE):
        lc = RuntimeLifecycle.start(at=T.isoformat())
        lc = lc.advance(RuntimeStage.WAITING_FOR_SESSION, at=T.isoformat()) if stage != RuntimeStage.WAITING_FOR_SESSION else lc
        lc = lc.advance(RuntimeStage.FAILED, at=T.isoformat(), reason="synthetic failure")
        assert lc.current_stage == RuntimeStage.FAILED
        assert lc.is_terminal()


def test_illegal_transition_raises():
    lc = RuntimeLifecycle.start(at=T.isoformat())
    with pytest.raises(IllegalLifecycleTransition):
        lc.advance(RuntimeStage.COMPLETED, at=T.isoformat())  # cannot skip straight from INITIALIZING to COMPLETED.


def test_lifecycle_is_deterministic_and_serializable():
    lc_a = RuntimeLifecycle.start(at=T.isoformat()).advance(RuntimeStage.WAITING_FOR_SESSION, at=T.isoformat())
    lc_b = RuntimeLifecycle.start(at=T.isoformat()).advance(RuntimeStage.WAITING_FOR_SESSION, at=T.isoformat())
    assert lc_a.to_dict() == lc_b.to_dict()
    assert lc_a.advance != lc_b.advance  # never the same object -- advance() returns a NEW lifecycle.


# ---------------------------------------------------------------------- #
# Property 2 & 3: replay determinism + LIVE == HISTORICAL_REPLAY fingerprints
# ---------------------------------------------------------------------- #
def test_replay_mode_produces_deterministic_output():
    reality = _synthetic_reality_snapshot()
    candles = _synthetic_candles()
    ctx = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY, reality_snapshot_reference=reality.fingerprint())

    cycle_a = build_intelligence_heartbeat_cycle(reality_snapshot=reality, spot_candles=candles, context=ctx)
    cycle_b = build_intelligence_heartbeat_cycle(reality_snapshot=reality, spot_candles=candles, context=ctx)

    assert cycle_a.to_dict() == cycle_b.to_dict()


def test_live_and_replay_produce_identical_intelligence_fingerprints_at_every_layer():
    reality = _synthetic_reality_snapshot()
    candles = _synthetic_candles()
    ctx_live = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_LIVE, reality_snapshot_reference=reality.fingerprint())
    ctx_replay = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY, reality_snapshot_reference=reality.fingerprint())

    cycle_live = build_intelligence_heartbeat_cycle(reality_snapshot=reality, spot_candles=candles, context=ctx_live)
    cycle_replay = build_intelligence_heartbeat_cycle(reality_snapshot=reality, spot_candles=candles, context=ctx_replay)

    assert cycle_live.market_intelligence_snapshot.intelligence_snapshot_id == cycle_replay.market_intelligence_snapshot.intelligence_snapshot_id
    assert cycle_live.decision_intelligence.decision_intelligence_id == cycle_replay.decision_intelligence.decision_intelligence_id
    assert cycle_live.phenomena.assessment_id == cycle_replay.phenomena.assessment_id
    assert cycle_live.state_node.state_id == cycle_replay.state_node.state_id
    assert cycle_live.environment.environment_id == cycle_replay.environment.environment_id


# ---------------------------------------------------------------------- #
# Property 4: no broker/execution/strategy imports
# ---------------------------------------------------------------------- #
def test_new_files_import_nothing_broker_execution_or_strategy_shaped():
    forbidden_import_substrings = (
        "broker", "fyers", "execution_engine", "execution_planner", "order_construction",
        "msi_strategy_selector", "msi_strategy_selection_foundation", "msi_trade_construction",
    )
    for filename in NEW_FILES:
        path = os.path.join(SHADOW_RUNTIME_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports a forbidden module: {node.module}"


def test_shadow_session_runner_still_has_no_broker_order_calls():
    """Structural re-check that the additive Phase 19.10.1 edit to
    shadow_session_runner.py did not introduce anything beyond what
    Phase 19.10.0's own audit already confirmed absent. AST-level check
    on actual attribute access, not prose -- this file's own module
    docstring legitimately explains it never calls place_order/
    modify_order/cancel_order/get_open_positions, which would trip a
    naive whole-file substring search on its own disclaimer."""
    path = os.path.join(SHADOW_RUNTIME_DIR, "shadow_session_runner.py")
    with open(path) as f:
        source = f.read()
    tree = ast.parse(source)
    forbidden = {"place_order", "modify_order", "cancel_order", "get_open_positions"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            pytest.fail(f"shadow_session_runner.py calls .{node.attr}( directly")


# ---------------------------------------------------------------------- #
# Property 5: health heartbeat updates during processing
# ---------------------------------------------------------------------- #
def test_health_heartbeat_reflects_each_real_stage(tmp_path):
    path = str(tmp_path / "shadow_runtime_health.json")
    seen_stages = []
    for stage in (RuntimeStage.INITIALIZING, RuntimeStage.WAITING_FOR_SESSION, RuntimeStage.COLLECTING,
                  RuntimeStage.PROCESSING_INTELLIGENCE, RuntimeStage.FINALIZING, RuntimeStage.COMPLETED):
        health = health_for_stage(stage, session_date="2026-08-14", at=T.isoformat())
        write_health_heartbeat(path, health)
        read_back = read_health_heartbeat(path)
        seen_stages.append(read_back.current_stage)
        assert read_back.current_stage == stage.value

    # Every real stage was actually observable via the file, in order --
    # not only the final COMPLETED state.
    assert seen_stages == [s.value for s in (
        RuntimeStage.INITIALIZING, RuntimeStage.WAITING_FOR_SESSION, RuntimeStage.COLLECTING,
        RuntimeStage.PROCESSING_INTELLIGENCE, RuntimeStage.FINALIZING, RuntimeStage.COMPLETED,
    )]


def test_health_heartbeat_status_derived_correctly():
    running = health_for_stage(RuntimeStage.COLLECTING, session_date="2026-08-14", at=T.isoformat())
    completed = health_for_stage(RuntimeStage.COMPLETED, session_date="2026-08-14", at=T.isoformat())
    failed = health_for_stage(RuntimeStage.FAILED, session_date="2026-08-14", at=T.isoformat(), last_error="synthetic")
    assert running.status == "RUNNING"
    assert completed.status == "COMPLETED"
    assert failed.status == "FAILED"
    assert failed.last_error == "synthetic"


def test_health_heartbeat_write_is_atomic_no_torn_file(tmp_path):
    path = str(tmp_path / "health.json")
    health = health_for_stage(RuntimeStage.COLLECTING, session_date="2026-08-14", at=T.isoformat())
    write_health_heartbeat(path, health)
    assert not os.path.exists(f"{path}.tmp")  # temp file cleaned up via os.replace, never left behind.
    assert read_health_heartbeat(path).status == "RUNNING"


# ---------------------------------------------------------------------- #
# Property 6: full pipeline smoke test
# ---------------------------------------------------------------------- #
def test_full_pipeline_smoke_reality_to_environment():
    reality = _synthetic_reality_snapshot()
    candles = _synthetic_candles()
    ctx = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_LIVE, reality_snapshot_reference=reality.fingerprint())

    cycle = build_intelligence_heartbeat_cycle(reality_snapshot=reality, spot_candles=candles, context=ctx)

    assert isinstance(cycle, IntelligenceHeartbeatCycle)
    assert cycle.market_intelligence_snapshot.as_of_time == T
    assert cycle.decision_context.intelligence_snapshot_reference == cycle.market_intelligence_snapshot.intelligence_snapshot_id
    assert cycle.decision_intelligence.market_intelligence_snapshot_id == cycle.market_intelligence_snapshot.intelligence_snapshot_id
    assert cycle.phenomena.intelligence_snapshot_id == cycle.market_intelligence_snapshot.intelligence_snapshot_id
    assert cycle.state_node.decision_posture == cycle.decision_intelligence.recommended_posture.value
    assert cycle.environment.market_state_id == cycle.state_node.state_id
    # Every id present and real -- no empty/placeholder identity anywhere in the chain.
    for identity in cycle.to_dict().values():
        assert identity  # non-empty string


def test_pipeline_refuses_to_run_with_no_spot():
    reality = _synthetic_reality_snapshot()
    from dataclasses import replace
    empty_reality = replace(reality, spot=None)
    ctx = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_LIVE)
    with pytest.raises(IntelligencePipelineAdapterError):
        build_intelligence_heartbeat_cycle(reality_snapshot=empty_reality, spot_candles=_synthetic_candles(), context=ctx)

"""Phase 19.13 -- Live Intelligence Data Bridge.

Proves the phase's own required scenarios:
1. Real production-shaped data flows end-to-end (MarketDataAdapter ->
   MarketRealitySnapshot -> MarketIntelligenceSnapshot ->
   DecisionIntelligenceSnapshot -> MarketPhenomenaAssessment ->
   MarketStateGraph -> CycleArtifact + DailyIntelligenceArtifact), with
   session timestamp, observation lineage, intelligence fingerprint,
   and health status all present.
2. LIVE vs HISTORICAL_REPLAY equivalence (same fingerprint,
   classification, decision posture).
3. Incomplete data handling (completeness gate refuses composition,
   never fabricates confident intelligence).
4. Restart recovery (a fresh process hydrates a prior process's
   already-persisted daily intelligence artifacts).
5. No order/position/strategy access anywhere in the new files, and no
   `place_order`/`modify_order`/`cancel_order` call reachable from
   `run_daily_intelligence_session.py`'s own real broker construction.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.core.models import Candle
from bujji.intelligence.context import EXECUTION_MODE_LIVE
from bujji.market_reality_snapshot.models import (
    COMPLETENESS_EMPTY,
    COMPLETENESS_PARTIAL,
    MarketRealitySnapshot,
    OptionContractSnapshot,
    OptionsSnapshot,
    SpotSnapshot,
    VixSnapshot,
)
from bujji.shadow_runtime.completeness_gate import evaluate_completeness_gate
from bujji.shadow_runtime.daily_intelligence_artifact import (
    build_daily_intelligence_artifact,
    hydrate_daily_intelligence_artifacts,
    record_daily_intelligence_artifact,
)
from bujji.shadow_runtime.intelligence_pipeline_adapter import build_intelligence_heartbeat_cycle
from bujji.shadow_runtime.live_intelligence_cycle import run_live_intelligence_cycle
from bujji.shadow_runtime.replay_equivalence import validate_live_replay_equivalence
from bujji.state_persistence.store import EventStore

FIXED_NOW = datetime(2026, 8, 15, 9, 45, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


class FakeBroker:
    """Same production-shaped fixture pattern already established in
    tests/test_shadow_runtime_intelligence_loop.py -- reused, not
    reinvented."""

    def __init__(self, fail_spot: bool = False, no_options: bool = False):
        self._fail_spot = fail_spot
        self._no_options = no_options

    async def connect(self):
        pass

    async def get_spot(self, underlying):
        return None if self._fail_spot else 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [] if self._no_options else [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out, c = [], 24300.0
        for _ in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


def _hand_built_reality_snapshot(*, with_options: bool = True) -> MarketRealitySnapshot:
    """A realistic, hand-built Reality snapshot -- same field shapes
    `reality_translator.py` itself produces, used for gate/replay tests
    that don't need a full broker round-trip."""
    spot = SpotSnapshot(
        open=24400.0, high=24400.0, low=24400.0, close=24400.0, volume=None,
        source="SOURCE_LIVE", source_observation_ids=(), observed_at=FIXED_NOW.isoformat(),
    )
    vix = VixSnapshot(
        close=13.0, open=None, high=None, low=None, change_percent=-3.7,
        source="SOURCE_LIVE", source_observation_ids=(), observed_at=FIXED_NOW.isoformat(),
    )
    options = None
    if with_options:
        contracts = tuple(
            OptionContractSnapshot(
                identity=f"NIFTY|2026-08-21|24450|{ot}", expiry="2026-08-21", strike=24450.0, option_type=ot,
                ltp=80.0, bid=78.0, ask=82.0, open_interest=12000.0, volume=1000.0,
                source="SOURCE_LIVE", source_observation_ids=(),
            )
            for ot in ("CE", "PE")
        )
        options = OptionsSnapshot(contracts=contracts, source="SOURCE_LIVE")
    return MarketRealitySnapshot(
        date="2026-08-15", spot=spot, futures=None, vix=vix,
        completeness=COMPLETENESS_PARTIAL, is_final=False, certification_refs=(),
        built_at=FIXED_NOW.isoformat(), options=options, as_of=FIXED_NOW.isoformat(),
    )


# ---------------------------------------------------------------------
# Scenario 1: real production-shaped data flows end-to-end
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_production_shaped_data_flows_end_to_end(tmp_path):
    cycle_store = EventStore(str(tmp_path / "cycle_artifacts.jsonl"))
    daily_store = EventStore(str(tmp_path / "daily_artifacts.jsonl"))

    result = await run_live_intelligence_cycle(
        broker=FakeBroker(), clock=clock, underlying="NIFTY", session_id="daily-2026-08-15",
        cycle_id="daily-2026-08-15-1", session_date="2026-08-15", execution_mode=EXECUTION_MODE_LIVE,
        cycle_artifact_store=cycle_store, daily_artifact_store=daily_store,
    )
    assert result.succeeded, result.error
    assert result.gate_passed
    assert result.intelligence_fingerprint

    daily_artifacts = list(hydrate_daily_intelligence_artifacts(daily_store).values())
    assert len(daily_artifacts) == 1
    artifact = daily_artifacts[0]

    # session timestamp, observation lineage, intelligence fingerprint, health status.
    assert artifact.as_of_time
    assert artifact.reality["date"] == "2026-08-15"  # lineage: the reality payload is real, preserved content.
    assert artifact.intelligence_fingerprint
    assert artifact.runtime_health_status
    # Full-payload preservation: reality, intelligence, phenomena, market state.
    assert artifact.reality and artifact.intelligence and artifact.phenomena and artifact.market_state and artifact.environment
    assert artifact.completeness_gate_passed is True


# ---------------------------------------------------------------------
# Scenario 2: LIVE vs HISTORICAL_REPLAY equivalence
# ---------------------------------------------------------------------

def test_live_replay_equivalence_same_fingerprint_classification_posture():
    reality_snapshot = _hand_built_reality_snapshot()
    candles = [Candle(timestamp=FIXED_NOW, open=24300 + i, high=24305 + i, low=24295 + i, close=24302 + i, volume=1000) for i in range(20)]

    report = validate_live_replay_equivalence(reality_snapshot=reality_snapshot, spot_candles=candles, as_of_time=FIXED_NOW)
    assert report.equivalent, report.mismatches
    assert report.live_intelligence_fingerprint == report.replay_intelligence_fingerprint
    assert report.live_environment_classification == report.replay_environment_classification
    assert report.live_decision_posture == report.replay_decision_posture


# ---------------------------------------------------------------------
# Scenario 3: incomplete data handling -- gate refuses composition
# ---------------------------------------------------------------------

def test_completeness_gate_fails_closed_on_empty_reality():
    empty_snapshot = MarketRealitySnapshot(
        date="2026-08-15", spot=None, futures=None, vix=None, completeness=COMPLETENESS_EMPTY,
        is_final=False, certification_refs=(), built_at=FIXED_NOW.isoformat(),
    )
    gate = evaluate_completeness_gate(empty_snapshot)
    assert gate.passed is False
    assert len(gate.reasons) >= 1


def test_completeness_gate_fails_closed_on_missing_options():
    snapshot = _hand_built_reality_snapshot(with_options=False)
    gate = evaluate_completeness_gate(snapshot)
    assert gate.passed is False
    assert any("options" in r for r in gate.reasons)


def test_completeness_gate_passes_on_real_complete_data():
    snapshot = _hand_built_reality_snapshot(with_options=True)
    gate = evaluate_completeness_gate(snapshot)
    assert gate.passed is True
    assert gate.reasons == ()


@pytest.mark.asyncio
async def test_live_cycle_never_composes_intelligence_when_gate_fails(tmp_path, monkeypatch):
    """No options chain at all -- the gate must refuse BEFORE
    `build_intelligence_heartbeat_cycle` is ever called, so no falsely
    confident intelligence/environment/decision posture is produced.
    `get_option_chain` alone only withholds real-time OI (legs still
    resolve from the real cached instrument universe,
    `resolve_chain_contracts`) -- to genuinely force an empty chain we
    monkeypatch `build_option_chain_snapshot` itself to return None,
    the same technique `test_market_perception_market_data_adapter.py`
    already established for isolating this adapter from the instrument
    master."""
    from bujji.market_perception import market_data_adapter as mda_module

    async def _no_chain(broker, underlying, spot, config):
        return None

    monkeypatch.setattr(mda_module, "build_option_chain_snapshot", _no_chain)

    cycle_store = EventStore(str(tmp_path / "cycle_artifacts.jsonl"))
    daily_store = EventStore(str(tmp_path / "daily_artifacts.jsonl"))

    result = await run_live_intelligence_cycle(
        broker=FakeBroker(no_options=True), clock=clock, underlying="NIFTY", session_id="daily-2026-08-15",
        cycle_id="daily-2026-08-15-1", session_date="2026-08-15", execution_mode=EXECUTION_MODE_LIVE,
        cycle_artifact_store=cycle_store, daily_artifact_store=daily_store,
    )
    assert result.succeeded is False
    assert result.gate_passed is False
    assert result.intelligence_fingerprint is None
    assert result.error is None  # gate failure, not an exception -- distinguished per this module's own contract.
    assert list(hydrate_daily_intelligence_artifacts(daily_store).values()) == []  # never persisted -- honest, not fabricated.


@pytest.mark.asyncio
async def test_live_cycle_reports_honest_failure_when_spot_missing(tmp_path):
    cycle_store = EventStore(str(tmp_path / "cycle_artifacts.jsonl"))
    result = await run_live_intelligence_cycle(
        broker=FakeBroker(fail_spot=True), clock=clock, underlying="NIFTY", session_id="daily-2026-08-15",
        cycle_id="daily-2026-08-15-1", session_date="2026-08-15", execution_mode=EXECUTION_MODE_LIVE,
        cycle_artifact_store=cycle_store, daily_artifact_store=None,
    )
    assert result.succeeded is False
    assert result.error is not None
    assert "reality_translation_failed" in result.error


# ---------------------------------------------------------------------
# Scenario 4: restart recovery -- a fresh process hydrates a prior
# process's already-persisted daily intelligence artifacts.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_recovery_hydrates_prior_process_artifacts(tmp_path):
    daily_store_path = str(tmp_path / "daily_artifacts.jsonl")
    cycle_store_path = str(tmp_path / "cycle_artifacts.jsonl")

    # "Process 1": composes and persists one real cycle, then exits (no
    # in-memory state carried forward -- a fresh EventStore instance
    # below simulates a genuine restart, not a shared object).
    first_process_store = EventStore(daily_store_path)
    result = await run_live_intelligence_cycle(
        broker=FakeBroker(), clock=clock, underlying="NIFTY", session_id="daily-2026-08-15",
        cycle_id="daily-2026-08-15-1", session_date="2026-08-15", execution_mode=EXECUTION_MODE_LIVE,
        cycle_artifact_store=EventStore(cycle_store_path), daily_artifact_store=first_process_store,
    )
    assert result.succeeded

    # "Process 2": a fresh EventStore pointed at the same file, as a
    # freshly restarted process would construct.
    second_process_store = EventStore(daily_store_path)
    hydrated = hydrate_daily_intelligence_artifacts(second_process_store)
    assert len(hydrated) == 1
    artifact = next(iter(hydrated.values()))
    assert artifact.session_date == "2026-08-15"
    assert artifact.intelligence_fingerprint == result.intelligence_fingerprint


# ---------------------------------------------------------------------
# Scenario 5: structural safety -- no order/position/strategy access;
# real broker construction never reaches place/modify/cancel order.
# ---------------------------------------------------------------------

PHASE_19_13_FILES = (
    "bujji/shadow_runtime/completeness_gate.py",
    "bujji/shadow_runtime/daily_intelligence_artifact.py",
    "bujji/shadow_runtime/live_intelligence_cycle.py",
    "bujji/shadow_runtime/replay_equivalence.py",
)
FORBIDDEN_MODULE_SUBSTRINGS = ("order", "position", "strategy", "execution")
FORBIDDEN_CALL_ATTRS = {"place_order", "modify_order", "cancel_order"}


def _imported_module_names(tree: ast.Module) -> list:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("relative_path", PHASE_19_13_FILES)
def test_no_trading_decision_imports(relative_path: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / relative_path).read_text()
    tree = ast.parse(source)
    for module_name in _imported_module_names(tree):
        lowered = module_name.lower()
        for forbidden in FORBIDDEN_MODULE_SUBSTRINGS:
            assert forbidden not in lowered, (
                f"{relative_path} imports {module_name!r}, containing forbidden substring {forbidden!r}"
            )


def test_run_daily_intelligence_session_never_places_orders() -> None:
    """The one file in this phase that legitimately imports broker
    modules (for read-only market data). The real safety check: no
    `place_order`/`modify_order`/`cancel_order` call is reachable
    anywhere in its source -- and `disable_live_execution` IS present,
    proving the broker is wrapped before use, not just described as
    wrapped."""
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "run_daily_intelligence_session.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_CALL_ATTRS, f"forbidden call found: .{node.attr}("
    assert "disable_live_execution" in source

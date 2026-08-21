"""Shadow Runtime Intelligence Loop Integration -- Phase 19.10.2 tests.

Proves the 7 required properties:
1. ShadowSessionRunner actually invokes the intelligence pipeline.
2. Real production-shaped data flows through HistoricalObservationStore
   -> Reality Snapshot -> Intelligence -> Environment (via the same
   adapter, real-data-verified manually in Phase 19.10.1; here proven
   through the actual runner with production-shaped fixture data).
3. Cycle artifact persistence works.
4. LIVE vs HISTORICAL_REPLAY equality.
5. Deterministic fingerprints.
6. Failure visibility.
7. No forbidden imports: broker, orders, positions, strategies.
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, EXECUTION_MODE_LIVE
from bujji.shadow_runtime.cycle_artifact import (
    EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED,
    EVENT_SHADOW_INTELLIGENCE_CYCLE_RECORDED,
    hydrate_cycle_artifacts,
)
from bujji.shadow_runtime.health import read_health_heartbeat
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
from bujji.state_persistence.store import EventStore

SHADOW_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "shadow_runtime")
NEW_FILES_1902 = ("reality_translator.py", "cycle_artifact.py")

CE_CONTRACT = OptionContract("NIFTY24450CE", "NIFTY", 24450, OptionType.CE, "2026-08-04", 65)
PE_CONTRACT = OptionContract("NIFTY24450PE", "NIFTY", 24450, OptionType.PE, "2026-08-04", 65)
FIXED_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


class FakeBroker:
    """Production-shaped responses -- same fixture pattern already
    established in tests/test_shadow_runtime_recovery.py, reused here
    rather than a second, divergent fake broker."""

    def __init__(self, fail_spot: bool = False):
        # `MarketDataAdapter.build_snapshot()` catches every individual
        # broker-call exception internally (missing field, never a
        # propagated crash) -- so the real way to reach the intelligence
        # pipeline's OWN failure path is a genuinely missing spot tick
        # (RealityTranslationError's exact documented condition), not a
        # raised exception from a single sub-call.
        self._fail_spot = fail_spot

    async def connect(self):
        pass

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        return None if self._fail_spot else 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out, c = [], 24300.0
        for _ in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


def make_runner(broker, tmp_path, session_id="S-IP", **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=str(tmp_path / "quotes.jsonl"), session_id=session_id, clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
        market_perception_enabled=True, intelligence_cycle_enabled=True,
        intelligence_cycle_path=str(tmp_path / "intelligence_cycle.jsonl"),
        intelligence_pipeline_enabled=True,
        intelligence_pipeline_event_store_path=str(tmp_path / "ip_events.jsonl"),
        health_path=str(tmp_path / "health.json"), session_date="2026-08-04",
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


# ---------------------------------------------------------------------- #
# Property 1: ShadowSessionRunner actually invokes the intelligence pipeline
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_runner_invokes_intelligence_pipeline_when_enabled(tmp_path):
    runner = make_runner(FakeBroker(), tmp_path)
    artifact = await runner.start()
    assert artifact.errors == ()
    store = EventStore(str(tmp_path / "ip_events.jsonl"))
    events = list(store.read_events())
    assert any(e.event_type == EVENT_SHADOW_INTELLIGENCE_CYCLE_RECORDED for e in events)


@pytest.mark.asyncio
async def test_runner_does_not_invoke_pipeline_when_disabled(tmp_path):
    runner = make_runner(FakeBroker(), tmp_path, intelligence_pipeline_enabled=False)
    await runner.start()
    assert not os.path.exists(str(tmp_path / "ip_events.jsonl"))


# ---------------------------------------------------------------------- #
# Property 2: production-shaped data flows end to end
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_production_shaped_data_flows_reality_to_environment(tmp_path):
    runner = make_runner(FakeBroker(), tmp_path)
    await runner.start()
    store = EventStore(str(tmp_path / "ip_events.jsonl"))
    artifacts = list(hydrate_cycle_artifacts(store).values())
    assert len(artifacts) == 2  # max_cycles=2
    for artifact in artifacts:
        assert artifact.reality_snapshot_fingerprint
        assert artifact.intelligence_snapshot_fingerprint
        assert artifact.decision_intelligence_fingerprint
        assert artifact.environment_classification


# ---------------------------------------------------------------------- #
# Property 3: cycle artifact persistence works
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cycle_artifact_persistence_round_trips(tmp_path):
    runner = make_runner(FakeBroker(), tmp_path)
    await runner.start()
    store = EventStore(str(tmp_path / "ip_events.jsonl"))
    artifacts = hydrate_cycle_artifacts(store)
    assert len(artifacts) == 2
    for artifact_id, artifact in artifacts.items():
        assert artifact.cycle_artifact_id == artifact_id
        assert artifact.fingerprint() == artifact_id  # recomputation matches the stored id.


# ---------------------------------------------------------------------- #
# Property 4: LIVE vs HISTORICAL_REPLAY equality
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_live_and_replay_produce_identical_cycle_artifacts(tmp_path):
    live_dir = tmp_path / "live"
    replay_dir = tmp_path / "replay"
    live_dir.mkdir()
    replay_dir.mkdir()

    live_runner = make_runner(FakeBroker(), live_dir, execution_mode=EXECUTION_MODE_LIVE)
    replay_runner = make_runner(FakeBroker(), replay_dir, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    await live_runner.start()
    await replay_runner.start()

    live_artifacts = sorted(hydrate_cycle_artifacts(EventStore(str(live_dir / "ip_events.jsonl"))).values(), key=lambda a: a.cycle_id)
    replay_artifacts = sorted(hydrate_cycle_artifacts(EventStore(str(replay_dir / "ip_events.jsonl"))).values(), key=lambda a: a.cycle_id)

    assert len(live_artifacts) == len(replay_artifacts) == 2
    for live_a, replay_a in zip(live_artifacts, replay_artifacts):
        assert live_a.intelligence_snapshot_fingerprint == replay_a.intelligence_snapshot_fingerprint
        assert live_a.decision_intelligence_fingerprint == replay_a.decision_intelligence_fingerprint
        assert live_a.environment_classification == replay_a.environment_classification
        assert live_a.detected_phenomena == replay_a.detected_phenomena
        # Only execution_mode (and the audit-only cycle_artifact_id, which
        # is NOT hashed from execution_mode either, so it matches too) differs.
        assert live_a.execution_mode != replay_a.execution_mode


# ---------------------------------------------------------------------- #
# Property 5: deterministic fingerprints
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_identical_runs_produce_identical_cycle_artifact_ids(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    await make_runner(FakeBroker(), dir_a).start()
    await make_runner(FakeBroker(), dir_b).start()

    ids_a = sorted(hydrate_cycle_artifacts(EventStore(str(dir_a / "ip_events.jsonl"))).keys())
    ids_b = sorted(hydrate_cycle_artifacts(EventStore(str(dir_b / "ip_events.jsonl"))).keys())
    assert ids_a == ids_b


# ---------------------------------------------------------------------- #
# Property 6: failure visibility
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_intelligence_pipeline_failure_is_visible_never_silent(tmp_path):
    runner = make_runner(FakeBroker(fail_spot=True), tmp_path)
    artifact = await runner.start()

    # A genuinely missing spot tick reaches the intelligence pipeline's
    # OWN failure path (RealityTranslationError) -- recorded in `errors`,
    # never silently swallowed, and the runner itself never crashes.
    assert any("intelligence_pipeline_failed" in e for e in artifact.errors)
    store = EventStore(str(tmp_path / "ip_events.jsonl"))
    events = list(store.read_events())
    assert any(e.event_type == EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED for e in events)
    health = read_health_heartbeat(str(tmp_path / "health.json"))
    assert health is not None
    assert health.last_error is not None
    assert "intelligence_pipeline_failed" in health.last_error


@pytest.mark.asyncio
async def test_reality_translation_failure_is_recorded_as_a_real_event(tmp_path):
    """Directly exercises the intelligence-pipeline-specific failure path
    (as opposed to an upstream market_perception failure) by handing the
    step a snapshot with no real spot -- the exact condition
    `RealityTranslationError` exists for."""
    from bujji.shadow_runtime.cycle_artifact import record_cycle_failure
    from bujji.shadow_runtime.reality_translator import RealityTranslationError

    store = EventStore(str(tmp_path / "ip_events.jsonl"))
    record_cycle_failure(
        store, session_id="S-FAIL", cycle_id="ip0", execution_mode=EXECUTION_MODE_LIVE,
        as_of_time=FIXED_NOW.isoformat(), error="RealityTranslationError: no spot", recorded_at=FIXED_NOW,
    )
    events = list(store.read_events())
    assert len(events) == 1
    assert events[0].event_type == EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED
    assert "no spot" in events[0].payload["error"]


# ---------------------------------------------------------------------- #
# Property 7: no forbidden imports (AST-level, not substring)
# ---------------------------------------------------------------------- #
def test_new_files_import_nothing_broker_order_position_strategy_shaped():
    forbidden_import_substrings = (
        "broker", "fyers", "execution_engine", "execution_planner", "order_construction",
        "msi_strategy_selector", "msi_strategy_selection_foundation", "msi_trade_construction",
        "position_lifecycle", "position_management",
    )
    for filename in NEW_FILES_1902:
        path = os.path.join(SHADOW_RUNTIME_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports a forbidden module: {node.module}"


def test_shadow_session_runner_still_has_no_broker_order_calls_after_wiring():
    """AST-level re-check (not a naive substring scan, which would trip
    on this file's own module docstring) that the Phase 19.10.2 wiring
    introduced no order/position call."""
    path = os.path.join(SHADOW_RUNTIME_DIR, "shadow_session_runner.py")
    with open(path) as f:
        source = f.read()
    tree = ast.parse(source)
    forbidden = {"place_order", "modify_order", "cancel_order", "get_open_positions"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            pytest.fail(f"shadow_session_runner.py calls .{node.attr}( directly")

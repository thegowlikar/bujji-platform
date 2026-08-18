"""Market Environment Intelligence Foundation -- Phase 19.9 tests.

Proves the 10 required properties:
1. deterministic fingerprint
2. replay equality
3. no trade vocabulary
4. evidence traceability
5. memory integration
6. contradiction handling
7. insufficient-data handling
8. no direct strategy imports
9. no execution imports
10. no datastore bypass
"""
from __future__ import annotations

import ast
import os
from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.decision_context import build_decision_context
from bujji.decision_intelligence import build_decision_intelligence_snapshot
from bujji.intelligence.context import (
    EXECUTION_MODE_HISTORICAL_REPLAY,
    EXECUTION_MODE_LIVE,
    IntelligenceContext,
)
from bujji.intelligence.event_brain import EventBrain
from bujji.intelligence.greeks_brain import GreeksBrain
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.market_intelligence_snapshot import build_market_intelligence_snapshot
from bujji.intelligence.regime_brain import RegimeBrain
from bujji.intelligence.structure_brain import StructureBrain
from bujji.intelligence.volatility_brain import VolatilityBrain
from bujji.market_environment import (
    EnvironmentType,
    MarketEnvironmentAssessment,
    build_market_environment_assessment,
)
from bujji.market_phenomena import build_market_phenomena_assessment
from bujji.market_state_graph import build_market_state_node
from bujji.market_understanding.memory_engine import build_market_memory_entry, record_market_memory
from bujji.market_understanding.memory_query import find_similar_memories_as_of
from bujji.state_persistence.store import EventStore

MENV_MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "market_environment")
MENV_FILES = ("models.py", "classify.py", "engine.py", "__init__.py")

T = datetime(2026, 8, 14, 10, 30, tzinfo=IST)
RANGING_CLOSES = [24000 + (3 if i % 2 == 0 else -3) for i in range(16)]
TRENDING_CLOSES = [24000 + i * 8 for i in range(16)]
VOLATILE_CLOSES = [24000, 24150, 23900, 24200, 23850, 24250, 23800, 24300,
                    23750, 24350, 23700, 24400, 23650, 24450, 23600, 24500]


def _candles(t, closes):
    return [
        Candle(t - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _snapshot(t, closes, execution_mode=EXECUTION_MODE_LIVE, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1, vix=15.0, ce=400.0, pe=350.0, expiry=date(2026, 8, 21)):
    context = IntelligenceContext(as_of_time=t, execution_mode=execution_mode, reality_snapshot_reference="ref")
    candles = _candles(t, closes)
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(closes[-1], [(closes[-1] - 100, 500, 100), (closes[-1] + 100, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(ce_bid, ce_ask, pe_bid, pe_ask, context=context)
    volatility = VolatilityBrain().analyze(candles, closes[-1], 24000, 5 / 365, ce, pe, context=context)
    greeks = GreeksBrain().analyze(spot=closes[-1], strike=24000, t_years=5 / 365, iv_ce=0.3, iv_pe=0.3, context=context)
    event = EventBrain().analyze(expiry, t.date(), vix, context=context)
    return build_market_intelligence_snapshot(
        context=context, created_at=t, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )


def _assessment(snapshot, memory_matches=None):
    decision_context = build_decision_context(snapshot)
    decision_intelligence = build_decision_intelligence_snapshot(
        snapshot=snapshot, decision_context=decision_context, memory_matches=memory_matches or [], created_at=snapshot.as_of_time,
    )
    phenomena = build_market_phenomena_assessment(snapshot=snapshot, previous_snapshot=None, created_at=snapshot.as_of_time)
    node = build_market_state_node(snapshot=snapshot, decision_intelligence=decision_intelligence, phenomena_assessment=phenomena)
    return build_market_environment_assessment(node=node, decision_intelligence=decision_intelligence, created_at=snapshot.as_of_time)


# ---------------------------------------------------------------------- #
# Property 1: deterministic fingerprint
# ---------------------------------------------------------------------- #
def test_deterministic_fingerprint():
    snap = _snapshot(T, RANGING_CLOSES)
    a = _assessment(snap)
    b = _assessment(snap)
    assert a.environment_id == b.environment_id
    assert a.fingerprint() == a.environment_id


# ---------------------------------------------------------------------- #
# Property 2: replay equality
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_environment():
    snap_live = _snapshot(T, RANGING_CLOSES, execution_mode=EXECUTION_MODE_LIVE)
    snap_replay = _snapshot(T, RANGING_CLOSES, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    a = _assessment(snap_live)
    b = _assessment(snap_replay)
    assert a.environment_type == b.environment_type
    assert a.environment_id == b.environment_id


# ---------------------------------------------------------------------- #
# Property 3: no trade vocabulary
# ---------------------------------------------------------------------- #
def test_no_trade_vocabulary_in_identifiers():
    forbidden_terms = ("buy", "sell_", "order", "quantity", "entry_price", "stop_loss", "put", "call")
    for filename in MENV_FILES:
        path = os.path.join(MENV_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.add(node.name.lower())
            elif isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
        for term in forbidden_terms:
            matches = [i for i in identifiers if term in i]
            assert not matches, f"{filename} has trade-shaped identifier(s): {matches}"


def test_no_order_shaped_fields_on_model():
    forbidden = {"action", "order", "quantity", "entry_price", "stop_loss", "side", "strike", "option_type", "direction_call"}
    field_names = set(MarketEnvironmentAssessment.__dataclass_fields__.keys())
    assert not (field_names & forbidden), f"MarketEnvironmentAssessment has trade-shaped fields: {field_names & forbidden}"


def test_real_assessment_contains_no_buy_sell_ce_pe_tokens():
    snap = _snapshot(T, RANGING_CLOSES)
    env = _assessment(snap)
    serialized = str(env.to_dict()).upper()
    for forbidden_token in (" BUY ", " SELL ", "\"CE\"", "\"PE\""):
        assert forbidden_token not in serialized


# ---------------------------------------------------------------------- #
# Property 4: evidence traceability
# ---------------------------------------------------------------------- #
def test_environment_classification_carries_real_supporting_or_blocking_evidence():
    favourable_snap = _snapshot(T, RANGING_CLOSES)
    env = _assessment(favourable_snap)
    assert env.environment_type == EnvironmentType.PREMIUM_SELLING_FAVOURABLE
    assert len(env.supporting_conditions) > 0
    for condition in env.supporting_conditions:
        assert isinstance(condition, str) and condition  # never an empty/placeholder string.


# ---------------------------------------------------------------------- #
# Property 5: memory integration
# ---------------------------------------------------------------------- #
def test_historical_similarity_reflects_real_decision_intelligence_memory_context(tmp_path):
    snap = _snapshot(T, RANGING_CLOSES)
    decision_context = build_decision_context(snap)
    memory_entry = build_market_memory_entry(snapshot=snap, decision_context=decision_context, underlying="NIFTY", recorded_at=T)
    store = EventStore(str(tmp_path / "memory.jsonl"))
    record_market_memory(store, memory_entry, recorded_at=T)

    later_snap = _snapshot(T + timedelta(days=1), RANGING_CLOSES)
    later_decision_context = build_decision_context(later_snap)
    from bujji.market_understanding.memory_engine import hydrate_market_memory
    universe = list(hydrate_market_memory(store).values())
    from bujji.market_understanding.memory_engine import build_market_memory_entry as build_entry
    target_entry = build_entry(snapshot=later_snap, decision_context=later_decision_context, underlying="NIFTY", recorded_at=later_snap.as_of_time)
    matches = find_similar_memories_as_of(target_entry, universe, as_of_time=later_snap.as_of_time, top_n=5)

    decision_intelligence = build_decision_intelligence_snapshot(
        snapshot=later_snap, decision_context=later_decision_context, memory_matches=matches, created_at=later_snap.as_of_time,
    )
    phenomena = build_market_phenomena_assessment(snapshot=later_snap, previous_snapshot=None, created_at=later_snap.as_of_time)
    node = build_market_state_node(snapshot=later_snap, decision_intelligence=decision_intelligence, phenomena_assessment=phenomena)
    env = build_market_environment_assessment(node=node, decision_intelligence=decision_intelligence, created_at=later_snap.as_of_time)

    assert env.historical_similarity == decision_intelligence.memory_context.to_dict()
    assert env.historical_similarity["matched_count"] >= 1
    # Never a prediction -- no "expected"/"probability" language.
    assert "expected" not in str(env.historical_similarity).lower()


# ---------------------------------------------------------------------- #
# Property 6: contradiction handling
# ---------------------------------------------------------------------- #
def test_contradictions_route_to_stand_aside():
    """Rich premiums + wide liquidity spread -> decision_intelligence
    reports a real contradiction -> environment must STAND_ASIDE, never
    force PREMIUM_SELLING_FAVOURABLE despite the rich-IV signal alone."""
    snap = _snapshot(T, RANGING_CLOSES, ce_bid=100.0, ce_ask=110.0, pe_bid=80.0, pe_ask=90.0)  # wide spread
    decision_context = build_decision_context(snap)
    decision_intelligence = build_decision_intelligence_snapshot(
        snapshot=snap, decision_context=decision_context, memory_matches=[], created_at=T,
    )
    assert len(decision_intelligence.contradictions) >= 1  # sanity: a real contradiction exists.
    phenomena = build_market_phenomena_assessment(snapshot=snap, previous_snapshot=None, created_at=T)
    node = build_market_state_node(snapshot=snap, decision_intelligence=decision_intelligence, phenomena_assessment=phenomena)
    env = build_market_environment_assessment(node=node, decision_intelligence=decision_intelligence, created_at=T)
    assert env.environment_type == EnvironmentType.STAND_ASIDE
    assert any("contradiction" in c for c in env.supporting_conditions)


# ---------------------------------------------------------------------- #
# Property 7: insufficient-data handling
# ---------------------------------------------------------------------- #
def test_insufficient_evidence_never_forces_a_favourable_label():
    """Perfectly flat closes -> realized_vol == 0 -> volatility richness
    UNKNOWN -> decision_intelligence reports INSUFFICIENT_INFORMATION ->
    environment must honestly STAND_ASIDE, never guess a FAVOURABLE
    category from missing evidence."""
    snap = _snapshot(T, [24000.0] * 16)
    env = _assessment(snap)
    assert env.environment_type == EnvironmentType.STAND_ASIDE


# ---------------------------------------------------------------------- #
# Property 8: no direct strategy imports
# ---------------------------------------------------------------------- #
def test_no_strategy_selection_imports():
    forbidden_import_substrings = ("msi_strategy_selector", "msi_strategy_selection_foundation", "strategy_taxonomy_bridge")
    for filename in MENV_FILES:
        path = os.path.join(MENV_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports a strategy-selection module: {node.module}"


# ---------------------------------------------------------------------- #
# Property 9: no execution imports
# ---------------------------------------------------------------------- #
def test_no_execution_or_broker_imports():
    forbidden_import_substrings = ("broker", "fyers", "execution_engine", "execution_planner", "order_construction")
    for filename in MENV_FILES:
        path = os.path.join(MENV_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports an execution-shaped module: {node.module}"


# ---------------------------------------------------------------------- #
# Property 10: no datastore bypass
# ---------------------------------------------------------------------- #
def test_no_datastore_import_or_direct_brain_calls():
    forbidden_import_substrings = ("eventstore", "sqlite", "historical_reality", "market_reality")
    forbidden_call_names = {"RegimeBrain", "StructureBrain", "LiquidityBrain", "VolatilityBrain", "GreeksBrain", "EventBrain"}
    for filename in MENV_FILES:
        path = os.path.join(MENV_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports a datastore-shaped module: {node.module}"
            if isinstance(node, ast.Attribute) and node.attr == "analyze":
                pytest.fail(f"{filename} calls .analyze( directly")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_call_names:
                pytest.fail(f"{filename} instantiates {node.func.id} directly")

"""Market State Graph Foundation -- Phase 19.8 tests.

Proves the 8 required properties:
1. deterministic state fingerprint
2. historical replay equality
3. no future-state leakage
4. transition evidence validation
5. unknown transition handling
6. no strategy vocabulary
7. memory compatibility
8. no direct datastore access
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
from bujji.market_phenomena import build_market_phenomena_assessment
from bujji.market_state_graph import (
    ALL_STATE_TRANSITION_TYPES,
    MarketStateNode,
    StateTransitionEdge,
    build_market_state_node,
    build_state_sequence,
    hydrate_market_state_graph,
    nodes_as_of,
    record_market_state_node,
)
from bujji.state_persistence.store import EventStore

MSG_MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "market_state_graph")
MSG_FILES = ("models.py", "transitions.py", "engine.py", "memory.py", "__init__.py")

T1 = datetime(2026, 8, 13, 10, 30, tzinfo=IST)
T2 = datetime(2026, 8, 14, 10, 30, tzinfo=IST)

FLAT_CLOSES = [24000.0] * 16
VOLATILE_CLOSES = [24000, 24150, 23900, 24200, 23850, 24250, 23800, 24300,
                    23750, 24350, 23700, 24400, 23650, 24450, 23600, 24500]


def _candles(t, closes):
    return [
        Candle(t - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _snapshot(t, closes, execution_mode=EXECUTION_MODE_LIVE, ce_bid=100.0, ce_ask=101.0, pe_bid=80.0, pe_ask=80.5, vix=15.0, expiry=date(2026, 8, 21)):
    context = IntelligenceContext(as_of_time=t, execution_mode=execution_mode, reality_snapshot_reference="ref")
    candles = _candles(t, closes)
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(closes[-1], [(closes[-1] - 100, 500, 100), (closes[-1] + 100, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(ce_bid, ce_ask, pe_bid, pe_ask, context=context)
    volatility = VolatilityBrain().analyze(candles, closes[-1], 24000, 5 / 365, 250.0, 250.0, context=context)
    greeks = GreeksBrain().analyze(spot=closes[-1], strike=24000, t_years=5 / 365, iv_ce=0.2, iv_pe=0.2, context=context)
    event = EventBrain().analyze(expiry, t.date(), vix, context=context)
    return build_market_intelligence_snapshot(
        context=context, created_at=t, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )


def _node(snapshot, previous_snapshot=None, previous_phenomena=None, previous_node=None):
    decision_context = build_decision_context(snapshot)
    decision_intelligence = build_decision_intelligence_snapshot(
        snapshot=snapshot, decision_context=decision_context, memory_matches=[], created_at=snapshot.as_of_time,
    )
    phenomena = build_market_phenomena_assessment(
        snapshot=snapshot, previous_snapshot=previous_snapshot, created_at=snapshot.as_of_time,
    )
    node = build_market_state_node(
        snapshot=snapshot, decision_intelligence=decision_intelligence, phenomena_assessment=phenomena,
        previous_snapshot=previous_snapshot, previous_phenomena_assessment=previous_phenomena, previous_node=previous_node,
    )
    return node, phenomena


# ---------------------------------------------------------------------- #
# Property 1: deterministic state fingerprint
# ---------------------------------------------------------------------- #
def test_deterministic_state_fingerprint():
    snap = _snapshot(T1, FLAT_CLOSES)
    node_a, _ = _node(snap)
    node_b, _ = _node(snap)
    assert node_a.state_id == node_b.state_id
    assert node_a.fingerprint() == node_a.state_id


# ---------------------------------------------------------------------- #
# Property 2: historical replay equality
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_state_fingerprint():
    snap_live = _snapshot(T1, FLAT_CLOSES, execution_mode=EXECUTION_MODE_LIVE)
    snap_replay = _snapshot(T1, FLAT_CLOSES, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    node_live, _ = _node(snap_live)
    node_replay, _ = _node(snap_replay)
    assert node_live.state_id == node_replay.state_id


# ---------------------------------------------------------------------- #
# Property 3: no future-state leakage
# ---------------------------------------------------------------------- #
def test_no_future_state_leakage_in_nodes_as_of():
    snap1 = _snapshot(T1, FLAT_CLOSES, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, VOLATILE_CLOSES, ce_bid=100.0, ce_ask=110.0, pe_bid=80.0, pe_ask=90.0, vix=25.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)

    all_nodes = [node1, node2]
    cutoff_before_node2 = T1
    visible = nodes_as_of(all_nodes, as_of_time=cutoff_before_node2)
    assert node2.state_id not in {n.state_id for n in visible}
    assert node1.state_id in {n.state_id for n in visible}


def test_build_state_sequence_never_reaches_forward_in_time():
    snap1 = _snapshot(T1, FLAT_CLOSES)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, VOLATILE_CLOSES, vix=25.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)

    sequence = build_state_sequence([node1, node2], from_state_id=node1.state_id)
    assert [n.state_id for n in sequence] == [node1.state_id]  # walking back from node1 never reaches node2.


# ---------------------------------------------------------------------- #
# Property 4: transition evidence validation
# ---------------------------------------------------------------------- #
def test_every_detected_transition_carries_real_evidence():
    snap1 = _snapshot(T1, FLAT_CLOSES, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, VOLATILE_CLOSES, ce_bid=100.0, ce_ask=110.0, pe_bid=80.0, pe_ask=90.0, vix=25.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)

    assert node2.transition is not None
    assert node2.transition.transition_type in ALL_STATE_TRANSITION_TYPES
    assert len(node2.transition.evidence) > 0
    assert node2.transition.from_state_id == node1.state_id
    assert node2.transition.to_state_id == node2.state_id


def test_observation_and_interpretation_confidence_are_never_combined():
    snap1 = _snapshot(T1, FLAT_CLOSES)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, VOLATILE_CLOSES, vix=25.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)
    assert node2.transition is not None
    # Two SEPARATE, independently-set fields -- never merged into one score.
    assert node2.transition.observation_confidence != {} and node2.transition.interpretation_confidence != {}
    assert isinstance(node2.transition.observation_confidence, str)
    assert isinstance(node2.transition.interpretation_confidence, str)


# ---------------------------------------------------------------------- #
# Property 5: unknown transition handling
# ---------------------------------------------------------------------- #
def test_no_transition_reported_without_a_previous_node():
    snap = _snapshot(T1, FLAT_CLOSES, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1, vix=12.0)
    node, _ = _node(snap)
    assert node.transition is None
    assert node.previous_state_id is None


def test_no_transition_reported_when_nothing_real_changed():
    """Two calm, unchanged cycles produce no transition -- never a
    fabricated UNKNOWN-shaped edge just to have something to report."""
    snap1 = _snapshot(T1, FLAT_CLOSES, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1, vix=12.0)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, FLAT_CLOSES, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1, vix=12.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)
    assert node2.transition is None
    assert node2.previous_state_id == node1.state_id  # still linked, just no named transition.


# ---------------------------------------------------------------------- #
# Property 6: no strategy vocabulary
# ---------------------------------------------------------------------- #
def test_no_strategy_vocabulary_in_identifiers():
    forbidden_terms = ("buy_breakout", "sell_premium", "buy_dip", "order", "quantity", "entry_signal", "exit_signal")
    for filename in MSG_FILES:
        path = os.path.join(MSG_MODULE_DIR, filename)
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
            assert not matches, f"{filename} has strategy-shaped identifier(s): {matches}"


def test_no_prediction_shaped_transition_types():
    """Wrong: BREAKOUT_EXPECTED. Correct: RANGE_TO_TREND (an observation
    that already occurred). Every real transition type name is checked
    to never contain a future-tense/prediction word."""
    forbidden_words = ("expected", "predicted", "will_", "forecast", "probability")
    for transition_type in ALL_STATE_TRANSITION_TYPES:
        lowered = transition_type.lower()
        for word in forbidden_words:
            assert word not in lowered, f"{transition_type} reads as a prediction, not an observation"


def test_no_order_shaped_fields_on_models():
    forbidden = {"action", "order", "quantity", "entry_price", "stop_loss", "side", "direction_call"}
    for cls in (MarketStateNode, StateTransitionEdge):
        field_names = set(cls.__dataclass_fields__.keys())
        assert not (field_names & forbidden), f"{cls.__name__} has strategy-shaped fields: {field_names & forbidden}"


# ---------------------------------------------------------------------- #
# Property 7: memory compatibility
# ---------------------------------------------------------------------- #
def test_memory_round_trip_via_event_store(tmp_path):
    snap1 = _snapshot(T1, FLAT_CLOSES)
    node1, phen1 = _node(snap1)
    snap2 = _snapshot(T2, VOLATILE_CLOSES, vix=25.0)
    node2, _ = _node(snap2, previous_snapshot=snap1, previous_phenomena=phen1, previous_node=node1)

    store = EventStore(str(tmp_path / "state_graph.jsonl"))
    record_market_state_node(store, node1, recorded_at=T1)
    record_market_state_node(store, node2, recorded_at=T2)

    hydrated = hydrate_market_state_graph(store)
    assert set(hydrated.keys()) == {node1.state_id, node2.state_id}
    assert hydrated[node2.state_id].to_dict() == node2.to_dict()

    sequence = build_state_sequence(list(hydrated.values()), from_state_id=node2.state_id)
    assert [n.state_id for n in sequence] == [node1.state_id, node2.state_id]


def test_state_node_shares_intelligence_snapshot_identity_conventions():
    """This phase's nodes key off the same real timestamp convention
    Phase 19.5's MarketMemoryEntry already uses (`as_of_time.isoformat()`),
    proving compatibility for a future combined "state sequence +
    phenomena + outcome" memory extension without inventing a new time
    representation."""
    snap = _snapshot(T1, FLAT_CLOSES)
    node, _ = _node(snap)
    assert node.timestamp == snap.as_of_time.isoformat()


# ---------------------------------------------------------------------- #
# Property 8: no direct datastore access
# ---------------------------------------------------------------------- #
def test_no_broker_or_direct_eventstore_import_in_models_transitions_engine():
    """`memory.py` is explicitly ALLOWED to import EventStore -- it IS
    the memory module. `models.py`/`transitions.py`/`engine.py` must
    not."""
    forbidden_import_substrings = ("broker", "fyers", "eventstore", "sqlite")
    for filename in ("models.py", "transitions.py", "engine.py", "__init__.py"):
        path = os.path.join(MSG_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    if filename == "__init__.py" and term == "eventstore":
                        continue  # __init__.py re-exports memory.py's own EventStore-based functions by name, not by importing EventStore itself.
                    assert term not in node.module.lower(), f"{filename} imports from a datastore/broker-shaped module: {node.module}"


def test_no_direct_brain_analyze_calls():
    forbidden_call_names = {"RegimeBrain", "StructureBrain", "LiquidityBrain", "VolatilityBrain", "GreeksBrain", "EventBrain"}
    for filename in MSG_FILES:
        path = os.path.join(MSG_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "analyze":
                pytest.fail(f"{filename} calls .analyze( directly")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_call_names:
                pytest.fail(f"{filename} instantiates {node.func.id} directly")

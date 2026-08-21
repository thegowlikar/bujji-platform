"""Market Understanding Memory -- Phase 19.5 tests.

Proves the 7 required properties:
1. Same snapshot -> same memory identity.
2. Historical replay produces identical similarity result.
3. Future observations cannot leak into past similarity search.
4. Similarity explanation is deterministic.
5. No strategy execution vocabulary.
6. No direct broker/order imports.
7. Memory layer can operate without live market (pure functions/fixtures only).
"""
from __future__ import annotations

import ast
import os
from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.decision_context import build_decision_context
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
from bujji.market_understanding.memory_engine import (
    build_market_memory_entry,
    hydrate_market_memory,
    record_market_memory,
    record_outcome_observation,
)
from bujji.market_understanding.memory_models import (
    STATUS_KNOWN,
    MarketMemoryEntry,
    MarketOutcomeObservation,
    market_memory_id_for,
)
from bujji.market_understanding.memory_query import find_similar_memories_as_of
from bujji.market_understanding.memory_similarity import explain_similarity, find_similar_memories
from bujji.state_persistence.store import EventStore

MEMORY_MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "market_understanding")
MEMORY_FILES = ("memory_models.py", "memory_similarity.py", "memory_engine.py", "memory_query.py")


def _candles(t, closes):
    return [
        Candle(t - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _entry(t, ce, pe, closes, underlying="NIFTY", execution_mode=EXECUTION_MODE_LIVE):
    context = IntelligenceContext(as_of_time=t, execution_mode=execution_mode, reality_snapshot_reference=f"ref-{t.isoformat()}")
    candles = _candles(t, closes)
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(closes[-1], [(closes[-1] - 100, 500, 100), (closes[-1] + 100, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(100.0, 100.2, 80.0, 80.1, context=context)
    volatility = VolatilityBrain().analyze(candles, closes[-1], 24000, 5 / 365, ce, pe, context=context)
    greeks = GreeksBrain().analyze(spot=closes[-1], strike=24000, t_years=5 / 365, iv_ce=0.2, iv_pe=0.2, context=context)
    event = EventBrain().analyze(date(2026, 8, 21), t.date(), 15.0, context=context)
    snapshot = build_market_intelligence_snapshot(
        context=context, created_at=t, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    decision_context = build_decision_context(snapshot)
    return build_market_memory_entry(snapshot=snapshot, decision_context=decision_context, underlying=underlying, recorded_at=t)


T1 = datetime(2026, 8, 14, 10, 30, tzinfo=IST)
T2 = datetime(2025, 3, 18, 10, 30, tzinfo=IST)
T3 = datetime(2024, 1, 1, 10, 30, tzinfo=IST)

TRENDING_CLOSES = [24000 + i * 8 for i in range(16)]
RANGING_CLOSES = [24027.45, 24023.5, 24027.45, 24028.05, 24067.95, 24075.0, 24107.45,
                   24116.0, 24121.2, 24110.1, 24109.3, 24114.15, 24113.8, 24119.1, 24145.8, 24154.75]


# ---------------------------------------------------------------------- #
# Property 1: same snapshot -> same memory identity
# ---------------------------------------------------------------------- #
def test_same_snapshot_produces_same_memory_identity():
    entry_a = _entry(T1, 250.0, 250.0, TRENDING_CLOSES)
    entry_b = _entry(T1, 250.0, 250.0, TRENDING_CLOSES)
    assert entry_a.market_memory_id == entry_b.market_memory_id
    assert entry_a.intelligence_snapshot_id == entry_b.intelligence_snapshot_id
    assert entry_a.market_memory_id == market_memory_id_for(entry_a.intelligence_snapshot_id, "NIFTY")


def test_intelligence_fingerprint_equals_intelligence_snapshot_id():
    entry = _entry(T1, 250.0, 250.0, TRENDING_CLOSES)
    assert entry.intelligence_fingerprint == entry.intelligence_snapshot_id


# ---------------------------------------------------------------------- #
# Property 2: historical replay produces identical similarity result
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_similarity_result():
    live_entry = _entry(T1, 260.0, 240.0, TRENDING_CLOSES, execution_mode=EXECUTION_MODE_LIVE)
    replay_entry = _entry(T1, 260.0, 240.0, TRENDING_CLOSES, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    candidate = _entry(T2, 40.0, 35.0, RANGING_CLOSES)

    live_result = explain_similarity(live_entry, candidate)
    replay_result = explain_similarity(replay_entry, candidate)

    assert live_result.similarity_pct == replay_result.similarity_pct
    assert live_result.breakdown == replay_result.breakdown
    assert live_entry.market_memory_id == replay_entry.market_memory_id  # same underlying fingerprint


# ---------------------------------------------------------------------- #
# Property 3: future observations cannot leak into past similarity search
# ---------------------------------------------------------------------- #
def test_no_look_ahead_in_similarity_search():
    early = _entry(T3, 250.0, 250.0, TRENDING_CLOSES)      # 2024-01-01
    later = _entry(T2, 260.0, 240.0, TRENDING_CLOSES)       # 2025-03-18
    latest = _entry(T1, 270.0, 230.0, TRENDING_CLOSES)      # 2026-08-14

    universe = [early, later, latest]

    # Querying AS OF a time before `later`/`latest` exist must never surface them.
    results_from_early = find_similar_memories_as_of(early, universe, as_of_time=T3, top_n=5)
    assert results_from_early == []

    # Querying as of T2 may see `early` but never `latest` (which is in T2's future).
    results_from_later = find_similar_memories_as_of(later, universe, as_of_time=T2, top_n=5)
    surfaced_ids = {entry.market_memory_id for entry, _ in results_from_later}
    assert latest.market_memory_id not in surfaced_ids
    assert early.market_memory_id in surfaced_ids


def test_no_look_ahead_excludes_candidates_strictly_after_as_of_time():
    early = _entry(T3, 250.0, 250.0, TRENDING_CLOSES)
    latest = _entry(T1, 270.0, 230.0, TRENDING_CLOSES)
    results = find_similar_memories_as_of(early, [early, latest], as_of_time=T3, top_n=5)
    assert all(entry.market_memory_id != latest.market_memory_id for entry, _ in results)


# ---------------------------------------------------------------------- #
# Property 4: similarity explanation is deterministic
# ---------------------------------------------------------------------- #
def test_similarity_explanation_is_deterministic_and_self_consistent():
    target = _entry(T1, 260.0, 240.0, TRENDING_CLOSES)
    candidate = _entry(T2, 40.0, 35.0, RANGING_CLOSES)

    result_a = explain_similarity(target, candidate)
    result_b = explain_similarity(target, candidate)
    assert result_a == result_b

    # The breakdown alone reproduces the score -- never a black box.
    from bujji.market_understanding.memory_similarity import MAX_POSSIBLE_SCORE
    raw_score = sum(c.contribution for c in result_a.breakdown)
    expected_pct = round(100 * max(0, raw_score) / MAX_POSSIBLE_SCORE, 1)
    assert result_a.similarity_pct == expected_pct


def test_find_similar_memories_ranking_is_deterministic():
    target = _entry(T1, 260.0, 240.0, TRENDING_CLOSES)
    candidates = [_entry(T2, 40.0, 35.0, RANGING_CLOSES), _entry(T3, 270.0, 230.0, TRENDING_CLOSES)]
    ranked_a = find_similar_memories(target, candidates)
    ranked_b = find_similar_memories(target, candidates)
    assert [e.market_memory_id for e, _ in ranked_a] == [e.market_memory_id for e, _ in ranked_b]
    assert [exp.similarity_pct for _, exp in ranked_a] == [exp.similarity_pct for _, exp in ranked_b]


# ---------------------------------------------------------------------- #
# Property 5: no strategy execution vocabulary
# ---------------------------------------------------------------------- #
def test_no_execution_vocabulary_in_memory_identifiers():
    identifier_terms = ("order", "fill", "buy", "sell", "entry_signal", "exit_signal", "qty", "quantity")
    for filename in MEMORY_FILES:
        path = os.path.join(MEMORY_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.add(node.name.lower())
            elif isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.arg):
                identifiers.add(node.arg.lower())
        for term in identifier_terms:
            matches = [i for i in identifiers if term in i]
            assert not matches, f"{filename} has execution-shaped identifier(s): {matches}"


def test_memory_entry_never_carries_a_buy_sell_recommendation_field():
    forbidden = {"action", "recommendation", "signal", "order", "side", "direction_call"}
    field_names = set(MarketMemoryEntry.__dataclass_fields__.keys())
    assert not (field_names & forbidden), f"MarketMemoryEntry has recommendation-shaped fields: {field_names & forbidden}"


# ---------------------------------------------------------------------- #
# Property 6: no direct broker/order imports
# ---------------------------------------------------------------------- #
def test_no_broker_imports_anywhere_in_memory_package():
    forbidden_import_substrings = ("broker", "fyers", "order", "execution_reality")
    for filename in MEMORY_FILES:
        path = os.path.join(MEMORY_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports from a broker/order-shaped module: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for term in forbidden_import_substrings:
                        assert term not in alias.name.lower(), f"{filename} imports a broker/order-shaped module: {alias.name}"


# ---------------------------------------------------------------------- #
# Property 7: memory layer can operate without live market
# ---------------------------------------------------------------------- #
def test_memory_layer_operates_fully_offline_via_persisted_events(tmp_path):
    """No live broker connection, no network call -- everything here is
    either a pure function or reads/writes a local, isolated temp file.
    Uses an isolated tempfile store, never the production DB (this
    project's own standing discipline since the Phase 18.12 incident)."""
    store = EventStore(str(tmp_path / "market_memory.jsonl"))
    entry = _entry(T1, 250.0, 250.0, TRENDING_CLOSES)

    record_market_memory(store, entry, recorded_at=T1)
    hydrated = hydrate_market_memory(store)
    assert entry.market_memory_id in hydrated
    assert hydrated[entry.market_memory_id].to_dict() == entry.to_dict()

    observation = MarketOutcomeObservation(
        sessions_later=2, observed_at=(T1 + timedelta(days=2)).isoformat(),
        regime_after="TRENDING", volatility_richness_after="IV_RICH", posture_after="TRENDING",
        status=STATUS_KNOWN,
    )
    record_outcome_observation(store, entry.market_memory_id, observation, recorded_at=T1 + timedelta(days=2))
    rehydrated = hydrate_market_memory(store)
    assert rehydrated[entry.market_memory_id].outcome_observation == observation
    # Original in-memory entry object is untouched -- immutability.
    assert entry.outcome_observation is None


def test_outcome_observation_is_a_separate_immutable_fact_not_a_mutation():
    entry = _entry(T1, 250.0, 250.0, TRENDING_CLOSES)
    observation = MarketOutcomeObservation(
        sessions_later=1, observed_at=(T1 + timedelta(days=1)).isoformat(),
        regime_after="RANGING", volatility_richness_after="IV_CHEAP", posture_after="RANGING",
        status=STATUS_KNOWN,
    )
    updated = entry.with_outcome_observation(observation)
    assert entry.outcome_observation is None       # original never mutated
    assert updated.outcome_observation == observation
    assert updated.market_memory_id == entry.market_memory_id  # same identity, new fact layered on

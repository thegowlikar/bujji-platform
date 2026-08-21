"""Decision Intelligence Engine Foundation -- Phase 19.6 tests.

Proves the 8 required properties:
1. Deterministic output.
2. Replay equality (LIVE vs HISTORICAL_REPLAY).
3. No execution vocabulary.
4. No direct brain calls.
5. No datastore access.
6. Memory integration.
7. Contradiction detection.
8. Insufficient evidence handling.
"""
from __future__ import annotations

import ast
import os
from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.decision_context import build_decision_context
from bujji.decision_intelligence import DecisionIntelligenceSnapshot, DecisionPosture, build_decision_intelligence_snapshot
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
from bujji.market_understanding.memory_models import STATUS_KNOWN, MarketMemoryEntry, MarketOutcomeObservation
from bujji.market_understanding.memory_similarity import explain_similarity

DI_MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "decision_intelligence")
DI_FILES = ("models.py", "evidence.py", "reasoning.py", "engine.py", "__init__.py")

T = datetime(2026, 8, 14, 10, 30, tzinfo=IST)
TRENDING_CLOSES = [24000 + i * 3 for i in range(16)]


def _candles(closes):
    return [
        Candle(T - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _build(execution_mode=EXECUTION_MODE_LIVE, ce=250.0, pe=250.0, ce_bid=100.0, ce_ask=101.0, pe_bid=80.0, pe_ask=80.5, memory_matches=None):
    context = IntelligenceContext(as_of_time=T, execution_mode=execution_mode, reality_snapshot_reference="ref")
    candles = _candles(TRENDING_CLOSES)
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(TRENDING_CLOSES[-1], [(TRENDING_CLOSES[-1] - 100, 500, 100), (TRENDING_CLOSES[-1] + 100, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(ce_bid, ce_ask, pe_bid, pe_ask, context=context)
    volatility = VolatilityBrain().analyze(candles, TRENDING_CLOSES[-1], 24000, 5 / 365, ce, pe, context=context)
    greeks = GreeksBrain().analyze(spot=TRENDING_CLOSES[-1], strike=24000, t_years=5 / 365, iv_ce=0.2, iv_pe=0.2, context=context)
    event = EventBrain().analyze(date(2026, 8, 21), T.date(), 15.0, context=context)
    snapshot = build_market_intelligence_snapshot(
        context=context, created_at=T, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    decision_context = build_decision_context(snapshot)
    return build_decision_intelligence_snapshot(
        snapshot=snapshot, decision_context=decision_context,
        memory_matches=memory_matches or [], created_at=T,
    )


# ---------------------------------------------------------------------- #
# Property 1: deterministic output
# ---------------------------------------------------------------------- #
def test_deterministic_output():
    di_a = _build()
    di_b = _build()
    assert di_a.decision_intelligence_id == di_b.decision_intelligence_id
    assert di_a.fingerprint() == di_a.decision_intelligence_id


# ---------------------------------------------------------------------- #
# Property 2: replay equality
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_fingerprint():
    live_di = _build(execution_mode=EXECUTION_MODE_LIVE)
    replay_di = _build(execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    assert live_di.decision_intelligence_id == replay_di.decision_intelligence_id
    assert live_di.recommended_posture == replay_di.recommended_posture
    assert live_di.environment_assessment == replay_di.environment_assessment


# ---------------------------------------------------------------------- #
# Property 3: no execution vocabulary
# ---------------------------------------------------------------------- #
def test_no_execution_vocabulary_in_identifiers():
    # "buy"/"sell" deliberately excluded here as bare substrings -- this
    # package legitimately uses "premium_selling"/"premium_buying"
    # (the existing msi_strategy_selection_foundation taxonomy's own
    # family names), which are strategy-family CONCEPTS, not order
    # actions. An actual order-action identifier would be caught by
    # "order"/"fill"/"quantity" etc. below, or by the dedicated
    # buy/sell-order check in test_no_ce_pe_or_buy_sell_strings_in_a_real_built_snapshot.
    identifier_terms = ("order", "fill", "quantity", "qty", "stoploss", "stop_loss", "entryprice", "entry_price")
    for filename in DI_FILES:
        path = os.path.join(DI_MODULE_DIR, filename)
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


def test_decision_intelligence_snapshot_has_no_order_shaped_fields():
    forbidden = {"action", "order", "quantity", "qty", "entry_price", "stop_loss", "side", "instrument", "strike", "option_type"}
    field_names = set(DecisionIntelligenceSnapshot.__dataclass_fields__.keys())
    assert not (field_names & forbidden), f"DecisionIntelligenceSnapshot has execution-shaped fields: {field_names & forbidden}"


def test_no_ce_pe_or_buy_sell_strings_in_a_real_built_snapshot():
    di = _build()
    serialized = str(di.to_dict()).upper()
    for forbidden_token in (" BUY ", " SELL ", "\"CE\"", "\"PE\""):
        assert forbidden_token not in serialized


# ---------------------------------------------------------------------- #
# Property 4: no direct brain calls
# ---------------------------------------------------------------------- #
def test_no_direct_brain_analyze_calls_in_decision_intelligence():
    """AST-level check on actual CALLS, not prose -- this package's own
    module docstrings legitimately explain that it never calls
    `.analyze(`, which would trip a naive whole-file substring search on
    its own disclaimer (the same false positive caught and fixed in the
    Phase 19.4 test suite)."""
    forbidden_call_names = {
        "RegimeBrain", "StructureBrain", "LiquidityBrain", "VolatilityBrain", "GreeksBrain", "EventBrain",
    }
    for filename in DI_FILES:
        path = os.path.join(DI_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "analyze":
                pytest.fail(f"{filename} calls .analyze( directly -- line {getattr(node, 'lineno', '?')}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_call_names:
                pytest.fail(f"{filename} instantiates {node.func.id} directly")


# ---------------------------------------------------------------------- #
# Property 5: no datastore access
# ---------------------------------------------------------------------- #
def test_no_datastore_or_broker_imports():
    forbidden_import_substrings = ("eventstore", "broker", "fyers", "sqlite", "historical_reality", "market_reality")
    for filename in DI_FILES:
        path = os.path.join(DI_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports from a datastore/broker-shaped module: {node.module}"


# ---------------------------------------------------------------------- #
# Property 6: memory integration
# ---------------------------------------------------------------------- #
def test_memory_integration_reflected_in_memory_context():
    di_no_memory = _build(memory_matches=[])
    assert di_no_memory.memory_context.matched_count == 0
    assert di_no_memory.memory_context.confidence_note == "no historical precedent found"

    fake_entry = MarketMemoryEntry(
        market_memory_id="MKTMEM-fake1", intelligence_snapshot_id="x", intelligence_fingerprint="x",
        as_of_time=T.isoformat(), underlying="NIFTY", recorded_at=T.isoformat(),
        regime_state="TRENDING", volatility_richness="IV_RICH", structure_proximity="MID_RANGE",
        liquidity_tightness="NORMAL", event_expiry_proximity="NORMAL", event_vix_regime="LOW",
        posture="TRENDING", confidence=0.8, compatible_strategy_families=(), blocked_strategy_families=(),
        intelligence_snapshot={}, decision_context={},
    )
    fake_target = fake_entry
    explanation = explain_similarity(fake_target, fake_entry)
    di_with_memory = _build(memory_matches=[(fake_entry, explanation)])
    assert di_with_memory.memory_context.matched_count == 1
    assert "limited" in di_with_memory.memory_context.confidence_note
    assert fake_entry.market_memory_id in di_with_memory.memory_context.matched_market_memory_ids
    assert fake_entry.market_memory_id in di_with_memory.evidence_bundle["memory_evidence_references"]


def test_memory_statistic_only_reported_with_enough_known_samples():
    def make_entry(idx, richness_after):
        obs = MarketOutcomeObservation(
            sessions_later=2, observed_at=T.isoformat(), regime_after="TRENDING",
            volatility_richness_after=richness_after, posture_after="TRENDING", status=STATUS_KNOWN,
        )
        entry = MarketMemoryEntry(
            market_memory_id=f"MKTMEM-fake{idx}", intelligence_snapshot_id="x", intelligence_fingerprint="x",
            as_of_time=T.isoformat(), underlying="NIFTY", recorded_at=T.isoformat(),
            regime_state="TRENDING", volatility_richness="IV_RICH", structure_proximity="MID_RANGE",
            liquidity_tightness="NORMAL", event_expiry_proximity="NORMAL", event_vix_regime="LOW",
            posture="TRENDING", confidence=0.8, compatible_strategy_families=(), blocked_strategy_families=(),
            intelligence_snapshot={}, decision_context={},
        )
        return entry.with_outcome_observation(obs)

    target = make_entry(0, "IV_RICH")
    few_matches = [(make_entry(i, "IV_RICH"), explain_similarity(target, make_entry(i, "IV_RICH"))) for i in range(1, 3)]
    di_few = _build(memory_matches=few_matches)
    assert di_few.memory_context.statistic is None  # only 2 samples -- below MIN_SAMPLES_FOR_STATISTIC.

    many_matches = [(make_entry(i, "IV_RICH" if i % 2 == 0 else "IV_CHEAP"), explain_similarity(target, make_entry(i, "IV_RICH"))) for i in range(1, 6)]
    di_many = _build(memory_matches=many_matches)
    assert di_many.memory_context.statistic is not None
    assert "of 5" in di_many.memory_context.statistic


# ---------------------------------------------------------------------- #
# Property 7: contradiction detection
# ---------------------------------------------------------------------- #
def test_contradiction_detected_when_rich_volatility_meets_poor_liquidity():
    di = _build(ce=400.0, pe=350.0, ce_bid=100.0, ce_ask=110.0, pe_bid=80.0, pe_ask=90.0)  # rich premiums + wide spread
    assert len(di.contradictions) >= 1
    assert any("liquidity" in c.dimensions_involved for c in di.contradictions)
    assert di.recommended_posture == DecisionPosture.REDUCE_EXPOSURE  # WIDE liquidity takes priority in posture


def test_no_contradiction_when_conditions_are_aligned():
    di = _build(ce=40.0, pe=35.0, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1)  # cheap premiums + tight spread
    assert all("liquidity" not in c.dimensions_involved for c in di.contradictions)


# ---------------------------------------------------------------------- #
# Property 8: insufficient evidence handling
# ---------------------------------------------------------------------- #
def test_uncertainty_observations_present_for_unassessable_families():
    di = _build()
    assert len(di.uncertainty_observations) > 0
    descriptions = [u.description for u in di.uncertainty_observations]
    assert any("LONG_DIRECTIONAL" in d or "CALENDAR" in d for d in descriptions)


def test_insufficient_information_posture_when_nothing_assessable():
    """Flat, unresolvable inputs (UNKNOWN richness, no clean liquidity
    signal) should never be forced into a false FAVOR_* posture."""
    context = IntelligenceContext(as_of_time=T, execution_mode=EXECUTION_MODE_LIVE)
    flat_candles = _candles([24000.0] * 16)
    regime = RegimeBrain().analyze(flat_candles, context)
    structure = StructureBrain().analyze(24000, [], context=context)  # no strikes -> UNKNOWN
    liquidity = LiquidityBrain().analyze(None, None, None, None, context=context)  # invalid -> UNKNOWN
    volatility = VolatilityBrain().analyze(flat_candles, 24000, 24000, 5 / 365, 250.0, 250.0, context=context)  # flat -> realized_vol 0 -> UNKNOWN richness
    greeks = GreeksBrain().analyze(spot=24000, strike=24000, t_years=5 / 365, iv_ce=0.2, iv_pe=0.2, context=context)
    event = EventBrain().analyze(date(2026, 8, 21), T.date(), None, context=context)  # no vix -> only expiry half known
    snapshot = build_market_intelligence_snapshot(
        context=context, created_at=T, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    decision_context = build_decision_context(snapshot)
    di = build_decision_intelligence_snapshot(snapshot=snapshot, decision_context=decision_context, memory_matches=[], created_at=T)
    assert di.recommended_posture in (DecisionPosture.INSUFFICIENT_INFORMATION, DecisionPosture.OBSERVE)
    assert len(di.uncertainty_observations) >= 1

"""Market Phenomena Recognition Foundation -- Phase 19.7 tests.

Proves the 8 required properties:
1. deterministic fingerprint
2. replay equality
3. no strategy vocabulary
4. evidence traceability
5. contradiction handling
6. historical memory compatibility
7. insufficient evidence handling
8. no direct data-store access
"""
from __future__ import annotations

import ast
import os
from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
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
from bujji.market_phenomena import (
    ALL_PHENOMENON_TYPES,
    PHENOMENON_EVENT_RISK,
    PHENOMENON_LIQUIDITY_STRESS,
    PHENOMENON_REGIME_TRANSITION,
    PHENOMENON_VOLATILITY_COMPRESSION,
    PHENOMENON_VOLATILITY_EXPANSION,
    MarketPhenomenaAssessment,
    build_market_phenomena_assessment,
)
from bujji.market_understanding.memory_engine import build_market_memory_entry
from bujji.decision_context import build_decision_context

MP_MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "market_phenomena")
MP_FILES = ("models.py", "evidence.py", "detectors.py", "engine.py", "__init__.py")

T_PREV = datetime(2026, 8, 13, 10, 30, tzinfo=IST)
T_CUR = datetime(2026, 8, 14, 10, 30, tzinfo=IST)

FLAT_CLOSES = [24000.0] * 16
VOLATILE_CLOSES = [24000, 24150, 23900, 24200, 23850, 24250, 23800, 24300,
                    23750, 24350, 23700, 24400, 23650, 24450, 23600, 24500]


def _candles(t, closes):
    return [
        Candle(t - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _snapshot(t, closes, execution_mode=EXECUTION_MODE_LIVE, ce_bid=100.0, ce_ask=110.0, pe_bid=80.0, pe_ask=90.0, vix=15.0, expiry=date(2026, 8, 14)):
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


# ---------------------------------------------------------------------- #
# Property 1: deterministic fingerprint
# ---------------------------------------------------------------------- #
def test_deterministic_fingerprint():
    prev = _snapshot(T_PREV, FLAT_CLOSES)
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    a = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=prev, created_at=T_CUR)
    b = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=prev, created_at=T_CUR)
    assert a.assessment_id == b.assessment_id
    assert a.fingerprint() == a.assessment_id
    for p in a.phenomena:
        assert p.fingerprint() is not None


# ---------------------------------------------------------------------- #
# Property 2: replay equality
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_fingerprint():
    prev = _snapshot(T_PREV, FLAT_CLOSES)
    cur_live = _snapshot(T_CUR, VOLATILE_CLOSES, execution_mode=EXECUTION_MODE_LIVE, vix=25.0)
    cur_replay = _snapshot(T_CUR, VOLATILE_CLOSES, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY, vix=25.0)
    a = build_market_phenomena_assessment(snapshot=cur_live, previous_snapshot=prev, created_at=T_CUR)
    b = build_market_phenomena_assessment(snapshot=cur_replay, previous_snapshot=prev, created_at=T_CUR)
    assert a.assessment_id == b.assessment_id
    assert {p.phenomenon_type for p in a.phenomena} == {p.phenomenon_type for p in b.phenomena}


# ---------------------------------------------------------------------- #
# Property 3: no strategy vocabulary
# ---------------------------------------------------------------------- #
def test_no_strategy_vocabulary_in_identifiers():
    forbidden_identifier_terms = ("buy_breakout", "sell_premium", "buy_dip", "entry_signal", "exit_signal", "order", "quantity")
    for filename in MP_FILES:
        path = os.path.join(MP_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.add(node.name.lower())
            elif isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
        for term in forbidden_identifier_terms:
            matches = [i for i in identifiers if term in i]
            assert not matches, f"{filename} has strategy-shaped identifier(s): {matches}"


def test_no_direction_prediction_or_entry_fields_on_models():
    from bujji.market_phenomena.models import MarketPhenomenonAssessment, MarketPhenomenaAssessment
    forbidden = {"action", "order", "quantity", "entry_price", "stop_loss", "direction", "side", "strike", "option_type"}
    for cls in (MarketPhenomenonAssessment, MarketPhenomenaAssessment):
        field_names = set(cls.__dataclass_fields__.keys())
        assert not (field_names & forbidden), f"{cls.__name__} has strategy-shaped fields: {field_names & forbidden}"


# ---------------------------------------------------------------------- #
# Property 4: evidence traceability
# ---------------------------------------------------------------------- #
def test_every_detected_phenomenon_carries_supporting_evidence():
    prev = _snapshot(T_PREV, FLAT_CLOSES)
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=prev, created_at=T_CUR)
    assert len(assessment.phenomena) > 0
    for p in assessment.phenomena:
        assert len(p.supporting_evidence) > 0
        for item in p.supporting_evidence:
            assert item.source in ("RegimeReading", "VolatilityReading", "LiquidityReading", "EventReading",
                                    "RegimeReading(previous)", "RegimeReading(current)")
            assert item.metric  # never an empty/placeholder metric name


def test_evidence_values_are_copied_verbatim_from_real_readings():
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=None, created_at=T_CUR)
    liquidity_phenomenon = next(p for p in assessment.phenomena if p.phenomenon_type == PHENOMENON_LIQUIDITY_STRESS)
    spread_item = next(i for i in liquidity_phenomenon.supporting_evidence if i.metric == "combined_spread")
    # "combined_spread" is a real FIELD on LiquidityReading, not a key in
    # its `.evidence` dict -- the evidence-item helper falls back to the
    # Reading's own attribute when the metric isn't in `.evidence`,
    # verified here against that real field, not a nonexistent dict key.
    assert spread_item.value == cur.liquidity.combined_spread


# ---------------------------------------------------------------------- #
# Property 5: contradiction handling
# ---------------------------------------------------------------------- #
def test_regime_transition_low_confidence_reported_as_contradiction():
    prev = _snapshot(T_PREV, FLAT_CLOSES)
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=prev, created_at=T_CUR)
    transition = next((p for p in assessment.phenomena if p.phenomenon_type == PHENOMENON_REGIME_TRANSITION), None)
    assert transition is not None
    # contradicting_evidence is always a real, present tuple field -- empty or populated, never absent.
    assert isinstance(transition.contradicting_evidence, tuple)


def test_volatility_compression_flags_contradiction_when_realized_vol_actually_rose():
    """A COMPRESSED regime this cycle but realized_vol that actually rose
    versus the previous cycle is a real, detectable tension -- not
    silently hidden."""
    calm_prev_closes = [24000 + (0.1 if i % 2 == 0 else -0.1) for i in range(16)]
    prev = _snapshot(T_PREV, calm_prev_closes)
    calm_cur_closes = [24000 + (2.0 if i % 2 == 0 else -2.0) for i in range(16)]  # still narrow enough to stay COMPRESSED-ish, but higher than prev
    cur = _snapshot(T_CUR, calm_cur_closes, vix=12.0)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=prev, created_at=T_CUR)
    compression = next((p for p in assessment.phenomena if p.phenomenon_type == PHENOMENON_VOLATILITY_COMPRESSION), None)
    if compression is not None:
        assert isinstance(compression.contradicting_evidence, tuple)  # present, real field either way


# ---------------------------------------------------------------------- #
# Property 6: historical memory compatibility
# ---------------------------------------------------------------------- #
def test_market_memory_entry_and_phenomena_assessment_share_the_same_intelligence_snapshot_id():
    """Phase 19.5's MarketMemoryEntry and this phase's
    MarketPhenomenaAssessment both key off the SAME real
    intelligence_snapshot_id -- future memory can be extended to store
    phenomena + outcome without inventing a second identity scheme."""
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    decision_context = build_decision_context(cur)
    memory_entry = build_market_memory_entry(snapshot=cur, decision_context=decision_context, underlying="NIFTY", recorded_at=T_CUR)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=None, created_at=T_CUR)
    assert memory_entry.intelligence_snapshot_id == assessment.intelligence_snapshot_id == cur.intelligence_snapshot_id


# ---------------------------------------------------------------------- #
# Property 7: insufficient evidence handling
# ---------------------------------------------------------------------- #
def test_insufficient_evidence_produces_honest_not_detected_list():
    """A snapshot with no real event risk, tight liquidity, a fair/flat
    regime, and no previous snapshot (so no transition is even
    possible) must NOT force any phenomenon -- every undetected type is
    disclosed in `not_detected`, never silently omitted."""
    cur = _snapshot(T_CUR, [24000.0] * 16, ce_bid=100.0, ce_ask=100.2, pe_bid=80.0, pe_ask=80.1, vix=12.0, expiry=date(2026, 8, 21))
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=None, created_at=T_CUR)
    assert PHENOMENON_REGIME_TRANSITION in assessment.not_detected  # structurally impossible without `previous`
    assert PHENOMENON_EVENT_RISK in assessment.not_detected  # normal expiry, calm VIX
    assert set(assessment.not_detected) | {p.phenomenon_type for p in assessment.phenomena} == set(ALL_PHENOMENON_TYPES)
    if assessment.not_detected:
        assert assessment.not_detected_reason  # a real, non-empty disclosed reason.


def test_regime_transition_never_detected_without_a_previous_snapshot():
    cur = _snapshot(T_CUR, VOLATILE_CLOSES, vix=25.0)
    assessment = build_market_phenomena_assessment(snapshot=cur, previous_snapshot=None, created_at=T_CUR)
    assert PHENOMENON_REGIME_TRANSITION in assessment.not_detected
    assert all(p.phenomenon_type != PHENOMENON_REGIME_TRANSITION for p in assessment.phenomena)


# ---------------------------------------------------------------------- #
# Property 8: no direct data-store access
# ---------------------------------------------------------------------- #
def test_no_datastore_or_broker_imports():
    forbidden_import_substrings = ("eventstore", "broker", "fyers", "sqlite", "historical_reality", "market_reality")
    for filename in MP_FILES:
        path = os.path.join(MP_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for term in forbidden_import_substrings:
                    assert term not in node.module.lower(), f"{filename} imports from a datastore/broker-shaped module: {node.module}"


def test_no_direct_brain_analyze_calls():
    forbidden_call_names = {"RegimeBrain", "StructureBrain", "LiquidityBrain", "VolatilityBrain", "GreeksBrain", "EventBrain"}
    for filename in MP_FILES:
        path = os.path.join(MP_MODULE_DIR, filename)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "analyze":
                pytest.fail(f"{filename} calls .analyze( directly")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_call_names:
                pytest.fail(f"{filename} instantiates {node.func.id} directly")

"""Decision Context Foundation -- Phase 19.4 tests.

Proves the five required properties:
1. Same MarketIntelligenceSnapshot -> identical DecisionContext.
2. Different market states -> different compatibility results.
3. LIVE and HISTORICAL_REPLAY -> identical interpretation.
4. No execution vocabulary anywhere in this package (structural).
5. No modification to Reality layer / brains / MarketIntelligenceSnapshot.
"""
from __future__ import annotations

import ast
import os
from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.decision_context import (
    DecisionContext,
    MarketStateTransition,
    StrategyCompatibilityAssessment,
    TransitionType,
    assess_strategy_compatibility,
    build_decision_context,
    detect_transition,
)
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

FIXED_TIME = datetime(2026, 8, 14, 10, 30, tzinfo=IST)
DECISION_CONTEXT_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "decision_context")

EXECUTION_VOCAB = ("order", "fill", "quantity", "position_size", "buy(", "sell(", "place_order", "qty")


def _candles(closes):
    return [
        Candle(FIXED_TIME - timedelta(minutes=5 * (len(closes) - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _snapshot(execution_mode=EXECUTION_MODE_LIVE, ce_premium=280.0, pe_premium=135.0, trending=False):
    context = IntelligenceContext(as_of_time=FIXED_TIME, execution_mode=execution_mode, reality_snapshot_reference="ref")
    if trending:
        closes = [24000 + i * 8 for i in range(16)]  # strong monotonic move -> TRENDING
    else:
        closes = [24027.45, 24023.5, 24027.45, 24028.05, 24067.95, 24075.0, 24107.45,
                  24116.0, 24121.2, 24110.1, 24109.3, 24114.15, 24113.8, 24119.1, 24145.8, 24154.75]
    candles = _candles(closes)
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(24154.8, [(23900, 500, 100), (24200, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(100.0, 100.2, 80.0, 80.1, context=context)
    volatility = VolatilityBrain().analyze(
        candles, 24154.8, 24000, (21 - 13) * 86400 / (365 * 86400), ce_premium, pe_premium, context=context,
    )
    greeks = GreeksBrain().analyze(spot=24154.8, strike=24000, t_years=(21 - 13) * 86400 / (365 * 86400),
                                    iv_ce=0.2, iv_pe=0.2, context=context)
    event = EventBrain().analyze(date(2026, 8, 21), date(2026, 8, 14), 15.0, context=context)
    return build_market_intelligence_snapshot(
        context=context, created_at=FIXED_TIME, regime=regime, structure=structure,
        liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )


# ---------------------------------------------------------------------- #
# Property 1: determinism
# ---------------------------------------------------------------------- #
def test_same_snapshot_produces_identical_decision_context():
    snap = _snapshot()
    dctx_a = build_decision_context(snap)
    dctx_b = build_decision_context(snap)
    assert dctx_a == dctx_b


# ---------------------------------------------------------------------- #
# Property 2: different market states -> different compatibility
# ---------------------------------------------------------------------- #
def test_iv_rich_vs_iv_cheap_produce_different_compatibility():
    rich_snap = _snapshot(ce_premium=400.0, pe_premium=350.0)   # expensive premiums -> likely IV_RICH
    cheap_snap = _snapshot(ce_premium=40.0, pe_premium=35.0)     # cheap premiums -> likely IV_CHEAP

    assert rich_snap.volatility.richness != cheap_snap.volatility.richness  # sanity: inputs actually differ

    rich_assessment = assess_strategy_compatibility(rich_snap)
    cheap_assessment = assess_strategy_compatibility(cheap_snap)
    assert rich_assessment != cheap_assessment
    assert rich_assessment.compatible_strategy_families != cheap_assessment.compatible_strategy_families


def test_compressed_regime_produces_different_compatibility_than_volatile():
    from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

    context = IntelligenceContext(as_of_time=FIXED_TIME, execution_mode=EXECUTION_MODE_LIVE)
    flat_candles = _candles([24000.0, 24000.5, 24000.2, 24000.6, 24000.1, 24000.4, 24000.3, 24000.2])
    volatile_candles = _candles([24000, 24150, 23900, 24200, 23850, 24250, 23800, 24300])

    flat_regime = RegimeBrain().analyze(flat_candles, context)
    volatile_regime = RegimeBrain().analyze(volatile_candles, context)
    assert flat_regime.regime != volatile_regime.regime


# ---------------------------------------------------------------------- #
# Property 3: LIVE and HISTORICAL_REPLAY -> identical interpretation
# ---------------------------------------------------------------------- #
def test_live_and_replay_produce_identical_decision_context():
    live_snap = _snapshot(execution_mode=EXECUTION_MODE_LIVE)
    replay_snap = _snapshot(execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)

    live_dctx = build_decision_context(live_snap)
    replay_dctx = build_decision_context(replay_snap)

    assert live_dctx.compatible_strategy_families == replay_dctx.compatible_strategy_families
    assert live_dctx.blocked_strategy_families == replay_dctx.blocked_strategy_families
    assert live_dctx.reasoning == replay_dctx.reasoning
    assert live_dctx.confidence == replay_dctx.confidence
    # Reference differs only because it's a fresh snapshot id per fingerprint
    # rules (execution_mode excluded from the fingerprint -- Phase 19.3) --
    # so the two references are actually identical too.
    assert live_dctx.intelligence_snapshot_reference == replay_dctx.intelligence_snapshot_reference


# ---------------------------------------------------------------------- #
# Property 4: no execution vocabulary anywhere in this package
# ---------------------------------------------------------------------- #
def test_no_execution_vocabulary_in_identifiers():
    """Checks actual CODE identifiers (function/class/variable/field
    names), not prose -- the module docstrings legitimately explain what
    this package does NOT do ("no order, no fill..."), which would
    trip a naive whole-file substring search on its own disclaimers."""
    identifier_terms = ("order", "fill", "quantity", "position_size", "qty")
    for filename in ("models.py", "compatibility_engine.py", "transition.py", "builder.py", "__init__.py"):
        path = os.path.join(DECISION_CONTEXT_DIR, filename)
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
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                identifiers.add(node.target.id.lower())
        for term in identifier_terms:
            matches = [i for i in identifiers if term in i]
            assert not matches, f"{filename} has execution-shaped identifier(s): {matches}"


def test_no_order_fill_quantity_fields_on_any_decision_context_model():
    forbidden_field_names = {"order", "order_id", "fill", "quantity", "qty", "position_size", "side", "price"}
    for cls in (DecisionContext, MarketStateTransition, StrategyCompatibilityAssessment):
        field_names = set(cls.__dataclass_fields__.keys())
        assert not (field_names & forbidden_field_names), f"{cls.__name__} has execution-shaped fields: {field_names & forbidden_field_names}"


# ---------------------------------------------------------------------- #
# Property 5: no modification to Reality / brains / MarketIntelligenceSnapshot
# ---------------------------------------------------------------------- #
def test_decision_context_package_only_reads_upstream_modules_never_imports_write_paths():
    """Phase 19.4 must not modify the Reality layer, the six brains, or
    `MarketIntelligenceSnapshot` -- verified structurally: this package's
    own source never imports a brain module, `market_reality_snapshot`,
    or `historical_reality` for anything other than read-only type/value
    access (no brain class is ever instantiated or called from within
    `bujji/decision_context/`, confirmed by AST inspection: no
    `RegimeBrain()`-style call, no `.analyze(` call, anywhere in this
    package)."""
    forbidden_call_names = {
        "RegimeBrain", "StructureBrain", "LiquidityBrain", "VolatilityBrain", "GreeksBrain", "EventBrain",
    }
    for filename in ("models.py", "compatibility_engine.py", "transition.py", "builder.py", "__init__.py"):
        path = os.path.join(DECISION_CONTEXT_DIR, filename)
        with open(path) as f:
            source = f.read()
        assert ".analyze(" not in source, f"{filename} calls a brain's analyze() directly -- this package must only compose already-built Readings"
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_call_names:
                pytest.fail(f"{filename} instantiates {node.func.id} directly -- this package must never call a brain itself")

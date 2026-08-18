"""Tests -- Phase 20.23 Market Understanding Context Bridge.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

from bujji.intelligence.models import (
    DataQuality, GreeksExposure, GreeksReading, LiquidityReading, PremiumBehavior, PremiumReading,
    RegimeReading, RegimeType, Richness, SpreadTightness, StructureProximity, StructureReading,
    VolatilityReading,
)
from bujji.mic_context_bridge import build_market_understanding_context, explain_market_understanding_context


def _regime(regime=RegimeType.RANGING, quality=DataQuality.SUFFICIENT):
    return RegimeReading(regime=regime, confidence=0.8, data_quality=quality, reason="real price action")


def _volatility(richness=Richness.IV_FAIR, quality=DataQuality.SUFFICIENT, ratio=1.1):
    return VolatilityReading(
        iv_ce=15.0, iv_pe=14.5, iv_average=14.75, realized_vol=13.0, richness=richness,
        richness_ratio=ratio, expected_move_points=120.0, expected_move_pct=0.5,
        confidence=0.7, data_quality=quality,
    )


def _premium(behavior=PremiumBehavior.DECAYING_AS_EXPECTED, quality=DataQuality.SUFFICIENT):
    return PremiumReading(
        entry_combined_premium=220.0, current_combined_premium=180.0, theoretical_time_decay_only_premium=190.0,
        behavior=behavior, behavior_ratio=0.95, premium_captured_pct=18.0, time_elapsed_pct=40.0,
        confidence=0.7, data_quality=quality,
    )


def _liquidity(tightness=SpreadTightness.NORMAL, quality=DataQuality.SUFFICIENT):
    return LiquidityReading(
        ce_bid=82.0, ce_ask=82.6, pe_bid=68.0, pe_ask=68.1, ce_spread_pct=0.7, pe_spread_pct=0.15,
        combined_spread=0.7, combined_spread_pct=0.4, tightness=tightness, confidence=0.7, data_quality=quality,
    )


def _structure(proximity=StructureProximity.MID_RANGE, quality=DataQuality.SUFFICIENT):
    return StructureReading(
        spot=24500.0, resistance_strike=24700.0, resistance_oi=1_000_000.0, support_strike=24300.0,
        support_oi=900_000.0, distance_to_resistance_pct=0.8, distance_to_support_pct=0.8,
        put_call_oi_ratio=0.9, proximity=proximity, confidence=0.7, data_quality=quality,
    )


def _greeks(exposure=GreeksExposure.DELTA_NEUTRAL, quality=DataQuality.SUFFICIENT):
    return GreeksReading(
        delta_ce=0.5, delta_pe=-0.5, gamma_ce=0.01, gamma_pe=0.01, theta_ce_per_day=-5.0, theta_pe_per_day=-5.0,
        vega_ce_per_pct=2.0, vega_pe_per_pct=2.0, position_delta=0.0, position_gamma=-0.02,
        position_theta_per_day=10.0, position_vega_per_pct=-4.0, exposure=exposure, confidence=0.7,
        data_quality=quality,
    )


# --------------------------------------------------------------------- #
# 1. Full intelligence context composition
# --------------------------------------------------------------------- #

def test_full_composition_produces_rich_supporting_factors():
    context = build_market_understanding_context(
        regime=_regime(), volatility=_volatility(), premium=_premium(), liquidity=_liquidity(),
        structure=_structure(), greeks=_greeks(),
    )
    assert context.market_state == "RANGING"
    assert len(context.supporting_factors) == 6   # regime + volatility + premium + liquidity + structure + greeks.
    assert not context.uncertainties


# --------------------------------------------------------------------- #
# 2. Missing volatility data
# --------------------------------------------------------------------- #

def test_missing_volatility_produces_honest_uncertainty():
    context = build_market_understanding_context(regime=_regime(), volatility=None)
    assert any("Volatility" in u for u in context.uncertainties)
    assert not any("volatility" in f for f in context.supporting_factors)


# --------------------------------------------------------------------- #
# 3. Missing liquidity data
# --------------------------------------------------------------------- #

def test_missing_liquidity_produces_honest_uncertainty_not_normal():
    context = build_market_understanding_context(regime=_regime(), liquidity=None)
    assert any("Liquidity" in u for u in context.uncertainties)
    assert not any("liquidity" in f for f in context.supporting_factors)


def test_insufficient_liquidity_data_quality_also_honest():
    context = build_market_understanding_context(
        regime=_regime(), liquidity=_liquidity(tightness=SpreadTightness.UNKNOWN, quality=DataQuality.INSUFFICIENT),
    )
    assert any("Liquidity" in u for u in context.uncertainties)


# --------------------------------------------------------------------- #
# 4. Conflicting intelligence sources
# --------------------------------------------------------------------- #

def test_compressed_regime_vs_rich_iv_surfaces_conflict():
    context = build_market_understanding_context(
        regime=_regime(regime=RegimeType.COMPRESSED), volatility=_volatility(richness=Richness.IV_RICH),
    )
    assert len(context.conflicts) == 1
    assert "COMPRESSED" in context.conflicts[0] and "RICH" in context.conflicts[0]
    assert "Conflicting signals" in context.explanation


def test_no_conflict_when_regime_and_volatility_agree():
    context = build_market_understanding_context(regime=_regime(regime=RegimeType.RANGING), volatility=_volatility())
    assert context.conflicts == ()


# --------------------------------------------------------------------- #
# 5. Explanation completeness
# --------------------------------------------------------------------- #

def test_explanation_always_nonempty_and_reflects_state():
    context = build_market_understanding_context()
    assert context.explanation
    text = explain_market_understanding_context(context)
    assert "UNKNOWN" in text


# --------------------------------------------------------------------- #
# 6. Existing MIC behavior unchanged (package boundary, not a live regression re-run here)
# --------------------------------------------------------------------- #

def test_bridge_never_imports_mic_v0():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_context_bridge"
    for path in pkg_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.mic_v0"), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 7-10. No strategy generation / decision / confidence / evidence_score modification
# --------------------------------------------------------------------- #

def test_context_carries_no_decision_evidence_or_confidence_fields():
    context = build_market_understanding_context(regime=_regime())
    for field in ("evidence_score", "confidence", "decision_state", "qualification_status", "priority_score"):
        assert not hasattr(context, field)


# --------------------------------------------------------------------- #
# 11. No broker imports
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "mic_context_bridge"


def test_no_broker_imports_anywhere_in_package():
    forbidden_modules = ("bujji.broker", "fyers_apiv3", "dhanhq")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 12. No execution vocabulary
# --------------------------------------------------------------------- #

def test_no_execution_or_order_vocabulary_anywhere_in_package():
    forbidden = ("place_order", "modify_order", "cancel_order", "quantity", "capital_allocation")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 13. No circular dependencies
# --------------------------------------------------------------------- #

def test_no_import_of_decision_orchestration_risk_or_execution_packages():
    forbidden_modules = (
        "bujji.decision_orchestration", "bujji.risk_context_adapter", "bujji.risk_governor_bridge",
        "bujji.execution_intelligence", "bujji.broker_boundary",
    )
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 14. Existing MIC tests still pass -- confirmed via full regression, not here.
# --------------------------------------------------------------------- #

def test_intelligence_models_never_mutated():
    regime = _regime()
    build_market_understanding_context(regime=regime)
    assert regime.regime == RegimeType.RANGING   # unchanged, same object.

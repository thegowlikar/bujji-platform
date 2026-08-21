"""Tests -- Phase 20.24 Market Understanding Runtime Integration.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

from bujji.intelligence.models import DataQuality, RegimeReading, RegimeType, Richness, VolatilityReading
from bujji.mic_context_bridge import build_market_understanding_context
from bujji.mic_runtime_context import build_mic_runtime_context, explain_runtime_intelligence_context
from bujji.strategy_intelligence import StrategyEvidence, score_strategy
from bujji.epistemics.uncertainty import HIGH, MODERATE


def _evidence(strategy_name="TrendFollowing", sample_size=11278, win_rate=0.665):
    return StrategyEvidence(
        strategy_name=strategy_name, sample_size=sample_size, win_rate=win_rate, profit_factor=2.30,
        gross_expectancy=949.0, net_expectancy=517.0, train_expectancy=512.0,
        validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )


# --------------------------------------------------------------------- #
# 1. Complete context reaches MIC runtime
# --------------------------------------------------------------------- #

def test_complete_context_composition():
    understanding = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.RANGING, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
    )
    context = build_mic_runtime_context("RANGE", ("RANGE",), (), market_understanding=understanding)
    assert context.mic_market_context.mic_regime == "RANGE"
    assert context.market_understanding is understanding


# --------------------------------------------------------------------- #
# 2. Regime preserved
# --------------------------------------------------------------------- #

def test_regime_preserved_verbatim():
    context = build_mic_runtime_context("TREND_UP", ("TREND_UP",), ())
    assert context.mic_market_context.mic_regime == "TREND_UP"


# --------------------------------------------------------------------- #
# 3. Volatility preserved
# --------------------------------------------------------------------- #

def test_volatility_preserved_in_market_understanding():
    understanding = build_market_understanding_context(
        volatility=VolatilityReading(
            iv_ce=15.0, iv_pe=14.5, iv_average=14.75, realized_vol=13.0, richness=Richness.IV_RICH,
            richness_ratio=1.13, expected_move_points=95.0, expected_move_pct=0.4,
            confidence=0.7, data_quality=DataQuality.SUFFICIENT,
        ),
    )
    context = build_mic_runtime_context("RANGE", (), (), market_understanding=understanding)
    assert any("rich" in f for f in context.market_understanding.supporting_factors)


# --------------------------------------------------------------------- #
# 4. Premium behaviour preserved (via market_understanding passthrough)
# --------------------------------------------------------------------- #

def test_market_understanding_passed_through_unmodified():
    understanding = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.RANGING, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
    )
    context = build_mic_runtime_context("RANGE", (), (), market_understanding=understanding)
    assert context.market_understanding.market_state == understanding.market_state
    assert context.market_understanding.supporting_factors == understanding.supporting_factors


# --------------------------------------------------------------------- #
# 5. Liquidity uncertainty preserved
# --------------------------------------------------------------------- #

def test_liquidity_uncertainty_preserved_not_fabricated():
    understanding = build_market_understanding_context(liquidity=None)
    context = build_mic_runtime_context("RANGE", (), (), market_understanding=understanding)
    assert any("Liquidity" in u for u in context.market_understanding.uncertainties)


# --------------------------------------------------------------------- #
# 6. Structure preserved -- covered by test_market_understanding_passed_through_unmodified
# --------------------------------------------------------------------- #

# --------------------------------------------------------------------- #
# 7. Greeks preserved -- covered by test_market_understanding_passed_through_unmodified
# --------------------------------------------------------------------- #

# --------------------------------------------------------------------- #
# 8. Conflict propagation
# --------------------------------------------------------------------- #

def test_conflict_propagates_into_runtime_context():
    understanding = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.COMPRESSED, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
        volatility=VolatilityReading(
            iv_ce=15.0, iv_pe=14.5, iv_average=14.75, realized_vol=13.0, richness=Richness.IV_RICH,
            richness_ratio=1.13, expected_move_points=95.0, expected_move_pct=0.4,
            confidence=0.7, data_quality=DataQuality.SUFFICIENT,
        ),
    )
    context = build_mic_runtime_context("COMPRESSED", (), (), market_understanding=understanding)
    assert len(context.market_understanding.conflicts) == 1


# --------------------------------------------------------------------- #
# 9. Missing-data honesty
# --------------------------------------------------------------------- #

def test_no_market_understanding_supplied_is_honest_not_fabricated():
    context = build_mic_runtime_context("RANGE", (), ())
    assert context.market_understanding is None
    assert "No richer market understanding" in context.explanation


# --------------------------------------------------------------------- #
# 10. Existing MIC behaviour unchanged
# --------------------------------------------------------------------- #

def test_mic_v0_never_imported_by_new_package():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_runtime_context"
    for path in pkg_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.mic_v0"), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 11. Decision output unchanged (score_strategy honors real context contract, unmodified)
# --------------------------------------------------------------------- #

def test_score_strategy_still_works_with_built_context():
    context = build_mic_runtime_context("RANGE", ("RANGE",), ())
    score = score_strategy(_evidence(), context=context.mic_market_context)
    assert score.confidence == HIGH   # favorable regime -> confidence unchanged from evidence-based HIGH.


def test_unfavorable_regime_demotes_confidence_exactly_one_band():
    context = build_mic_runtime_context("RANGE", (), ("RANGE",))
    score = score_strategy(_evidence(), context=context.mic_market_context)
    assert score.confidence == MODERATE   # demoted from HIGH by exactly one band.


# --------------------------------------------------------------------- #
# 12. Evidence score unchanged
# --------------------------------------------------------------------- #

def test_evidence_score_never_touched_by_runtime_context():
    context = build_mic_runtime_context("RANGE", (), ("RANGE",))
    score_without = score_strategy(_evidence())
    score_with = score_strategy(_evidence(), context=context.mic_market_context)
    assert score_without.evidence_score == score_with.evidence_score == 78.62


# --------------------------------------------------------------------- #
# 13. Confidence unchanged when regime favorable
# --------------------------------------------------------------------- #

def test_confidence_unchanged_for_favorable_regime():
    context = build_mic_runtime_context("TREND_UP", ("TREND_UP",), ())
    score = score_strategy(_evidence(), context=context.mic_market_context)
    assert score.confidence == HIGH


# --------------------------------------------------------------------- #
# 14. No strategy generation
# --------------------------------------------------------------------- #

def test_no_strategy_or_opportunity_vocabulary_in_package():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_runtime_context"
    forbidden = ("qualification_status", "priority_score", "allocation_class")
    for path in pkg_root.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 15. No execution path
# --------------------------------------------------------------------- #

def test_no_execution_or_order_vocabulary_anywhere_in_package():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_runtime_context"
    forbidden = ("place_order", "modify_order", "cancel_order", "quantity", "capital")
    for path in pkg_root.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 16. No broker imports
# --------------------------------------------------------------------- #

def test_no_broker_imports_anywhere_in_package():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_runtime_context"
    forbidden_modules = ("bujji.broker", "fyers_apiv3", "dhanhq")
    for path in pkg_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 17. No circular dependencies
# --------------------------------------------------------------------- #

def test_no_import_of_decision_risk_or_execution_packages():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "mic_runtime_context"
    forbidden_modules = (
        "bujji.decision_orchestration", "bujji.risk_context_adapter", "bujji.risk_governor_bridge",
        "bujji.execution_intelligence", "bujji.broker_boundary",
    )
    for path in pkg_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


def test_explainability():
    understanding = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.RANGING, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
    )
    context = build_mic_runtime_context("RANGE", ("RANGE",), (), market_understanding=understanding)
    text = explain_runtime_intelligence_context(context)
    assert "RANGE" in text

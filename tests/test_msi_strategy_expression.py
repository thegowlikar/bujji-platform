"""Tests for bujji.msi_strategy_expression — Engineering Series 93."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_strategy_expression import config as se_config
from bujji.msi_strategy_expression import engine, taxonomy
from bujji.msi_strategy_expression.journal import StrategyExpressionJournal
from bujji.msi_strategy_expression.runner import derive_expressions_batch, StrategyExpressionStream
from bujji.msi_strategy_expression import query as se_query
from bujji.msi_strategy_expression import serialization as se_serialization
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.msi_trade_thesis import taxonomy as thesis_taxonomy
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

TS = "2026-05-25T15:30:00+05:30"


def _thesis(thesis_type, direction="STRONG_BULLISH", **overrides):
    exp = ThesisExplanation(assessment_id="tx", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                             what_would_invalidate=(), schema_version="1.0.0")
    base = dict(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.0,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation=direction,
        conviction="HIGH", invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )
    base.update(overrides)
    return TradeThesisAssessment(**base)


# --- Deliverable 4: exactly one expression per thesis --------------------

def test_every_real_thesis_type_produces_an_expression():
    for tt in thesis_taxonomy.ALL_THESIS_TYPES:
        a = engine.derive_strategy_expression(_thesis(tt), timestamp=TS)
        assert a.thesis.thesis_type == tt


def test_no_trade_thesis_produces_no_compatible_families():
    a = engine.derive_strategy_expression(_thesis(taxonomy_no_trade := thesis_taxonomy.THESIS_NO_TRADE), timestamp=TS)
    assert a.compatible_strategy_families == ()
    assert a.incompatible_strategy_families == ()


# --- Deliverable 7: one thesis, multiple acceptable expressions ----------

def test_trend_continuation_allows_long_directional_and_requires_defined_risk_positive_convexity():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    assert taxonomy.CHAR_DIRECTIONAL in a.required_characteristics
    assert taxonomy.CHAR_DEFINED_RISK in a.required_characteristics
    assert taxonomy.CHAR_POSITIVE_CONVEXITY in a.required_characteristics
    assert "LONG_DIRECTIONAL" in a.compatible_strategy_families
    # Undefined-risk, negative-convexity families (e.g. a naked short strangle
    # analog) must be rejected for this thesis.
    assert "NEUTRAL_PREMIUM_SELLING" in a.incompatible_strategy_families
    assert "SHORT_DIRECTIONAL" in a.incompatible_strategy_families


def test_range_persistence_allows_multiple_families_including_undefined_risk_short_strangle_analog():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_RANGE_PERSISTENCE, direction="NEUTRAL"), timestamp=TS)
    assert len(a.compatible_strategy_families) >= 3
    assert "IRON_CONDOR" in a.compatible_strategy_families
    assert "IRON_FLY" in a.compatible_strategy_families
    assert "NEUTRAL_PREMIUM_SELLING" in a.compatible_strategy_families  # the short-strangle analog.
    assert "LONG_DIRECTIONAL" in a.incompatible_strategy_families  # directional, contradicts range persistence.


def test_volatility_expansion_maps_to_multiple_long_volatility_families():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_VOLATILITY_EXPANSION, direction="NEUTRAL"), timestamp=TS)
    for fam in ("VOLATILITY_EXPANSION", "NEUTRAL_PREMIUM_BUYING", "LONG_DIRECTIONAL", "CALENDAR"):
        assert fam in a.compatible_strategy_families
    assert "VOLATILITY_COMPRESSION" in a.incompatible_strategy_families


def test_incompatible_reasons_cite_the_actual_contradicting_characteristic():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    reasons_text = " ".join(a.explanation.why_families_incompatible)
    assert "NEUTRAL_PREMIUM_SELLING" in reasons_text
    assert ("NEGATIVE_CONVEXITY" in reasons_text) or ("UNDEFINED_RISK" in reasons_text)


# --- Deliverable 3: characteristics describe exposure, not named strategies

def test_family_characteristics_table_covers_every_ssf_family():
    assert set(se_config.FAMILY_CHARACTERISTICS) == set(ssf_taxonomy.ALL_STRATEGY_FAMILIES)


def test_no_family_characteristic_set_is_empty():
    for fam, chars in se_config.FAMILY_CHARACTERISTICS.items():
        assert chars, f"{fam} has no declared characteristics"


# --- Determinism / immutability / batch-streaming parity -----------------

def test_determinism_identical_input_identical_id():
    a1 = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    a2 = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    assert a1.assessment_id == a2.assessment_id


def test_assessment_is_immutable():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    with pytest.raises(Exception):
        a.desired_direction = "X"


def test_batch_and_streaming_are_byte_identical():
    requests = [
        dict(thesis=_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS),
        dict(thesis=_thesis(thesis_taxonomy.THESIS_RANGE_PERSISTENCE, direction="NEUTRAL"), timestamp=TS),
    ]
    batch = derive_expressions_batch(requests)
    stream = StrategyExpressionStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query ------------------------------------------------

def test_serialization_round_trip():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    d = se_serialization.assessment_to_dict(a)
    assert d["assessment_id"] == a.assessment_id
    assert "LONG_DIRECTIONAL" in d["compatible_strategy_families"]


def test_query_helpers():
    a1 = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    a2 = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_RANGE_PERSISTENCE, direction="NEUTRAL"), timestamp=TS)
    assessments = (a1, a2)
    assert se_query.by_id(assessments, a1.assessment_id) is a1
    assert a1 in se_query.by_compatible_family(assessments, "LONG_DIRECTIONAL")


def test_journal_is_append_only():
    a = engine.derive_strategy_expression(_thesis(thesis_taxonomy.THESIS_TREND_CONTINUATION), timestamp=TS)
    j = StrategyExpressionJournal()
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) -------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_strategy_expression")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.msi_strategy_selector", "bujji.msi_trade_construction",
                         "bujji.msi_portfolio_construction")
    for path in _pkg_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name.startswith(m) for m in forbidden_modules), f"{path} imports {name}"


def test_ast_no_optimization_vocabulary_or_uuid4_or_randomness():
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in ("optimi", "backtest", "pnl", "rank", "score"):
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

"""Tests for bujji.msi_trade_thesis — Engineering Series 92."""
from __future__ import annotations

import ast
import os
from types import SimpleNamespace

import pytest

from bujji.msi_trade_thesis import engine, taxonomy
from bujji.msi_trade_thesis.journal import TradeThesisJournal
from bujji.msi_trade_thesis.runner import derive_theses_batch, TradeThesisStream
from bujji.msi_trade_thesis import query as thesis_query
from bujji.msi_trade_thesis import serialization as thesis_serialization

TS = "2026-05-25T15:30:00+05:30"


def _psi(**overrides):
    base = dict(structure_state="UNKNOWN", trend_state="NO_TREND", compression_state="NOT_DETECTED",
                expansion_state="NOT_DETECTED", structure_integrity="COHERENT")
    base.update(overrides)
    return SimpleNamespace(**base)


def _mssi(**overrides):
    base = dict(breakout_state="NONE", breakdown_state="NONE", structural_balance="UNKNOWN",
                structure_location="UNKNOWN")
    base.update(overrides)
    return SimpleNamespace(**base)


def _mdi(**overrides):
    base = dict(overall_direction="NEUTRAL")
    base.update(overrides)
    return SimpleNamespace(**base)


def _mppi(**overrides):
    base = dict(positioning_bias="NEUTRAL_POSITIONING")
    base.update(overrides)
    return SimpleNamespace(**base)


def _vsb(**overrides):
    base = dict(expansion_state="NOT_DETECTED", compression_state="NOT_DETECTED",
                volatility_regime="STABLE", expected_move_pct=1.1, confidence="MODERATE")
    base.update(overrides)
    return SimpleNamespace(**base)


def _consensus(**overrides):
    base = dict(consensus_level="MODERATE_CONSENSUS")
    base.update(overrides)
    return SimpleNamespace(**base)


_UNSET = object()


def _derive(psi=None, mssi=None, mdi=None, mppi=None, vsb=_UNSET, consensus=None):
    return engine.derive_trade_thesis(
        psi or _psi(), mssi or _mssi(), mdi or _mdi(), mppi or _mppi(),
        _vsb() if vsb is _UNSET else vsb, consensus or _consensus(), timestamp=TS,
    )


# --- Deliverable 3/4: thesis derivation, one per case --------------------

def test_market_structure_breakout_confirmed_wins():
    a = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    assert a.thesis_type == taxonomy.THESIS_BREAKOUT


def test_market_structure_failed_breakout_wins():
    a = _derive(mssi=_mssi(breakout_state="FAILED"))
    assert a.thesis_type == taxonomy.THESIS_FAILED_BREAKOUT


def test_mean_reversion_from_range_bound_near_support():
    a = _derive(mssi=_mssi(structural_balance="RANGE_BOUND", structure_location="NEAR_SUPPORT"))
    assert a.thesis_type == taxonomy.THESIS_MEAN_REVERSION


def test_range_persistence_from_range_bound_no_location():
    a = _derive(mssi=_mssi(structural_balance="RANGE_BOUND"))
    assert a.thesis_type == taxonomy.THESIS_RANGE_PERSISTENCE


def test_volatility_expansion_from_vsb_when_no_structure_signal():
    a = _derive(vsb=_vsb(expansion_state="CONFIRMED"))
    assert a.thesis_type == taxonomy.THESIS_VOLATILITY_EXPANSION


def test_volatility_compression_from_vsb():
    a = _derive(vsb=_vsb(compression_state="CONFIRMED"))
    assert a.thesis_type == taxonomy.THESIS_VOLATILITY_COMPRESSION


def test_trend_continuation_from_psi_when_no_mssi_or_vsb_signal():
    a = _derive(psi=_psi(structure_state="TRENDING", trend_state="ESTABLISHED_TREND"))
    assert a.thesis_type == taxonomy.THESIS_TREND_CONTINUATION


def test_trend_reversal_from_weakening_trend():
    a = _derive(psi=_psi(structure_state="TRENDING", trend_state="WEAKENING_TREND"))
    assert a.thesis_type == taxonomy.THESIS_TREND_REVERSAL


def test_no_trade_when_no_domain_votes():
    a = _derive()
    assert a.thesis_type == taxonomy.THESIS_NO_TRADE
    assert a.conviction == taxonomy.CONVICTION_NONE


def test_event_risk_when_structure_conflicted_and_multiple_distinct_votes():
    a = _derive(
        psi=_psi(structure_integrity="CONFLICTED", structure_state="TRENDING", trend_state="ESTABLISHED_TREND"),
        vsb=_vsb(expansion_state="CONFIRMED"),
    )
    assert a.thesis_type == taxonomy.THESIS_EVENT_RISK


def test_market_structure_takes_priority_over_volatility_and_price_structure():
    a = _derive(
        mssi=_mssi(breakout_state="CONFIRMED"),
        vsb=_vsb(compression_state="CONFIRMED"),
        psi=_psi(structure_state="TRENDING", trend_state="ESTABLISHED_TREND"),
    )
    assert a.thesis_type == taxonomy.THESIS_BREAKOUT


# --- Deliverable 5: multiple strategy families can satisfy one thesis ---
# (demonstrated structurally: this package makes no reference whatsoever
# to strategy families -- verified by the AST vocabulary test below --
# so by construction, any number of downstream families may map to one
# thesis without this package needing to know about any of them.)

def test_thesis_never_references_a_strategy_family_name():
    a = _derive(vsb=_vsb(expansion_state="CONFIRMED"))
    assert a.thesis_type == taxonomy.THESIS_VOLATILITY_EXPANSION
    text = a.market_expectation + " ".join(a.explanation.why_this_thesis)
    for family in ("LONG_STRADDLE", "LONG_STRANGLE", "LONG_CALL", "LONG_PUT", "DEBIT_SPREAD", "IRON_CONDOR"):
        assert family not in text.upper()


# --- directional pass-through and conflict detection --------------------

def test_directional_expectation_is_real_mdi_passthrough():
    a = _derive(mdi=_mdi(overall_direction="STRONG_BEARISH"), psi=_psi(structure_state="TRENDING", trend_state="ESTABLISHED_TREND"))
    assert a.directional_expectation == "STRONG_BEARISH"


def test_participant_positioning_conflict_detected_for_directional_thesis():
    a = _derive(
        psi=_psi(structure_state="TRENDING", trend_state="ESTABLISHED_TREND"),
        mdi=_mdi(overall_direction="BULLISH"),
        mppi=_mppi(positioning_bias="BEARISH_POSITIONING"),
    )
    assert taxonomy.DOMAIN_PARTICIPANT_POSITIONING in a.conflicting_domains


def test_mixed_direction_disclosed_in_invalidation_for_directional_thesis():
    a = _derive(psi=_psi(structure_state="TRENDING", trend_state="ESTABLISHED_TREND"), mdi=_mdi(overall_direction="MIXED"))
    assert any("mixed" in cond.lower() for cond in a.invalidation_conditions)


def test_volatility_expectation_always_reported_regardless_of_thesis_type():
    a = _derive(mssi=_mssi(breakout_state="CONFIRMED"), vsb=_vsb(volatility_regime="STABLE"))
    assert a.volatility_expectation == taxonomy.VOL_EXPECTATION_STABLE


def test_expected_move_is_real_vsb_passthrough():
    a = _derive(vsb=_vsb(expected_move_pct=2.5))
    assert a.expected_move == 2.5


def test_vsb_none_handled_honestly():
    a = _derive(vsb=None)
    assert a.volatility_expectation == taxonomy.VOL_EXPECTATION_UNKNOWN
    assert a.expected_move is None


# --- Deliverable 7: explainability ---------------------------------------

def test_explanation_answers_all_required_questions():
    a = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    assert a.explanation.why_this_thesis
    assert a.explanation.supporting_evidence or a.thesis_type == taxonomy.THESIS_NO_TRADE
    assert a.explanation.what_would_invalidate


def test_reasoning_never_cites_historical_performance():
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit")
    for a in (_derive(), _derive(mssi=_mssi(breakout_state="CONFIRMED")), _derive(vsb=_vsb(expansion_state="CONFIRMED"))):
        text = " ".join(a.explanation.why_this_thesis + a.explanation.supporting_evidence + a.invalidation_conditions).lower()
        for word in forbidden:
            assert word not in text


# --- Deliverable 6: determinism, byte-identical rerun --------------------

def test_determinism_identical_input_identical_id():
    a1 = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    a2 = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    assert a1.assessment_id == a2.assessment_id


def test_assessment_is_immutable():
    a = _derive()
    with pytest.raises(Exception):
        a.thesis_type = taxonomy.THESIS_BREAKOUT


def test_batch_and_streaming_are_byte_identical():
    requests = [
        dict(psi=_psi(), mssi=_mssi(breakout_state="CONFIRMED"), mdi=_mdi(), mppi=_mppi(), vsb=_vsb(), consensus=_consensus(), timestamp=TS),
        dict(psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), vsb=_vsb(expansion_state="CONFIRMED"), consensus=_consensus(), timestamp=TS),
    ]
    batch = derive_theses_batch(requests)
    stream = TradeThesisStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query ------------------------------------------------

def test_serialization_round_trip():
    a = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    d = thesis_serialization.assessment_to_dict(a)
    assert d["assessment_id"] == a.assessment_id
    assert d["thesis_type"] == taxonomy.THESIS_BREAKOUT


def test_query_helpers():
    a1 = _derive(mssi=_mssi(breakout_state="CONFIRMED"))
    a2 = _derive()
    assessments = (a1, a2)
    assert thesis_query.by_id(assessments, a1.assessment_id) is a1
    assert thesis_query.by_thesis_type(assessments, taxonomy.THESIS_BREAKOUT) == (a1,)
    assert thesis_query.by_thesis_type(assessments, taxonomy.THESIS_NO_TRADE) == (a2,)


def test_journal_is_append_only():
    a = _derive()
    j = TradeThesisJournal()
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) -------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_trade_thesis")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain",
                         "fyers_apiv3", "bujji.msi_strategy_selection_foundation",
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


def test_ast_no_strategy_family_or_optimization_vocabulary():
    forbidden_fragments = ("optimi", "backtest", "pnl", "strategyfamily", "strikeselect",
                           "longstraddle", "longstrangle", "ironcondor", "bullcall")
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in forbidden_fragments:
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"

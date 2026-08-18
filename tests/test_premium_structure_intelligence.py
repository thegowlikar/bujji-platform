"""Tests -- Phase 20.31 Premium Structure Intelligence Layer v1.

No broker, no live data, no systemd. Real dataclass construction of
upstream assessments (same pattern tests/test_msi_position_construction.py
already uses for its own upstream fixtures) -- these are real MSI
model types, directly instantiated for test control, not fabricated
shapes."""
from __future__ import annotations

from pathlib import Path

import pytest

from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.msi_market_direction import taxonomy as mdi_taxonomy
from bujji.msi_market_direction.models import Explanation as MdiExplanation, MarketDirectionAssessment
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_market_structure.models import Explanation as MssiExplanation, MarketStructureAssessment
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_price_structure.models import Explanation as PsiExplanation, PriceStructureAssessment
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation, VolatilityStructureAssessment
from bujji.premium_structure_intelligence import (
    STRUCTURE_IRON_CONDOR, STRUCTURE_IRON_FLY, STRUCTURE_NO_TRADE, STRUCTURE_SHORT_STRANGLE,
    explain_structure_selection, select_structure,
)
from bujji.premium_structure_intelligence.models import StructureSelectionAssessment
from bujji.premium_structure_intelligence import taxonomy as psi_own_taxonomy

TS = "2026-05-25T15:30:00+05:30"


def _mdi(direction=mdi_taxonomy.NEUTRAL, confidence="MODERATE"):
    exp = MdiExplanation(assessment_id="m1", which_lenses_participated=(), which_bullish=(),
                          which_bearish=(), which_neutral_or_unknown=(), per_lens_evidence=(),
                          why_not_a_simple_vote="", schema_version="1.0.0")
    return MarketDirectionAssessment(
        assessment_id="MDA-1", timestamp=TS, overall_direction=direction, overall_confidence=confidence,
        participating_lenses=(), conflicting_lenses=(), supporting_assessment_ids=(), explanation=exp,
        provenance="p", schema_version="1.0.0",
    )


def _psi(structure_state=psi_taxonomy.STRUCTURE_BALANCE, compression_state=psi_taxonomy.COMPRESSION_NOT_DETECTED,
          confidence="MODERATE"):
    exp = PsiExplanation(assessment_id="p1", what_changed=None, why=(), which_episodes_caused_it=(),
                          which_observations_support_it=(), missing_evidence=(), would_increase_confidence=(),
                          schema_version="1.0.0")
    return PriceStructureAssessment(
        assessment_id="PSA-1", timestamp=TS, structure_state=structure_state, trend_state="NO_TREND",
        swing_state="UNKNOWN", compression_state=compression_state, expansion_state="NOT_DETECTED",
        balance_state="IN_BALANCE", structure_integrity="INTACT", confidence=confidence,
        supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(),
        contradictions=(), explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _mssi(structure_location=mssi_taxonomy.LOCATION_INSIDE_RANGE, confidence="MODERATE"):
    exp = MssiExplanation(assessment_id="ms1", what_changed=None, why=(), which_episodes_caused_it=(),
                           which_observations_support_it=(), missing_evidence=(), would_increase_confidence=(),
                           schema_version="1.0.0")
    return MarketStructureAssessment(
        assessment_id="MSA-1", timestamp=TS, structure_location=structure_location, support_state="UNKNOWN",
        resistance_state="UNKNOWN", breakout_state="UNKNOWN", breakdown_state="UNKNOWN", retest_state="UNKNOWN",
        rejection_state="UNKNOWN", structural_balance=mssi_taxonomy.STRUCTURAL_BALANCE_RANGE_BOUND,
        confidence=confidence, supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(),
        contradictions=(), explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _vsb(iv_state=vsb_taxonomy.IV_RICH, expansion_state="NOT_DETECTED", compression_state="NOT_DETECTED"):
    exp = VsbExplanation(assessment_id="v1", why=(), missing_evidence=(), would_increase_confidence=(),
                          schema_version="1.0.0")
    return VolatilityStructureAssessment(
        assessment_id="VSB-1", timestamp=TS, volatility_regime="STABLE", iv_state=iv_state,
        expected_move_state="MODERATE", skew_state="UNKNOWN", term_structure_state="UNKNOWN",
        expansion_state=expansion_state, compression_state=compression_state, confidence="MODERATE",
        iv_average=0.12, realized_vol=0.10, expected_move_pct=1.2, explanation=exp, provenance="p",
        schema_version="1.0.0",
    )


def _liquidity(tightness=SpreadTightness.NORMAL):
    from datetime import datetime, timezone
    return LiquidityReading(
        ce_bid=99.0, ce_ask=101.0, pe_bid=88.0, pe_ask=90.0,
        ce_spread_pct=2.0, pe_spread_pct=2.2, combined_spread=4.0, combined_spread_pct=2.1,
        tightness=tightness, confidence=0.8, reason="",
        as_of=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------- #
# Scenario A -- stable range: SHORT_STRANGLE
# --------------------------------------------------------------------- #

def test_scenario_a_stable_range_selects_short_strangle():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_NOT_DETECTED),
        _mssi(mssi_taxonomy.LOCATION_INSIDE_RANGE), _vsb(vsb_taxonomy.IV_RICH, "NOT_DETECTED"),
        timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_SHORT_STRANGLE
    assert result.data_quality == psi_own_taxonomy.DATA_QUALITY_SUFFICIENT


# --------------------------------------------------------------------- #
# Scenario B -- compressed range: IRON_FLY
# --------------------------------------------------------------------- #

def test_scenario_b_compressed_range_selects_iron_fly():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_CONFIRMED),
        _mssi(mssi_taxonomy.LOCATION_INSIDE_RANGE), _vsb(vsb_taxonomy.IV_RICH, "NOT_DETECTED"),
        timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_IRON_FLY


# --------------------------------------------------------------------- #
# Scenario C -- defined range: IRON_CONDOR
# --------------------------------------------------------------------- #

def test_scenario_c_defined_range_selects_iron_condor():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_NOT_DETECTED),
        _mssi(mssi_taxonomy.LOCATION_NEAR_SUPPORT, confidence="HIGH"), _vsb(vsb_taxonomy.IV_CHEAP, "NOT_DETECTED"),
        timestamp=TS,
    )
    # IRON_FLY requires COMPRESSION_CONFIRMED -- absent here, so it must fall through to IRON_CONDOR.
    assert result.selected_structure == STRUCTURE_IRON_CONDOR


def test_iron_condor_with_liquidity_note_included():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_NOT_DETECTED),
        _mssi(mssi_taxonomy.LOCATION_NEAR_RESISTANCE, confidence="HIGH"), _vsb(vsb_taxonomy.IV_RICH, "NOT_DETECTED"),
        liquidity=_liquidity(SpreadTightness.TIGHT), timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_IRON_CONDOR
    assert any("liquidity_tightness=TIGHT" in r for r in result.reasons)


# --------------------------------------------------------------------- #
# Scenario D -- bad environment: NO_TRADE
# --------------------------------------------------------------------- #

def test_scenario_d_directional_trend_selects_no_trade():
    result = select_structure(
        _mdi(mdi_taxonomy.MIXED), _psi(psi_taxonomy.STRUCTURE_BALANCE), _mssi(), _vsb(),
        timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_NO_TRADE
    assert psi_own_taxonomy.REJECT_NOT_NEUTRAL in result.reasons[0]


def test_scenario_d_volatility_expanding_selects_no_trade():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE), _mssi(),
        _vsb(vsb_taxonomy.IV_RICH, expansion_state="CONFIRMED"),
        timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_NO_TRADE
    assert psi_own_taxonomy.REJECT_VOLATILITY_EXPANDING in result.reasons[0]


# --------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------- #

def test_insufficient_evidence_never_guesses():
    result = select_structure(None, None, None, timestamp=TS)
    assert result.selected_structure == STRUCTURE_NO_TRADE
    assert result.data_quality == psi_own_taxonomy.DATA_QUALITY_INSUFFICIENT
    assert result.confidence == psi_own_taxonomy.CONFIDENCE_NONE


def test_neutral_but_nothing_matches_falls_to_no_trade_not_forced():
    # BALANCE state absent, and range is not bounded either -- no
    # candidate's own condition set is satisfied.
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi("TRENDING", "NOT_DETECTED"),
        _mssi(mssi_taxonomy.LOCATION_ABOVE_RESISTANCE), _vsb(vsb_taxonomy.IV_CHEAP, "NOT_DETECTED"),
        timestamp=TS,
    )
    assert result.selected_structure == STRUCTURE_NO_TRADE
    assert psi_own_taxonomy.REJECT_NO_STRUCTURE_MATCHED in result.reasons[0]


def test_missing_vsb_does_not_crash_and_is_honestly_reflected():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE), _mssi(),
        vsb=None, timestamp=TS,
    )
    # Neither IRON_FLY nor SHORT_STRANGLE can match without a real vsb reading.
    assert result.selected_structure in (STRUCTURE_NO_TRADE, STRUCTURE_IRON_CONDOR)


def test_rejected_structures_always_populated_when_not_no_trade():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_NOT_DETECTED),
        _mssi(mssi_taxonomy.LOCATION_INSIDE_RANGE), _vsb(vsb_taxonomy.IV_RICH, "NOT_DETECTED"),
        timestamp=TS,
    )
    assert len(result.rejected_structures) == 2  # the two non-winning real candidates


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #

def test_deterministic_same_input_same_output():
    args = (_mdi(), _psi(), _mssi(), _vsb())
    a = select_structure(*args, timestamp=TS)
    b = select_structure(*args, timestamp=TS)
    assert a == b


# --------------------------------------------------------------------- #
# Explainability
# --------------------------------------------------------------------- #

def test_explain_output_nonempty_and_mentions_structure():
    result = select_structure(
        _mdi(mdi_taxonomy.NEUTRAL), _psi(psi_taxonomy.STRUCTURE_BALANCE, psi_taxonomy.COMPRESSION_CONFIRMED),
        _mssi(), _vsb(vsb_taxonomy.IV_RICH, "NOT_DETECTED"), timestamp=TS,
    )
    text = explain_structure_selection(result)
    assert "IRON_FLY" in text
    assert "Confidence:" in text


# --------------------------------------------------------------------- #
# No generic "score" field (explicit instruction, same discipline as
# Phase 20.27's microstructure_intelligence)
# --------------------------------------------------------------------- #

def test_no_generic_score_field():
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(StructureSelectionAssessment)}
    assert "score" not in field_names


# --------------------------------------------------------------------- #
# Safety: no msi_strategy_selector duplication, no broker/execution,
# msi_trade_construction and msi_position_construction untouched.
# --------------------------------------------------------------------- #

def test_no_broker_or_execution_imports():
    pkg_dir = Path(__file__).resolve().parent.parent / "bujji" / "premium_structure_intelligence"
    forbidden = ("import bujji.broker", "from bujji.broker", "place_order", "modify_order", "cancel_order",
                 "import bujji.msi_strategy_selector", "from bujji.msi_strategy_selector")
    for py_file in pkg_dir.glob("*.py"):
        source = py_file.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {py_file.name}"


def test_msi_trade_construction_still_byte_identical_to_baseline():
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39", "--", "bujji/msi_trade_construction/"],
        cwd=repo_root, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    assert changed == [], f"msi_trade_construction was modified, expected untouched: {changed}"

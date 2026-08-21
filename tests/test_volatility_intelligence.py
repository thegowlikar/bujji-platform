"""Tests for Volatility Intelligence -- Trading Brain Intelligence
Upgrade, Phase 2. Mirrors tests/test_strategy_evaluator.py's own
conventions (helper builders for real dataclasses, class-grouped
tests)."""
from __future__ import annotations

import pytest

from bujji.mic_v0.volatility_classifier import MIN_WINDOW
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation, VolatilityStructureAssessment
from bujji.volatility_intelligence import assess, taxonomy

TS = "2026-01-01T09:15:00"


def _vsb(**overrides):
    base = dict(
        assessment_id="VSB-0000000000000001", timestamp=TS,
        volatility_regime="STABLE", iv_state="IV_RICH", expected_move_state="MODERATE",
        skew_state="UNKNOWN", term_structure_state="UNKNOWN",
        expansion_state="NOT_DETECTED", compression_state="NOT_DETECTED",
        confidence="HIGH", iv_average=0.18, realized_vol=0.12, expected_move_pct=1.2,
        explanation=VsbExplanation(
            assessment_id="VSB-0000000000000001", why=(), missing_evidence=(),
            would_increase_confidence=(), schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return VolatilityStructureAssessment(**base)


_VIX_HISTORY = [float(v) for v in range(10, 10 + MIN_WINDOW)]  # 10..69, MIN_WINDOW samples
_VIX_HIGH = 68.0    # top of the range -> HIGH percentile
_VIX_LOW = 11.0     # bottom of the range -> LOW percentile
_VIX_MID = 10.0 + MIN_WINDOW / 2  # -> NORMAL percentile


# ---------------------------------------------------------------------------
# 1. Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_iv_states(self):
        assert taxonomy.ALL_IV_STATES == ("CHEAP", "FAIR", "RICH", "UNKNOWN")

    def test_iv_rank_states(self):
        assert taxonomy.ALL_IV_RANK_STATES == ("LOW", "NORMAL", "HIGH", "UNKNOWN")

    def test_volatility_states(self):
        assert taxonomy.ALL_VOLATILITY_STATES == ("LOW", "NORMAL", "ELEVATED", "UNKNOWN")

    def test_volatility_regimes(self):
        assert taxonomy.ALL_VOLATILITY_REGIMES == ("EXPANDING", "CONTRACTING", "STABLE", "UNKNOWN")

    def test_expected_move_states(self):
        assert taxonomy.ALL_EXPECTED_MOVE_STATES == ("LOW", "NORMAL", "HIGH", "UNKNOWN")

    def test_volatility_qualities(self):
        assert taxonomy.ALL_VOLATILITY_QUALITIES == ("STRONG", "MODERATE", "WEAK", "UNKNOWN")

    def test_confidence_levels(self):
        assert taxonomy.ALL_CONFIDENCE_LEVELS == ("NONE", "LOW", "MODERATE", "HIGH")


# ---------------------------------------------------------------------------
# 2. VSB-only input -- works with UNKNOWN IV rank
# ---------------------------------------------------------------------------

class TestVsbOnly:
    def test_vsb_only_produces_real_iv_state_and_unknown_rank(self):
        result = assess(volatility_structure=_vsb(iv_state="IV_RICH"), timestamp=TS)
        assert result.iv_state == taxonomy.IV_RICH
        assert result.iv_rank_state == taxonomy.IV_RANK_UNKNOWN
        assert result.volatility_quality == taxonomy.QUALITY_MODERATE
        assert result.confidence == taxonomy.CONFIDENCE_MODERATE

    def test_vsb_only_derives_volatility_state_and_regime(self):
        result = assess(volatility_structure=_vsb(volatility_regime="HIGH_VOLATILITY"), timestamp=TS)
        assert result.volatility_state == taxonomy.VOLATILITY_ELEVATED

    def test_vsb_only_derives_expected_move(self):
        result = assess(volatility_structure=_vsb(expected_move_state="WIDE"), timestamp=TS)
        assert result.expected_move_state == taxonomy.EXPECTED_MOVE_HIGH


# ---------------------------------------------------------------------------
# 3. VIX-only input -- works with UNKNOWN VSB fields
# ---------------------------------------------------------------------------

class TestVixOnly:
    def test_vix_only_produces_real_rank_and_unknown_vsb_fields(self):
        result = assess(current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS)
        assert result.iv_rank_state == taxonomy.IV_RANK_HIGH
        assert result.iv_state == taxonomy.IV_UNKNOWN
        assert result.volatility_state == taxonomy.VOLATILITY_UNKNOWN
        assert result.volatility_regime == taxonomy.REGIME_UNKNOWN
        assert result.expected_move_state == taxonomy.EXPECTED_MOVE_UNKNOWN
        assert result.volatility_quality == taxonomy.QUALITY_MODERATE
        assert result.supporting_assessment_ids == ()

    def test_vix_evidence_discloses_index_level_caveat(self):
        result = assess(current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS)
        joined = " ".join(result.reasons)
        assert "index-level" in joined


# ---------------------------------------------------------------------------
# 4. Both aligned -- strong volatility evidence
# ---------------------------------------------------------------------------

class TestAlignedEvidence:
    def test_iv_rich_and_high_vix_percentile_is_strong(self):
        result = assess(
            volatility_structure=_vsb(iv_state="IV_RICH"),
            current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS,
        )
        assert result.volatility_quality == taxonomy.QUALITY_STRONG
        assert result.confidence == taxonomy.CONFIDENCE_HIGH

    def test_iv_cheap_and_low_vix_percentile_is_strong(self):
        result = assess(
            volatility_structure=_vsb(iv_state="IV_CHEAP"),
            current_vix=_VIX_LOW, trailing_vix_history=_VIX_HISTORY, timestamp=TS,
        )
        assert result.volatility_quality == taxonomy.QUALITY_STRONG


# ---------------------------------------------------------------------------
# 5. Conflicting evidence -- reduced confidence, never averaged
# ---------------------------------------------------------------------------

class TestConflictingEvidence:
    def test_iv_rich_and_low_vix_percentile_is_weak(self):
        result = assess(
            volatility_structure=_vsb(iv_state="IV_RICH"),
            current_vix=_VIX_LOW, trailing_vix_history=_VIX_HISTORY, timestamp=TS,
        )
        assert result.volatility_quality == taxonomy.QUALITY_WEAK
        assert result.confidence == taxonomy.CONFIDENCE_LOW
        joined = " ".join(result.reasons)
        assert "DISAGREE" in joined


# ---------------------------------------------------------------------------
# 6. Missing evidence -- UNKNOWN everywhere, never guessed
# ---------------------------------------------------------------------------

class TestMissingEvidence:
    def test_nothing_supplied_is_fully_unknown(self):
        result = assess(timestamp=TS)
        assert result.iv_state == taxonomy.IV_UNKNOWN
        assert result.iv_rank_state == taxonomy.IV_RANK_UNKNOWN
        assert result.volatility_state == taxonomy.VOLATILITY_UNKNOWN
        assert result.volatility_regime == taxonomy.REGIME_UNKNOWN
        assert result.expected_move_state == taxonomy.EXPECTED_MOVE_UNKNOWN
        assert result.skew_state == taxonomy.SKEW_UNKNOWN
        assert result.term_structure_state == taxonomy.TERM_STRUCTURE_UNKNOWN
        assert result.volatility_quality == taxonomy.QUALITY_UNKNOWN
        assert result.confidence == taxonomy.CONFIDENCE_NONE
        assert result.supporting_assessment_ids == ()

    def test_insufficient_vix_history_is_unknown_not_a_guess(self):
        result = assess(current_vix=50.0, trailing_vix_history=[15.0] * (MIN_WINDOW - 1), timestamp=TS)
        assert result.iv_rank_state == taxonomy.IV_RANK_UNKNOWN
        joined = " ".join(result.reasons)
        assert "insufficient_history" in joined


# ---------------------------------------------------------------------------
# Skew / term structure always UNKNOWN, regardless of input.
# ---------------------------------------------------------------------------

class TestAlwaysUnknownDimensions:
    def test_skew_and_term_structure_always_unknown(self):
        result = assess(
            volatility_structure=_vsb(), current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS,
        )
        assert result.skew_state == taxonomy.SKEW_UNKNOWN
        assert result.term_structure_state == taxonomy.TERM_STRUCTURE_UNKNOWN


# ---------------------------------------------------------------------------
# Contradictory VSB input (both expansion and compression CONFIRMED) --
# fails closed rather than silently picking one.
# ---------------------------------------------------------------------------

class TestContradictoryVsbFailsClosed:
    def test_both_expansion_and_compression_confirmed_is_unknown(self):
        result = assess(
            volatility_structure=_vsb(expansion_state="CONFIRMED", compression_state="CONFIRMED"), timestamp=TS,
        )
        assert result.volatility_regime == taxonomy.REGIME_UNKNOWN


# ---------------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        a = assess(volatility_structure=_vsb(), current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS)
        b = assess(volatility_structure=_vsb(), current_vix=_VIX_HIGH, trailing_vix_history=_VIX_HISTORY, timestamp=TS)
        assert a == b

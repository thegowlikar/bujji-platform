"""Open interest finally reaches the direction read.

OPERATOR DIRECTIVE 2026-08-20. Direction was derived from exactly two lenses,
price structure and market structure, and BOTH read the same evidence: NIFTY
spot price polled every 30 seconds. One instrument, one field. When they
disagreed the answer was UNKNOWN -- which is what the first live continuous
session reported for most of the day.

MPPI was already computing five lenses over ~199,000 option rows a day and
reaching the THESIS, invisible to direction. `OPTIONS_POSITIONING_DIRECTION`
had been sitting in KNOWN_LENS_NAMES unfilled the whole time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_market_direction import taxonomy as mdi_tax
from bujji.msi_market_direction.engine import derive_participant_positioning_lens
from bujji.msi_participant_positioning import taxonomy as mppi_tax
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment


def _mppi(bias, strength=mppi_tax.STRENGTH_MODERATE, conflicting=(), lenses=5):
    from bujji.msi_participant_positioning.models import LensOpinion as PLens

    return MarketParticipantPositioningAssessment(
        assessment_id="MPPI-1", timestamp="T",
        positioning_bias=bias, positioning_strength=strength,
        participating_lenses=tuple(
            PLens(lens_name=f"L{i}", positioning_lean=bias,
                  confidence=mppi_tax.CONFIDENCE_MODERATE,
                  supporting_evidence_ids=(), reasoning="x")
            for i in range(lenses)),
        conflicting_lenses=tuple(conflicting),
        supporting_observation_ids=("OBS-1", "OBS-2"),
        explanation=None, provenance="test", schema_version="1.0.0")


class TestTheMappingIsDirect:
    def test_bullish_positioning_means_price_up(self):
        """MPPI's bias is already normalised to PRICE direction -- verified in
        derive_writer_dominance_lens, where CALL writers dominant yields
        BEARISH_POSITIONING. Re-inverting it here would invent an opinion."""
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.BULLISH_POSITIONING))
        assert lens.directional_lean == mdi_tax.BULLISH

    def test_bearish_positioning_means_price_down(self):
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.BEARISH_POSITIONING))
        assert lens.directional_lean == mdi_tax.BEARISH

    def test_neutral_positioning_is_a_real_neutral(self):
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.NEUTRAL_POSITIONING))
        assert lens.directional_lean == mdi_tax.NEUTRAL

    def test_strength_drives_the_lean_intensity(self):
        strong = derive_participant_positioning_lens(
            _mppi(mppi_tax.BULLISH_POSITIONING, mppi_tax.STRENGTH_STRONG))
        moderate = derive_participant_positioning_lens(
            _mppi(mppi_tax.BULLISH_POSITIONING, mppi_tax.STRENGTH_MODERATE))
        assert strong.directional_lean == mdi_tax.STRONG_BULLISH
        assert moderate.directional_lean == mdi_tax.BULLISH

    def test_it_uses_the_lens_slot_that_was_always_reserved(self):
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.BULLISH_POSITIONING))
        assert lens.lens_name == mdi_tax.OPTIONS_POSITIONING_DIRECTION
        assert lens.lens_name in mdi_tax.KNOWN_LENS_NAMES


class TestDisagreementIsNotBalance:
    def test_mixed_positioning_becomes_unknown_never_neutral(self):
        """This module's taxonomy is explicit that MIXED must not be silently
        averaged into NEUTRAL -- genuine disagreement is not genuine balance,
        and for a premium seller the difference decides whether to sell."""
        lens = derive_participant_positioning_lens(
            _mppi(mppi_tax.MIXED_POSITIONING, conflicting=("LENS_PCR",)))
        assert lens.directional_lean == mdi_tax.UNKNOWN
        assert lens.directional_lean != mdi_tax.NEUTRAL
        assert lens.confidence == mdi_tax.CONFIDENCE_NONE

    def test_unknown_positioning_contributes_no_opinion(self):
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.UNKNOWN_POSITIONING))
        assert lens.directional_lean == mdi_tax.UNKNOWN

    def test_an_absent_assessment_is_unknown_not_neutral(self):
        """An absent lens must abstain, not vote for balance."""
        lens = derive_participant_positioning_lens(None)
        assert lens.directional_lean == mdi_tax.UNKNOWN
        assert lens.confidence == mdi_tax.CONFIDENCE_NONE


class TestConfidenceIsCappedOnPurpose:
    def test_positioning_never_reaches_high_confidence(self):
        """Positioning is INTENT, not a fact about price: writers can be wrong,
        and often are at exactly the moment it matters. HIGH stays reserved for
        the structural facts MSSI reports -- a confirmed breakout or breakdown."""
        for strength in mppi_tax.ALL_POSITIONING_STRENGTHS:
            for bias in (mppi_tax.BULLISH_POSITIONING, mppi_tax.BEARISH_POSITIONING,
                         mppi_tax.NEUTRAL_POSITIONING):
                lens = derive_participant_positioning_lens(_mppi(bias, strength))
                assert lens.confidence != mdi_tax.CONFIDENCE_HIGH

    def test_internal_disagreement_lowers_confidence(self):
        clean = derive_participant_positioning_lens(
            _mppi(mppi_tax.BULLISH_POSITIONING, mppi_tax.STRENGTH_MODERATE))
        conflicted = derive_participant_positioning_lens(
            _mppi(mppi_tax.BULLISH_POSITIONING, mppi_tax.STRENGTH_MODERATE,
                  conflicting=("LENS_PCR", "LENS_MIGRATION")))
        assert clean.confidence == mdi_tax.CONFIDENCE_MODERATE
        assert conflicted.confidence == mdi_tax.CONFIDENCE_LOW

    def test_weak_participation_lowers_confidence(self):
        lens = derive_participant_positioning_lens(
            _mppi(mppi_tax.BULLISH_POSITIONING, mppi_tax.STRENGTH_WEAK))
        assert lens.confidence == mdi_tax.CONFIDENCE_LOW

    def test_the_reasoning_cites_the_real_evidence(self):
        lens = derive_participant_positioning_lens(_mppi(mppi_tax.BEARISH_POSITIONING))
        assert "positioning_bias=BEARISH_POSITIONING" in lens.reasoning
        assert "open-interest lens" in lens.reasoning
        assert lens.supporting_evidence_ids == ("OBS-1", "OBS-2")


class TestItIsWiredEndToEnd:
    def test_direction_combines_more_than_the_two_spot_price_lenses(self):
        """Name-based, not count-based -- the lesson from the MDI tests this
        change already had to fix. A fourth lens (futures basis) arrived the
        same day; a hardcoded count would have needed editing again."""
        source = (REPO_ROOT / "bujji" / "msi_market_direction" / "engine.py").read_text()
        assert "positioning_lens" in source
        assert "price_lens, structure_lens, positioning_lens" in source

    def test_mppi_is_optional_so_existing_callers_keep_working(self):
        """Requiring it would make direction unavailable on any cycle with a
        thin chain -- strictly worse than the two-lens answer we had."""
        import inspect

        from bujji.msi_market_direction.engine import determine_market_direction

        assert inspect.signature(determine_market_direction).parameters["mppi"].default is None

    def test_the_bridge_passes_positioning_through(self):
        source = (REPO_ROOT / "bujji" / "market_state" / "direction_bridge.py").read_text()
        assert "participant_positioning" in source

    def test_a_contributing_mppi_is_cited_in_supporting_assessments(self):
        source = (REPO_ROOT / "bujji" / "msi_market_direction" / "engine.py").read_text()
        assert "mppi.assessment_id" in source

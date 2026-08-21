"""Futures basis as a direction lens -- read from the CHANGE, never the level.

OPERATOR DIRECTIVE 2026-08-20. Direction had two lenses, both reading spot
price. Options positioning became the third. Futures basis is the fourth, and
the first that reads the futures market at all.

THE TRAP THIS LENS EXISTS TO AVOID: basis LEVEL is not directional. NIFTY
futures normally carry a premium to spot that decays toward expiry, so a lens
keyed on the level would report a bullish market every morning and a bearish
one every expiry -- a calendar artifact wearing the clothes of a market
opinion.

Plumbing the previous observation through also supplied MPPI's previous chain
for the first time. Two of its five lenses -- OI migration and OI
expansion/contraction -- had returned UNKNOWN on every cycle Bujji has ever
run, because nothing passed a previous snapshot. Their docstrings blame "no
intraday OI history exists in this codebase (Bhavcopy is end-of-day only)",
which stopped being true when the live chain capture began.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_market_direction import config as mdi_config
from bujji.msi_market_direction import taxonomy as mdi_tax
from bujji.msi_market_direction.engine import derive_futures_basis_lens

NOISE = mdi_config.BASIS_CHANGE_MIN_POINTS
STRONG = mdi_config.BASIS_CHANGE_STRONG_POINTS


class TestChangeNotLevel:
    def test_a_large_positive_level_alone_says_nothing(self):
        """The whole point. A steady 45-point premium is the carry, not a
        bullish opinion -- so an unchanged basis must not read as bullish."""
        lens = derive_futures_basis_lens(45.0, 45.0)
        assert lens.directional_lean == mdi_tax.NEUTRAL

    def test_widening_premium_is_bullish(self):
        lens = derive_futures_basis_lens(45.0 + STRONG - 1, 45.0)
        assert lens.directional_lean == mdi_tax.BULLISH

    def test_narrowing_premium_is_bearish(self):
        lens = derive_futures_basis_lens(45.0 - (STRONG - 1), 45.0)
        assert lens.directional_lean == mdi_tax.BEARISH

    def test_the_sign_depends_on_the_change_not_the_level(self):
        """A basis still deeply in premium but FALLING is bearish; a basis in
        discount but RISING is bullish. Level-keyed logic gets both backwards."""
        falling_from_premium = derive_futures_basis_lens(50.0, 50.0 + STRONG)
        rising_from_discount = derive_futures_basis_lens(-20.0, -20.0 - STRONG)
        assert falling_from_premium.directional_lean == mdi_tax.STRONG_BEARISH
        assert rising_from_discount.directional_lean == mdi_tax.STRONG_BULLISH

    @pytest.mark.parametrize("delta,expected", [
        (STRONG, mdi_tax.STRONG_BULLISH),
        (STRONG + 5, mdi_tax.STRONG_BULLISH),
        (-STRONG, mdi_tax.STRONG_BEARISH),
        (-(STRONG + 5), mdi_tax.STRONG_BEARISH),
    ])
    def test_magnitude_drives_the_strong_band(self, delta, expected):
        assert derive_futures_basis_lens(45.0 + delta, 45.0).directional_lean == expected


class TestTheNoiseFloor:
    @pytest.mark.parametrize("delta", [0.0, 0.5, NOISE - 0.01, -(NOISE - 0.01)])
    def test_moves_inside_the_floor_are_neutral_not_unknown(self, delta):
        """A measured non-move is EVIDENCE of no change -- distinct from
        having no evidence at all, which is UNKNOWN."""
        lens = derive_futures_basis_lens(45.0 + delta, 45.0)
        assert lens.directional_lean == mdi_tax.NEUTRAL
        assert lens.directional_lean != mdi_tax.UNKNOWN

    def test_the_floor_is_configuration_not_a_magic_number(self):
        assert mdi_config.BASIS_CHANGE_MIN_POINTS > 0
        assert mdi_config.BASIS_CHANGE_STRONG_POINTS > mdi_config.BASIS_CHANGE_MIN_POINTS

    def test_the_thresholds_are_disclosed_as_uncalibrated(self):
        """They are a modelled judgement, not measured against outcomes --
        the same posture execution_profiles takes about slippage. Saying so
        in the source is what keeps it honest."""
        source = (REPO_ROOT / "bujji" / "msi_market_direction" / "config.py").read_text()
        assert "NOT MEASURED" in source


class TestAbstainingIsNotNeutral:
    @pytest.mark.parametrize("basis,previous", [
        (None, None), (45.0, None), (None, 45.0),
    ])
    def test_missing_either_observation_yields_unknown(self, basis, previous):
        """One cycle in, or after a failed futures poll, the lens has no
        change to read. Reporting NEUTRAL would claim a measured non-move."""
        lens = derive_futures_basis_lens(basis, previous)
        assert lens.directional_lean == mdi_tax.UNKNOWN
        assert lens.confidence == mdi_tax.CONFIDENCE_NONE

    def test_it_explains_which_observation_is_missing(self):
        assert "this cycle's" in derive_futures_basis_lens(None, 45.0).reasoning
        assert "the previous cycle's" in derive_futures_basis_lens(45.0, None).reasoning


class TestConfidenceIsCapped:
    def test_basis_never_reaches_high_confidence(self):
        """A single interval's basis move is real but thin, and basis is
        noisy near expiry when carry collapses. HIGH stays reserved for
        MSSI's structural breakout/breakdown."""
        for delta in (NOISE, STRONG, STRONG * 5, -STRONG * 5):
            lens = derive_futures_basis_lens(45.0 + delta, 45.0)
            assert lens.confidence != mdi_tax.CONFIDENCE_HIGH

    def test_it_uses_the_reserved_lens_slot(self):
        lens = derive_futures_basis_lens(60.0, 45.0)
        assert lens.lens_name == mdi_tax.FUTURES_POSITIONING_DIRECTION
        assert lens.lens_name in mdi_tax.KNOWN_LENS_NAMES

    def test_the_reasoning_shows_both_numbers(self):
        lens = derive_futures_basis_lens(60.0, 45.0)
        assert "45.00" in lens.reasoning and "60.00" in lens.reasoning
        assert "+15.00" in lens.reasoning


class TestThePlumbing:
    def test_the_state_assessment_carries_both_observations(self):
        from bujji.market_state_builder.assessment_bridge import MarketStateAssessment

        fields = MarketStateAssessment.__dataclass_fields__
        assert "futures_basis" in fields and "previous_futures_basis" in fields
        assert fields["futures_basis"].default is None, "absent, never 0.0"

    def test_the_builder_remembers_after_comparing_not_before(self):
        """Overwriting first would compare a cycle against itself and report
        no change forever."""
        source = (REPO_ROOT / "bujji" / "market_state_builder" / "market_state.py").read_text()
        call = source.index("build_market_state_assessment(")
        remember = source.index("self._previous_futures_basis = futures_basis")
        assert call < remember

    def test_mppi_now_receives_a_previous_chain(self):
        """The dark-lens fix that came with the same plumbing."""
        source = (REPO_ROOT / "bujji" / "market_state_builder" / "assessment_bridge.py").read_text()
        assert "previous_option_observations, timestamp=timestamp" in source

    def test_a_failed_poll_does_not_reset_the_baseline(self):
        """Only real values are retained, so one missed futures poll costs a
        comparison rather than blanking the reference point."""
        source = (REPO_ROOT / "bujji" / "market_state_builder" / "market_state.py").read_text()
        assert "if futures_basis is not None:" in source
        assert "if option_observations:" in source

    def test_direction_receives_the_basis_from_the_bridge(self):
        source = (REPO_ROOT / "bujji" / "market_state" / "direction_bridge.py").read_text()
        assert "futures_basis" in source and "previous_futures_basis" in source

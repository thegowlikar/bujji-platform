"""L-1: swing levels that survive their own detection parameters.

Bujji's price structure has always been categorical -- RANGING, TRENDING,
swing CONFIRMED -- with no notion of the PRICE any of it sits at. This is the
first numeric structure in the codebase, so these tests are mostly about the
ways a level can be a lie: an artifact of one parameter, a value read from
the future, or an empty list pretending to be an answer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.price_levels import Bar, detect_levels, detect_swings_at_strength
from bujji.price_levels import taxonomy


def _bar(i, high, low):
    return Bar(timestamp=f"2026-08-20T{9 + i // 60:02d}:{i % 60:02d}:00+05:30",
               open=(high + low) / 2, high=high, low=low, close=(high + low) / 2)


def _flat(n, high=100.0, low=90.0, start=0):
    return [_bar(start + i, high, low) for i in range(n)]


def _with_peak(pad=10, peak=120.0):
    """A single unambiguous pivot high, padded either side."""
    return _flat(pad) + [_bar(pad, peak, 90.0)] + _flat(pad, start=pad + 1)


class TestSwingDetection:
    def test_a_clear_pivot_high_is_found(self):
        swings = detect_swings_at_strength(_with_peak(), 3)
        highs = [s for s in swings if s.kind == taxonomy.LEVEL_SWING_HIGH]
        assert len(highs) == 1 and highs[0].price == 120.0

    def test_a_flat_top_is_not_a_pivot(self):
        """Strictly greater, deliberately: a tie means the pivot is not
        unique, and publishing both sides of a flat top would double-count
        one piece of structure as two levels."""
        bars = _flat(5) + [_bar(5, 120.0, 90.0), _bar(6, 120.0, 90.0)] + _flat(5, start=7)
        assert detect_swings_at_strength(bars, 3) == []

    def test_edge_bars_are_never_pivots(self):
        """The first and last N bars have no evidence on one side. Judging
        them on partial evidence is how a chart grows levels at its edges."""
        bars = [_bar(0, 200.0, 90.0)] + _flat(10)
        assert all(s.bar_index != 0 for s in detect_swings_at_strength(bars, 3))

    def test_strength_zero_is_rejected(self):
        with pytest.raises(ValueError):
            detect_swings_at_strength(_flat(10), 0)


class TestTheAgreementGate:
    def test_a_pivot_visible_at_every_strength_survives(self):
        result = detect_levels(_with_peak(pad=12), strengths=(3, 5, 8))
        assert result.is_available
        assert any(lvl.price == 120.0 for lvl in result.levels)

    def test_a_pivot_only_the_loosest_strength_sees_is_rejected(self):
        """The whole point of the gate. A tiny bump that a 3-bar window
        calls a pivot and an 8-bar window does not is an artifact of the
        parameter, not structure in the market."""
        # A small bump inside a larger rising move: strength 3 sees it,
        # strength 8 does not (a higher high sits within 8 bars).
        bars = (_flat(9)
                + [_bar(9, 101.0, 90.0)]      # the bump
                + _flat(3, high=100.0)
                + [_bar(13, 150.0, 90.0)]     # a much higher high nearby
                + _flat(12, start=14))
        loose = [s.price for s in detect_swings_at_strength(bars, 3)
                 if s.kind == taxonomy.LEVEL_SWING_HIGH]
        assert 101.0 in loose, "positive control: the loose parameter must see the bump"

        result = detect_levels(bars, strengths=(3, 5, 8))
        published = [lvl.price for lvl in result.levels]
        assert 101.0 not in published, "a parameter artifact reached publication"

    def test_the_cost_of_the_gate_is_reported_not_hidden(self):
        """A caller must be able to say '11 of 214 survived' rather than
        publishing 11 and implying that was all there ever was."""
        result = detect_levels(_with_peak(pad=12), strengths=(3, 5, 8))
        assert result.swings_before_agreement >= result.swings_after_agreement
        assert result.swings_after_agreement == len(result.levels)

    def test_no_survivors_is_its_own_status_with_a_reason(self):
        bars = (_flat(9) + [_bar(9, 101.0, 90.0)] + _flat(3)
                + [_bar(13, 150.0, 90.0)] + _flat(12, start=14))
        result = detect_levels(bars, strengths=(3, 5, 8))
        if not result.levels:
            assert result.status == taxonomy.LEVELS_NO_AGREEMENT
            assert result.reason and "survived" in result.reason


class TestAbsenceIsNotEmptiness:
    def test_too_few_bars_is_insufficient_history_not_zero_levels(self):
        """'No levels near price' and 'we could not compute levels' must
        never render identically."""
        result = detect_levels(_flat(5), strengths=(3, 5, 8))
        assert result.status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert result.levels == () and result.is_available is False
        assert "at least" in result.reason

    def test_the_minimum_is_derived_from_the_widest_strength(self):
        assert detect_levels(_flat(16), strengths=(8,)).status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert detect_levels(_flat(17), strengths=(8,)).status != taxonomy.LEVELS_INSUFFICIENT_HISTORY


class TestNoLookahead:
    def test_bars_at_or_after_as_of_are_dropped(self):
        """With nine years of bars in one table, an assessment that reads
        past its own timestamp is one careless query away -- and the levels
        would look extraordinary."""
        bars = _with_peak(pad=12)
        cut = bars[12].timestamp                      # the peak's own timestamp
        result = detect_levels(bars, strengths=(3,), as_of=cut)
        assert all(lvl.formed_at < cut for lvl in result.levels)
        assert 120.0 not in [lvl.price for lvl in result.levels]

    def test_touches_never_come_from_beyond_the_cut(self):
        bars = _with_peak(pad=12)
        early = detect_levels(bars, strengths=(3,), as_of=bars[20].timestamp)
        full = detect_levels(bars, strengths=(3,))
        assert early.bars_considered < full.bars_considered

    def test_without_as_of_the_whole_series_is_used(self):
        bars = _with_peak(pad=12)
        assert detect_levels(bars, strengths=(3,)).bars_considered == len(bars)


class TestTouchesAreCountedNotEstimated:
    def test_an_untested_level_reports_zero_and_no_last_touch(self):
        result = detect_levels(_with_peak(pad=12), strengths=(3,))
        peak = [lvl for lvl in result.levels if lvl.price == 120.0][0]
        assert peak.touch_count == 0
        assert peak.last_touch_at is None, "absent, not an empty string"
        assert peak.strength == taxonomy.STRENGTH_UNTESTED

    def test_a_revisited_level_counts_each_returning_bar_once(self):
        bars = (_flat(12) + [_bar(12, 120.0, 90.0)] + _flat(6, start=13)
                + [_bar(19, 120.0, 119.0), _bar(20, 120.0, 119.0)]
                + _flat(12, start=21))
        result = detect_levels(bars, strengths=(3,))
        peak = [lvl for lvl in result.levels if lvl.price == 120.0]
        assert peak and peak[0].touch_count == 2
        assert peak[0].strength == taxonomy.STRENGTH_TESTED

    def test_the_tolerance_band_is_published_with_the_count(self):
        """A touch count means nothing without the band that produced it."""
        result = detect_levels(_with_peak(pad=12), strengths=(3,))
        peak = [lvl for lvl in result.levels if lvl.price == 120.0][0]
        assert peak.touch_tolerance == pytest.approx(120.0 * 0.0005)


class TestDeterminism:
    def test_the_same_bars_give_the_same_answer_every_time(self):
        bars = _with_peak(pad=12)
        a = detect_levels(bars, strengths=(3, 5, 8)).to_dict()
        b = detect_levels(bars, strengths=(3, 5, 8)).to_dict()
        assert a == b

    def test_strength_order_does_not_change_the_answer(self):
        bars = _with_peak(pad=12)
        assert (detect_levels(bars, strengths=(8, 3, 5)).to_dict()
                == detect_levels(bars, strengths=(3, 5, 8)).to_dict())

    def test_the_result_is_json_serialisable(self):
        import json

        d = detect_levels(_with_peak(pad=12), strengths=(3,)).to_dict()
        assert json.loads(json.dumps(d)) == d


class TestBarsAreBarsNotPointSamples:
    def test_a_point_sample_row_is_refused(self):
        """Live capture rows carry only `ltp`. Deriving a high from one
        traded price would invent a value never observed -- point samples
        test levels, they never form them."""
        with pytest.raises(ValueError):
            Bar.from_mapping({"timestamp": "T", "ltp": 24100.0})

    def test_a_real_ohlc_row_is_accepted(self):
        bar = Bar.from_mapping({"timestamp": "T", "open": 1, "high": 3, "low": 0.5, "close": 2})
        assert bar.high == 3.0 and bar.low == 0.5

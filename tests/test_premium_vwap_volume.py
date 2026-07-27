"""PremiumVwapTracker — genuine volume-weighting tests.

Proves the tracker is actually volume-weighted (not just equal-weight in
disguise) by using deliberately varying, non-constant volumes and checking
the result matches sum(price*volume)/sum(volume) exactly, and diverges from
a simple equal-weight average whenever volumes differ.
"""
import pytest

from bujji.signal.indicators import PremiumVwapTracker


def test_seed_with_default_weight_matches_the_seed_value():
    tracker = PremiumVwapTracker()
    tracker.update(240.0)  # No volume passed -> defaults to weight 1.0.
    assert tracker.value == 240.0
    assert tracker.candle_count == 1
    assert tracker.cumulative_volume == 1.0


def test_equal_volumes_reduce_to_equal_weight_average():
    """Sanity: constant volume every candle must reduce to exactly the old
    equal-weight behavior -- proves backward compatibility isn't
    accidental."""
    tracker = PremiumVwapTracker()
    tracker.update(240.0, volume=1_000_000.0)
    tracker.update(230.0, volume=1_000_000.0)
    tracker.update(250.0, volume=1_000_000.0)
    assert tracker.value == pytest.approx((240.0 + 230.0 + 250.0) / 3)


def test_genuinely_different_volumes_produce_a_genuinely_different_vwap():
    """The core proof: with real, varying volume, the VWAP must differ from
    the simple (equal-weight) average -- a heavier-volume candle pulls the
    VWAP toward its own price more than a lighter one."""
    tracker = PremiumVwapTracker()
    # A big spike (350) on LOW volume should barely move the VWAP...
    tracker.update(300.0, volume=10_000_000.0)
    tracker.update(350.0, volume=100_000.0)  # Thin volume -- shouldn't dominate.
    equal_weight_would_be = (300.0 + 350.0) / 2  # = 325.0
    vol_weighted = tracker.value
    assert vol_weighted < equal_weight_would_be  # Pulled toward the heavy candle (300).
    expected = (300.0 * 10_000_000.0 + 350.0 * 100_000.0) / (10_000_000.0 + 100_000.0)
    assert vol_weighted == pytest.approx(expected)


def test_zero_volume_candle_falls_back_to_equal_weight_for_that_candle():
    """A candle with volume=0 (data glitch) must NOT silently vanish from
    the average (price*0=0 would contribute nothing to either sum) --
    it degrades to weight=1.0 for that one candle instead."""
    tracker = PremiumVwapTracker()
    tracker.update(240.0, volume=1_000_000.0)
    tracker.update(300.0, volume=0.0)  # Glitch: zero volume reported.
    # The zero-volume candle still counts (weight=1), not silently dropped.
    assert tracker.candle_count == 2
    expected = (240.0 * 1_000_000.0 + 300.0 * 1.0) / (1_000_000.0 + 1.0)
    assert tracker.value == pytest.approx(expected)


def test_negative_volume_also_falls_back_to_equal_weight():
    """Defensive: a malformed negative volume must never corrupt the
    running sums (which could otherwise produce a nonsensical or even
    negative VWAP)."""
    tracker = PremiumVwapTracker()
    tracker.update(240.0, volume=1_000_000.0)
    tracker.update(300.0, volume=-50.0)  # Malformed.
    expected = (240.0 * 1_000_000.0 + 300.0 * 1.0) / (1_000_000.0 + 1.0)
    assert tracker.value == pytest.approx(expected)
    assert tracker.value > 0


def test_ready_and_candle_count_track_calls_not_volume():
    tracker = PremiumVwapTracker()
    assert tracker.ready is False
    assert tracker.candle_count == 0
    tracker.update(100.0, volume=0.0)  # Even a zero-volume update marks ready.
    assert tracker.ready is True
    assert tracker.candle_count == 1

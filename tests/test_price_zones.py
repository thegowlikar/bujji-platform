"""L-2: supply and demand zones, and whether their claim survived.

A level is one price; a zone is the band a move came from. The failure modes
are the same shape as L-1's and one new one: a zone is a CLAIM about where
price should react, and a claim that has already failed must not keep being
presented as if it hadn't.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.price_levels import Bar, detect_zones, taxonomy, true_range, typical_range
from bujji.price_levels.zones import detect_zones_at_multiple, track_zone


def _bar(i, high, low, close=None, open_=None):
    mid = (high + low) / 2
    return Bar(timestamp=f"T{i:04d}", open=open_ if open_ is not None else mid,
               high=high, low=low, close=close if close is not None else mid)


def _quiet(n, start=0, price=100.0):
    """Bars with a small, steady range -- the baseline a move stands out from."""
    return [_bar(start + i, price + 1, price - 1, price) for i in range(n)]


def _demand_series(pad=8):
    """25 quiet bars, an origin bar, an impulse UP, then quiet at the top."""
    bars = _quiet(25)
    bars.append(_bar(25, 101.0, 99.0, 100.0))          # origin
    bars.append(_bar(26, 120.0, 100.0, 118.0))         # impulse up
    bars += _quiet(pad, start=27, price=118.0)
    return bars


def _supply_series(pad=8):
    bars = _quiet(25)
    bars.append(_bar(25, 101.0, 99.0, 100.0))
    bars.append(_bar(26, 100.0, 80.0, 82.0))           # impulse down
    bars += _quiet(pad, start=27, price=82.0)
    return bars


class TestZoneFormation:
    def test_an_impulse_up_leaves_a_demand_zone_at_its_origin(self):
        result = detect_zones(_demand_series())
        assert result.is_available
        demand = [z for z in result.zones if z.kind == taxonomy.ZONE_DEMAND]
        assert len(demand) == 1
        assert (demand[0].lower, demand[0].upper) == (99.0, 101.0)

    def test_an_impulse_down_leaves_a_supply_zone(self):
        result = detect_zones(_supply_series())
        supply = [z for z in result.zones if z.kind == taxonomy.ZONE_SUPPLY]
        assert len(supply) == 1 and (supply[0].lower, supply[0].upper) == (99.0, 101.0)

    def test_the_band_is_the_origin_bars_own_range_not_a_widened_one(self):
        """No smoothing, no padding: the band is a range price really
        traded in, so a strike is inside or outside a real thing."""
        z = detect_zones(_demand_series()).zones[0]
        assert z.height == pytest.approx(2.0) and z.midpoint == pytest.approx(100.0)

    def test_quiet_bars_alone_produce_no_zones(self):
        result = detect_zones(_quiet(40))
        assert result.zones == ()
        assert result.status in (taxonomy.LEVELS_NO_AGREEMENT, taxonomy.LEVELS_AVAILABLE)

    def test_the_impulse_size_is_recorded(self):
        z = detect_zones(_demand_series()).zones[0]
        assert z.impulse_size == pytest.approx(18.0)


class TestScaleComesFromTheSeries:
    def test_true_range_includes_a_gap_from_the_previous_close(self):
        previous = _bar(0, 101.0, 99.0, 100.0)
        gapped = _bar(1, 121.0, 119.0, 120.0)
        assert true_range(gapped, previous) == pytest.approx(21.0)
        assert true_range(gapped, None) == pytest.approx(2.0)

    def test_typical_range_uses_the_median_not_the_mean(self):
        """One gap day must not redefine what is typical for the series."""
        bars = _quiet(20) + [_bar(20, 300.0, 100.0, 200.0)]
        assert typical_range(bars, 21, 21) == pytest.approx(2.0)

    def test_insufficient_history_yields_no_scale_and_so_no_zone(self):
        assert typical_range(_quiet(1), 1, 20) is None

    def test_the_same_shape_at_a_different_price_finds_the_same_zone(self):
        """A hardcoded point threshold would silently become stricter every
        year the index rose. The threshold must scale with the instrument."""
        low = detect_zones(_demand_series())
        high = detect_zones([Bar(b.timestamp, b.open * 240, b.high * 240,
                                 b.low * 240, b.close * 240) for b in _demand_series()])
        assert len(low.zones) == len(high.zones) == 1
        assert high.zones[0].kind == low.zones[0].kind


class TestTheAgreementGate:
    def test_a_zone_only_the_loosest_threshold_sees_is_rejected(self):
        """A move of ~1.7 typical ranges is a zone at 1.5x and not at 2.0x.
        Publishing it would be publishing the threshold, not the market."""
        bars = _quiet(25) + [_bar(25, 101.0, 99.0, 100.0),
                             _bar(26, 104.0, 100.0, 103.4)] + _quiet(8, start=27, price=103.0)
        loose = detect_zones_at_multiple(bars, 1.5)
        assert loose, "positive control: the loose threshold must see this candidate"

        result = detect_zones(bars, multiples=(1.5, 2.0, 3.0))
        assert result.zones == ()
        assert result.status == taxonomy.LEVELS_NO_AGREEMENT
        assert "artifacts" in result.reason

    def test_the_gate_cost_is_reported(self):
        result = detect_zones(_demand_series())
        assert result.zones_before_agreement >= result.zones_after_agreement
        assert result.zones_after_agreement == len(result.zones)

    def test_an_empty_multiples_list_is_refused(self):
        with pytest.raises(ValueError):
            detect_zones(_demand_series(), multiples=())


class TestTheZonesClaimCanFail:
    def test_an_untouched_zone_is_fresh(self):
        z = detect_zones(_demand_series()).zones[0]
        assert z.status == taxonomy.ZONE_FRESH
        assert z.test_count == 0 and z.last_test_at is None and z.broken_at is None

    def test_a_wick_into_the_zone_is_a_test_not_a_break(self):
        """A wick through and back is exactly the test a zone is supposed to
        survive. Calling it a break would retire every zone on first contact."""
        status, tests, last, broken = track_zone(
            _quiet(3) + [_bar(3, 102.0, 98.0, 100.5)], origin_index=-2,
            kind=taxonomy.ZONE_DEMAND, lower=99.0, upper=101.0)
        assert status == taxonomy.ZONE_TESTED and tests >= 1 and broken is None

    def test_a_close_through_the_far_side_breaks_a_demand_zone(self):
        bars = _demand_series() + [_bar(40, 99.0, 90.0, 92.0)]
        z = [x for x in detect_zones(bars).zones if x.kind == taxonomy.ZONE_DEMAND][0]
        assert z.status == taxonomy.ZONE_BROKEN and z.broken_at is not None

    def test_a_close_through_the_far_side_breaks_a_supply_zone(self):
        bars = _supply_series() + [_bar(40, 115.0, 100.0, 112.0)]
        z = [x for x in detect_zones(bars).zones if x.kind == taxonomy.ZONE_SUPPLY][0]
        assert z.status == taxonomy.ZONE_BROKEN

    def test_broken_zones_are_kept_but_excluded_from_live_zones(self):
        """A failed claim is real history -- 'what has already broken here'
        is a legitimate question. It just must not be presented as live."""
        bars = _demand_series() + [_bar(40, 99.0, 90.0, 92.0)]
        result = detect_zones(bars)
        assert any(z.status == taxonomy.ZONE_BROKEN for z in result.zones)
        assert all(z.status != taxonomy.ZONE_BROKEN for z in result.live_zones)


class TestAbsenceAndLookahead:
    def test_too_few_bars_is_insufficient_history_not_zero_zones(self):
        result = detect_zones(_quiet(5))
        assert result.status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert result.zones == () and result.is_available is False
        assert "at least" in result.reason

    def test_bars_at_or_after_as_of_are_dropped(self):
        bars = _demand_series()
        cut = bars[26].timestamp                       # the impulse bar itself
        result = detect_zones(bars, as_of=cut)
        assert result.zones == () or all(z.formed_at < cut for z in result.zones)

    def test_a_zone_cannot_be_broken_by_a_bar_beyond_the_cut(self):
        bars = _demand_series() + [_bar(40, 99.0, 90.0, 92.0)]
        early = detect_zones(bars, as_of="T0040")
        assert all(z.status != taxonomy.ZONE_BROKEN for z in early.zones)


class TestDeterminism:
    def test_the_same_bars_give_the_same_answer(self):
        bars = _demand_series()
        assert detect_zones(bars).to_dict() == detect_zones(bars).to_dict()

    def test_multiple_order_does_not_change_the_answer(self):
        bars = _demand_series()
        assert (detect_zones(bars, multiples=(3.0, 1.5, 2.0)).to_dict()
                == detect_zones(bars, multiples=(1.5, 2.0, 3.0)).to_dict())

    def test_the_result_is_json_serialisable(self):
        import json

        d = detect_zones(_demand_series()).to_dict()
        assert json.loads(json.dumps(d)) == d

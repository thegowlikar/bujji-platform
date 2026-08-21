"""L-3: where price is, relative to structure that can actually be reached.

L-1 publishes real levels and too many of them. This turns that into an
answer to "what is above me, what is below me, how much room is there" --
and the tests here are mostly about the ways a shortlist can lie: by hiding
what it dropped, by inventing a relevance score, or by rendering "nothing
nearby" identically to "we could not look".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.price_levels import LevelSet, PriceLevel, SupplyDemandZone, ZoneSet, taxonomy
from bujji.price_levels import build_level_context


def _level(price, touches=3, kind=taxonomy.LEVEL_SWING_LOW):
    return PriceLevel(
        price=price, kind=kind, formed_at="T0001", formed_bar_index=1,
        touch_count=touches, last_touch_at="T0100" if touches else None,
        strength=taxonomy.STRENGTH_TESTED if touches else taxonomy.STRENGTH_UNTESTED,
        detected_at_strengths=(3, 5, 8), source_resolution="FIVE_MINUTE",
        touch_tolerance=1.0,
    )


def _levels(*prices_and_touches):
    return LevelSet(
        status=taxonomy.LEVELS_AVAILABLE,
        levels=tuple(_level(p, t) for p, t in prices_and_touches),
        bars_considered=1500, swings_after_agreement=len(prices_and_touches),
    )


def _zone(lower, upper, kind=taxonomy.ZONE_SUPPLY, status=taxonomy.ZONE_FRESH):
    return SupplyDemandZone(
        lower=lower, upper=upper, kind=kind, formed_at="T0001", formed_bar_index=1,
        impulse_size=50.0, status=status, test_count=0, last_test_at=None,
        broken_at="T0500" if status == taxonomy.ZONE_BROKEN else None,
        detected_at_multiples=(1.5, 2.0, 3.0), source_resolution="FIVE_MINUTE",
    )


def _zones(*zs):
    return ZoneSet(status=taxonomy.LEVELS_AVAILABLE, zones=tuple(zs),
                   zones_after_agreement=len(zs))


class TestTheShortlistCannotHideWhatItDropped:
    def test_the_whole_funnel_is_published(self):
        """A shortlist that silently discards ninety levels reads exactly
        like a market with ten levels in it."""
        levels = _levels(*[(24000.0 + i, 3) for i in range(100)])
        ctx = build_level_context(spot=24050.0, levels=levels, band_fraction=0.002,
                                  max_per_side=5)
        assert ctx.levels_total == 100
        assert ctx.levels_in_band < ctx.levels_total
        assert ctx.levels_published <= 10
        assert ctx.levels_published < ctx.levels_after_min_touches

    def test_untested_levels_are_excluded_and_counted(self):
        levels = _levels((23990.0, 0), (23980.0, 5))
        ctx = build_level_context(spot=24000.0, levels=levels)
        assert ctx.levels_in_band == 2 and ctx.levels_after_min_touches == 1
        assert [l.price for l in ctx.supports_by_proximity] == [23980.0]

    def test_min_touches_zero_keeps_untested_levels(self):
        ctx = build_level_context(spot=24000.0, levels=_levels((23990.0, 0)), min_touches=0)
        assert [l.price for l in ctx.supports_by_proximity] == [23990.0]


class TestProximityNotCalendar:
    def test_only_reachable_structure_is_published(self):
        """Age is not the discriminator -- reachability is. A far level is
        excluded because price cannot get there, not because it is old."""
        levels = _levels((24000.0, 5), (18000.0, 200))
        ctx = build_level_context(spot=24100.0, levels=levels, band_fraction=0.02)
        prices = [l.price for l in ctx.supports_by_proximity]
        assert 24000.0 in prices and 18000.0 not in prices

    def test_the_band_scales_with_price_not_a_point_constant(self):
        near = build_level_context(spot=100.0, levels=_levels((99.0, 5)), band_fraction=0.02)
        far = build_level_context(spot=24000.0, levels=_levels((23760.0, 5)),
                                  band_fraction=0.02)
        assert near.supports_by_proximity and far.supports_by_proximity

    def test_no_lookback_window_parameter_exists(self):
        """A 'last N days' filter would throw away good structure and keep
        bad -- the exact mistake the decay intuition warns against."""
        import inspect

        params = inspect.signature(build_level_context).parameters
        assert not any("day" in p or "lookback" in p or "recent" in p for p in params)


class TestTwoRealOrderingsNotOneInventedScore:
    def test_proximity_and_strength_are_published_separately(self):
        """Ranking by w1*proximity + w2*touches would embed MY weights and
        every downstream conclusion would inherit them while looking
        objective. Both orderings are real measurements; the trade-off
        belongs to the consumer."""
        levels = _levels((23990.0, 2), (23900.0, 150))
        ctx = build_level_context(spot=24000.0, levels=levels)
        assert ctx.supports_by_proximity[0].price == 23990.0
        assert ctx.supports_by_strength[0].price == 23900.0

    def test_both_orderings_are_deterministic_under_ties(self):
        levels = _levels((23990.0, 5), (23980.0, 5), (23970.0, 5))
        a = build_level_context(spot=24000.0, levels=levels).to_dict()
        b = build_level_context(spot=24000.0, levels=levels).to_dict()
        assert a == b


class TestAirAndPosition:
    def test_air_is_the_distance_to_whichever_obstacle_comes_first(self):
        """Price does not care whether the thing above it is one of our
        levels or one of our zones."""
        ctx = build_level_context(
            spot=24000.0, levels=_levels((24100.0, 5, )),
            zones=_zones(_zone(24050.0, 24070.0, taxonomy.ZONE_SUPPLY)))
        assert ctx.air_above == pytest.approx(50.0), "the zone edge is nearer than the level"

    def test_air_is_none_when_nothing_is_reachable(self):
        """Not zero. Zero air means price is against an obstacle; None means
        there is nothing in the way, which is the opposite fact."""
        ctx = build_level_context(spot=24000.0, levels=_levels((23900.0, 5)))
        assert ctx.air_above is None and ctx.air_below == pytest.approx(100.0)

    def test_position_in_range_needs_both_sides(self):
        one_sided = build_level_context(spot=24000.0, levels=_levels((23900.0, 5)))
        assert one_sided.position_in_range is None

        both = build_level_context(spot=24000.0, levels=_levels((23900.0, 5), (24100.0, 5)))
        assert both.position_in_range == pytest.approx(0.5)

    def test_air_percentages_accompany_the_point_distances(self):
        ctx = build_level_context(spot=24000.0, levels=_levels((23880.0, 5)))
        assert ctx.air_below_pct == pytest.approx(0.5)


class TestZonesAreFilteredByLifeNotJustDistance:
    def test_broken_zones_never_appear(self):
        zones = _zones(_zone(24050.0, 24070.0, taxonomy.ZONE_SUPPLY, taxonomy.ZONE_BROKEN))
        ctx = build_level_context(spot=24000.0, levels=_levels((23900.0, 5)), zones=zones)
        assert ctx.zones_above == () and ctx.zones_total == 1 and ctx.zones_live == 0

    def test_zones_are_split_by_side(self):
        zones = _zones(_zone(24050.0, 24070.0, taxonomy.ZONE_SUPPLY),
                       _zone(23900.0, 23920.0, taxonomy.ZONE_DEMAND))
        ctx = build_level_context(spot=24000.0, levels=_levels((23950.0, 5)), zones=zones)
        assert len(ctx.zones_above) == 1 and len(ctx.zones_below) == 1

    def test_zones_are_optional(self):
        ctx = build_level_context(spot=24000.0, levels=_levels((23900.0, 5)), zones=None)
        assert ctx.is_available and ctx.zones_above == () and ctx.zones_total == 0


class TestAbsenceIsNotEmptiness:
    def test_an_unavailable_level_set_propagates_its_own_status_and_reason(self):
        """'We could not compute levels' must not be flattened into
        'nothing nearby' -- the consumer would trade on the difference."""
        unavailable = LevelSet(status=taxonomy.LEVELS_INSUFFICIENT_HISTORY,
                               reason="only 4 bars available")
        ctx = build_level_context(spot=24000.0, levels=unavailable)
        assert ctx.status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert ctx.reason == "only 4 bars available"
        assert ctx.is_available is False

    def test_no_spot_is_refused_rather_than_guessed(self):
        ctx = build_level_context(spot=None, levels=_levels((23900.0, 5)))
        assert ctx.status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert "spot" in ctx.reason

    def test_a_nonpositive_spot_is_refused(self):
        assert build_level_context(spot=0.0, levels=_levels((1.0, 5))).is_available is False

    def test_nothing_reachable_is_available_with_empty_sides(self):
        """Distinct from the cases above: we looked, and there is nothing."""
        ctx = build_level_context(spot=24000.0, levels=_levels((18000.0, 5)))
        assert ctx.is_available is True
        assert ctx.supports_by_proximity == () and ctx.levels_published == 0

    def test_the_result_is_json_serialisable(self):
        import json

        d = build_level_context(spot=24000.0, levels=_levels((23900.0, 5)),
                                zones=_zones(_zone(24050.0, 24070.0))).to_dict()
        assert json.loads(json.dumps(d)) == d

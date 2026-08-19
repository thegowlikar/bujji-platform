"""L-3: where price actually is, relative to structure that matters.

L-1 published 130 surviving levels across twenty sessions in a 1,168-point
range -- one every nine points. Every one of them is real structure, and the
set as a whole is unusable: a decision cannot consider a hundred things.
This module turns that into an answer to the question a premium seller
actually asks: what is above me, what is below me, and how much room is there
before price meets something.

THE FILTER IS PROXIMITY, NOT CALENDAR (operator directive, 2026-08-19). A
zone from 2024 near today's price is live; last week's zone 2,000 points away
is not. Age is not the discriminator -- reachability is. So relevance is a
band around spot, and nothing here carries a "last N days" window.

NO INVENTED RELEVANCE SCORE. It would be easy to rank levels by
`w1*proximity + w2*touches` and call the result importance. Those weights
would be mine, not the market's, and every downstream conclusion would
inherit them while looking objective. Instead this module publishes TWO
orderings, each a real measurement -- nearest by distance, strongest by
observed touch count -- and leaves the trade-off to the consumer.

EVERY DROPPED LEVEL IS COUNTED. A shortlist that silently discards ninety
levels reads exactly like a market with ten levels in it. The counts at each
filtering stage are published so a reader can see the funnel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from . import taxonomy
from .models import LevelSet, PriceLevel, SupplyDemandZone, ZoneSet

# How far from spot structure is still reachable, as a fraction of spot.
# 2% of NIFTY 24,000 is ~480 points -- comfortably outside any plausible
# single-session strangle, and far inside the years of history behind it.
DEFAULT_BAND_FRACTION = 0.02

# A level nobody ever came back to is a level nobody has confirmed. The
# default keeps untested levels OUT of the shortlist while leaving them in
# the underlying LevelSet, and the count of what this removed is published.
DEFAULT_MIN_TOUCHES = 1

# The shortlist cap per side. Not a claim that only this many exist -- the
# funnel counts say how many did.
DEFAULT_MAX_PER_SIDE = 5


@dataclass(frozen=True)
class LevelContext:
    """Price's position relative to reachable structure.

    `status` is checked FIRST, exactly as with LevelSet/ZoneSet. AVAILABLE
    with empty sides means "nothing reachable"; INSUFFICIENT_HISTORY means
    "we could not look" -- and those must never render the same.
    """

    status: str
    spot: Optional[float] = None

    nearest_support: Optional[PriceLevel] = None
    nearest_resistance: Optional[PriceLevel] = None
    supports_by_proximity: Tuple[PriceLevel, ...] = ()
    resistances_by_proximity: Tuple[PriceLevel, ...] = ()
    supports_by_strength: Tuple[PriceLevel, ...] = ()
    resistances_by_strength: Tuple[PriceLevel, ...] = ()

    zones_below: Tuple[SupplyDemandZone, ...] = ()
    zones_above: Tuple[SupplyDemandZone, ...] = ()

    # Distance to the nearest obstacle on each side -- level OR zone edge,
    # whichever comes first. None means nothing reachable that way, which is
    # itself information: price has room.
    air_above: Optional[float] = None
    air_below: Optional[float] = None
    air_above_pct: Optional[float] = None
    air_below_pct: Optional[float] = None

    # 0.0 at the nearest support, 1.0 at the nearest resistance. None unless
    # BOTH exist -- a position "in a range" with only one side is not a
    # position in a range.
    position_in_range: Optional[float] = None

    # The funnel, published so a shortlist cannot masquerade as the whole.
    levels_total: int = 0
    levels_in_band: int = 0
    levels_after_min_touches: int = 0
    levels_published: int = 0
    zones_total: int = 0
    zones_live: int = 0
    zones_in_band: int = 0

    band_fraction: float = DEFAULT_BAND_FRACTION
    min_touches: int = DEFAULT_MIN_TOUCHES
    reason: Optional[str] = None
    schema_version: str = taxonomy.SCHEMA_VERSION

    @property
    def is_available(self) -> bool:
        return self.status == taxonomy.LEVELS_AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "spot": self.spot,
            "nearest_support": self.nearest_support.to_dict() if self.nearest_support else None,
            "nearest_resistance": (self.nearest_resistance.to_dict()
                                   if self.nearest_resistance else None),
            "supports_by_proximity": [l.to_dict() for l in self.supports_by_proximity],
            "resistances_by_proximity": [l.to_dict() for l in self.resistances_by_proximity],
            "supports_by_strength": [l.to_dict() for l in self.supports_by_strength],
            "resistances_by_strength": [l.to_dict() for l in self.resistances_by_strength],
            "zones_below": [z.to_dict() for z in self.zones_below],
            "zones_above": [z.to_dict() for z in self.zones_above],
            "air_above": self.air_above, "air_below": self.air_below,
            "air_above_pct": self.air_above_pct, "air_below_pct": self.air_below_pct,
            "position_in_range": self.position_in_range,
            "levels_total": self.levels_total, "levels_in_band": self.levels_in_band,
            "levels_after_min_touches": self.levels_after_min_touches,
            "levels_published": self.levels_published,
            "zones_total": self.zones_total, "zones_live": self.zones_live,
            "zones_in_band": self.zones_in_band,
            "band_fraction": self.band_fraction, "min_touches": self.min_touches,
            "reason": self.reason, "schema_version": self.schema_version,
        }


def build_level_context(
    *,
    spot: Optional[float],
    levels: LevelSet,
    zones: Optional[ZoneSet] = None,
    band_fraction: float = DEFAULT_BAND_FRACTION,
    min_touches: int = DEFAULT_MIN_TOUCHES,
    max_per_side: int = DEFAULT_MAX_PER_SIDE,
) -> LevelContext:
    """Assemble the context. Pure: no clock, no I/O, no market opinion."""
    if spot is None or spot <= 0:
        return LevelContext(
            status=taxonomy.LEVELS_INSUFFICIENT_HISTORY, spot=spot,
            band_fraction=band_fraction, min_touches=min_touches,
            reason="no usable spot price -- position relative to structure is undefined",
        )

    # An unavailable input propagates its OWN status and reason rather than
    # being flattened into "nothing nearby".
    if not levels.is_available:
        return LevelContext(
            status=levels.status, spot=spot, band_fraction=band_fraction,
            min_touches=min_touches, levels_total=len(levels.levels),
            reason=levels.reason,
        )

    band = abs(spot) * band_fraction
    lo, hi = spot - band, spot + band

    in_band = [lvl for lvl in levels.levels if lo <= lvl.price <= hi]
    tested = [lvl for lvl in in_band if lvl.touch_count >= min_touches]

    below = [lvl for lvl in tested if lvl.price < spot]
    above = [lvl for lvl in tested if lvl.price > spot]

    by_prox_below = sorted(below, key=lambda l: (spot - l.price, l.price))
    by_prox_above = sorted(above, key=lambda l: (l.price - spot, l.price))
    # Strength is the observed touch count; proximity breaks ties so the
    # ordering is total and therefore deterministic.
    by_str_below = sorted(below, key=lambda l: (-l.touch_count, spot - l.price))
    by_str_above = sorted(above, key=lambda l: (-l.touch_count, l.price - spot))

    supports = tuple(by_prox_below[:max_per_side])
    resistances = tuple(by_prox_above[:max_per_side])

    zone_set = zones if zones is not None and zones.is_available else None
    live = zone_set.live_zones if zone_set else ()
    zones_below = tuple(sorted((z for z in live if z.upper < spot),
                               key=lambda z: spot - z.upper)[:max_per_side])
    zones_above = tuple(sorted((z for z in live if z.lower > spot),
                               key=lambda z: z.lower - spot)[:max_per_side])
    zones_in_band = len([z for z in live if lo <= z.midpoint <= hi])

    # The nearest obstacle on each side is whichever comes first, a level or
    # a zone edge -- price does not care which of our categories it is.
    up_candidates = [l.price - spot for l in resistances] + [z.lower - spot for z in zones_above]
    down_candidates = [spot - l.price for l in supports] + [spot - z.upper for z in zones_below]
    air_above = min(up_candidates) if up_candidates else None
    air_below = min(down_candidates) if down_candidates else None

    nearest_support = supports[0] if supports else None
    nearest_resistance = resistances[0] if resistances else None
    position = None
    if nearest_support is not None and nearest_resistance is not None:
        span = nearest_resistance.price - nearest_support.price
        if span > 0:
            position = (spot - nearest_support.price) / span

    return LevelContext(
        status=taxonomy.LEVELS_AVAILABLE, spot=spot,
        nearest_support=nearest_support, nearest_resistance=nearest_resistance,
        supports_by_proximity=supports, resistances_by_proximity=resistances,
        supports_by_strength=tuple(by_str_below[:max_per_side]),
        resistances_by_strength=tuple(by_str_above[:max_per_side]),
        zones_below=zones_below, zones_above=zones_above,
        air_above=air_above, air_below=air_below,
        air_above_pct=(100.0 * air_above / spot) if air_above is not None else None,
        air_below_pct=(100.0 * air_below / spot) if air_below is not None else None,
        position_in_range=position,
        levels_total=len(levels.levels), levels_in_band=len(in_band),
        levels_after_min_touches=len(tested),
        levels_published=len(supports) + len(resistances),
        zones_total=len(zone_set.zones) if zone_set else 0,
        zones_live=len(live), zones_in_band=zones_in_band,
        band_fraction=band_fraction, min_touches=min_touches,
    )

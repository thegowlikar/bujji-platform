"""Intraday: live samples TEST structure, they never form or break it.

The daily refresh builds levels and zones from real OHLC bars. During the
session all Bujji has is a 60-second point sample -- one traded price, no
high, no low, no close of anything. Two rules follow, and both are refusals:

  1. A SAMPLE NEVER FORMS STRUCTURE. A pivot needs bars either side of it; a
     zone needs a range price traded in. Neither exists in a single number.

  2. A SAMPLE NEVER BREAKS A ZONE. L-2 decides a break on a CLOSE through the
     far side, precisely so a wick through and back does not retire a zone on
     its first real test. A point sample is not a close -- it is closer to a
     wick, being one instant inside a bar that has not finished. Letting it
     break a zone would reintroduce the exact failure the close rule exists
     to prevent, one cadence lower down.

What a sample CAN do is what it really is evidence of: price was here. So it
increments touch and test counts and updates the "last seen at" stamps. Break
decisions wait for tomorrow's refresh over the completed bars.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional, Tuple

from . import taxonomy
from .models import LevelSet, PriceLevel, SupplyDemandZone, ZoneSet


def apply_sample_to_levels(levels: LevelSet, price: float, timestamp: str) -> Tuple[LevelSet, int]:
    """Record a live touch against every level the sample sat inside.

    Returns the updated set and how many levels were touched. The tolerance
    is each level's OWN published band -- the same number the daily refresh
    counted bar touches with, so intraday and historical counts mean the
    same thing and can be added together honestly.
    """
    if not levels.is_available or price is None or price <= 0:
        return levels, 0

    touched = 0
    updated = []
    for level in levels.levels:
        if abs(price - level.price) <= level.touch_tolerance:
            touched += 1
            count = level.touch_count + 1
            updated.append(replace(
                level, touch_count=count, last_touch_at=timestamp,
                strength=(taxonomy.STRENGTH_UNTESTED if count <= 0
                          else taxonomy.STRENGTH_TESTED if count <= 2
                          else taxonomy.STRENGTH_STRONG),
            ))
        else:
            updated.append(level)

    if not touched:
        return levels, 0
    return replace(levels, levels=tuple(updated)), touched


def apply_sample_to_zones(zones: ZoneSet, price: float, timestamp: str) -> Tuple[ZoneSet, int]:
    """Record a live test against every LIVE zone the sample sat inside.

    A BROKEN zone is left completely alone: its claim already failed, and
    counting further visits would quietly make a dead zone look active. And
    no zone is broken here -- see this module's docstring.
    """
    if not zones.is_available or price is None or price <= 0:
        return zones, 0

    tested = 0
    updated = []
    for zone in zones.zones:
        if zone.status != taxonomy.ZONE_BROKEN and zone.lower <= price <= zone.upper:
            tested += 1
            updated.append(replace(
                zone, status=taxonomy.ZONE_TESTED,
                test_count=zone.test_count + 1, last_test_at=timestamp,
            ))
        else:
            updated.append(zone)

    if not tested:
        return zones, 0
    return replace(zones, zones=tuple(updated)), tested


def apply_sample(
    levels: LevelSet, zones: Optional[ZoneSet], price: float, timestamp: str,
) -> Tuple[LevelSet, Optional[ZoneSet], int, int]:
    """Apply one live sample to both. Returns (levels, zones, touched, tested)."""
    new_levels, touched = apply_sample_to_levels(levels, price, timestamp)
    if zones is None:
        return new_levels, None, touched, 0
    new_zones, tested = apply_sample_to_zones(zones, price, timestamp)
    return new_levels, new_zones, touched, tested

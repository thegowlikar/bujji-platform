"""Price Levels — the one entry point.

`detect_levels()` turns real OHLC bars into published levels, or into an
honest statement that it could not. It derives nothing about the market
beyond what the bars themselves say: a level's price IS an observed high or
low, and a touch count IS a count of later bars that entered the band.

NO LOOKAHEAD, ENFORCED HERE RATHER THAN TRUSTED. With nine years of bars in
one table, an assessment that accidentally reads bars from after its own
timestamp is one careless query away, and the resulting levels would look
extraordinary. `detect_levels(as_of=...)` DROPS every bar at or after
`as_of` before doing anything else, and records how many it dropped.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import taxonomy
from .models import Bar, LevelSet, PriceLevel, ZoneSet
from .swings import swings_surviving_agreement
from .zones import build_zones


def _strength_bucket(touch_count: int) -> str:
    if touch_count <= 0:
        return taxonomy.STRENGTH_UNTESTED
    if touch_count <= 2:
        return taxonomy.STRENGTH_TESTED
    return taxonomy.STRENGTH_STRONG


def count_touches(
    bars: Sequence[Bar], price: float, after_index: int, tolerance: float,
) -> Tuple[int, Optional[str]]:
    """How many LATER bars actually entered the band around `price`.

    Only bars strictly after the forming pivot count: the pivot bar itself
    made the level and revisiting is what tests it. A bar counts at most
    once however deeply it penetrates -- this measures how often price came
    back, not how hard.
    """
    count = 0
    last: Optional[str] = None
    lo, hi = price - tolerance, price + tolerance
    for bar in bars[after_index + 1:]:
        if bar.low <= hi and bar.high >= lo:
            count += 1
            last = bar.timestamp
    return count, last


def detect_levels(
    bars: Sequence[Bar],
    *,
    strengths: Sequence[int] = taxonomy.DEFAULT_SWING_STRENGTHS,
    touch_tolerance_fraction: float = taxonomy.DEFAULT_TOUCH_TOLERANCE_FRACTION,
    source_resolution: str = "",
    as_of: Optional[str] = None,
) -> LevelSet:
    """Published levels, or a named reason there are none.

    `as_of` (ISO timestamp) is the no-lookahead cut: bars at or after it are
    dropped before detection. Omit it only for a whole-history sweep.
    """
    ordered = tuple(sorted(set(int(s) for s in strengths)))
    if not ordered:
        raise ValueError("at least one detection strength is required")

    usable: List[Bar] = [b for b in bars if as_of is None or b.timestamp < as_of]

    # A pivot at the widest strength needs that many bars on BOTH sides plus
    # the pivot itself. Fewer than that is not "no levels" -- it is not
    # enough evidence to have an opinion, and it says so.
    widest = ordered[-1]
    minimum = widest * 2 + 1
    if len(usable) < minimum:
        return LevelSet(
            status=taxonomy.LEVELS_INSUFFICIENT_HISTORY, as_of=as_of,
            bars_considered=len(usable), strengths_required=ordered,
            source_resolution=source_resolution,
            reason=(f"{len(usable)} bars available; strength {widest} needs at least "
                    f"{minimum} to have evidence on both sides of a pivot"),
        )

    survivors, seen_by_loosest = swings_surviving_agreement(usable, ordered)

    if not survivors:
        return LevelSet(
            status=taxonomy.LEVELS_NO_AGREEMENT, as_of=as_of,
            bars_considered=len(usable), swings_before_agreement=seen_by_loosest,
            swings_after_agreement=0, strengths_required=ordered,
            source_resolution=source_resolution,
            reason=(f"{seen_by_loosest} pivot(s) seen at strength {ordered[0]}, none survived "
                    f"every strength in {ordered} -- parameter artifacts, not levels"),
        )

    levels: List[PriceLevel] = []
    for swing in survivors:
        tolerance = abs(swing.price) * touch_tolerance_fraction
        touches, last_touch = count_touches(usable, swing.price, swing.bar_index, tolerance)
        levels.append(PriceLevel(
            price=swing.price, kind=swing.kind, formed_at=swing.timestamp,
            formed_bar_index=swing.bar_index, touch_count=touches,
            last_touch_at=last_touch, strength=_strength_bucket(touches),
            detected_at_strengths=swing.detected_at_strengths,
            source_resolution=source_resolution, touch_tolerance=tolerance,
        ))

    # Deterministic order: same bars in, same order out, every time.
    levels.sort(key=lambda lvl: (lvl.price, lvl.kind))
    return LevelSet(
        status=taxonomy.LEVELS_AVAILABLE, levels=tuple(levels), as_of=as_of,
        bars_considered=len(usable), swings_before_agreement=seen_by_loosest,
        swings_after_agreement=len(survivors), strengths_required=ordered,
        source_resolution=source_resolution,
    )


def detect_zones(
    bars: Sequence[Bar],
    *,
    multiples: Sequence[float] = taxonomy.DEFAULT_IMPULSE_MULTIPLES,
    range_lookback: int = taxonomy.DEFAULT_RANGE_LOOKBACK,
    source_resolution: str = "",
    as_of: Optional[str] = None,
) -> ZoneSet:
    """Published supply/demand zones, or a named reason there are none.

    Same contract as `detect_levels`: the `as_of` cut is applied FIRST, the
    status distinguishes "none found" from "could not look", and the
    agreement gate's cost is reported rather than hidden.
    """
    ordered = tuple(sorted(set(float(m) for m in multiples)))
    if not ordered:
        raise ValueError("at least one impulse multiple is required")

    usable: List[Bar] = [b for b in bars if as_of is None or b.timestamp < as_of]

    # A zone needs bars to measure a typical range against, plus an origin
    # and the impulse that followed it. Fewer than that is not "no zones" --
    # it is not enough evidence to have an opinion.
    minimum = range_lookback + 2
    if len(usable) < minimum:
        return ZoneSet(
            status=taxonomy.LEVELS_INSUFFICIENT_HISTORY, as_of=as_of,
            bars_considered=len(usable), multiples_required=ordered,
            source_resolution=source_resolution,
            reason=(f"{len(usable)} bars available; a zone needs at least {minimum} "
                    f"({range_lookback} to measure a typical range, plus an origin bar "
                    f"and its impulse)"),
        )

    zones, seen_by_loosest = build_zones(usable, ordered, range_lookback, source_resolution)

    if not zones:
        return ZoneSet(
            status=taxonomy.LEVELS_NO_AGREEMENT, as_of=as_of,
            bars_considered=len(usable), zones_before_agreement=seen_by_loosest,
            zones_after_agreement=0, multiples_required=ordered,
            source_resolution=source_resolution,
            reason=(f"{seen_by_loosest} candidate zone(s) at the loosest impulse threshold "
                    f"{ordered[0]}x, none survived every threshold in {ordered} -- artifacts "
                    f"of where the threshold was set, not places the market cared about"),
        )

    return ZoneSet(
        status=taxonomy.LEVELS_AVAILABLE, zones=tuple(zones), as_of=as_of,
        bars_considered=len(usable), zones_before_agreement=seen_by_loosest,
        zones_after_agreement=len(zones), multiples_required=ordered,
        source_resolution=source_resolution,
    )

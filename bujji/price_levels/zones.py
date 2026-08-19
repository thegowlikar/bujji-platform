"""Supply and demand zones: where a move came from, and whether it held.

A zone is the price band a bar occupied immediately before price left it in a
hurry. The convention here is the plain one, stated rather than implied: the
ORIGIN bar is the bar directly preceding an impulse, and the zone is that
bar's own high-low range. No smoothing, no widening, no "institutional
candle" heuristics -- the band is a range price actually traded in.

SCALE COMES FROM THE SERIES, NOT FROM A CONSTANT. "A big move" is measured
against the median true range of the preceding bars, so the same code means
the same thing at NIFTY 8,000 and NIFTY 24,000. A hardcoded point threshold
would silently become stricter every year the index rose.

THE AGREEMENT GATE AGAIN, on a different parameter. A zone that exists only
because the impulse threshold was set at 1.5x is an artifact of that choice.
A zone must be produced at EVERY configured multiple to be published, and the
cost of that gate is reported alongside the survivors.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from . import taxonomy
from .models import Bar, SupplyDemandZone


def true_range(bar: Bar, previous: Optional[Bar]) -> float:
    """Standard true range: the bar's own span, extended to include a gap
    from the previous close. A gap is real movement and excluding it would
    understate what this series typically does."""
    span = bar.high - bar.low
    if previous is None:
        return span
    return max(span, abs(bar.high - previous.close), abs(bar.low - previous.close))


def typical_range(bars: Sequence[Bar], end_index: int, lookback: int) -> Optional[float]:
    """Median true range over the bars BEFORE `end_index`.

    Median, not mean: one gap day should not redefine what is typical.
    Returns None when there is not enough history to have an opinion -- the
    caller then forms no zone, rather than forming one against a guess.
    """
    start = max(0, end_index - lookback)
    window = bars[start:end_index]
    if len(window) < 2:
        return None
    ranges = [true_range(window[i], window[i - 1] if i > 0 else None)
              for i in range(len(window))]
    value = median(ranges)
    return value if value > 0 else None


def detect_zones_at_multiple(
    bars: Sequence[Bar], multiple: float, lookback: int = taxonomy.DEFAULT_RANGE_LOOKBACK,
) -> Dict[Tuple[int, str], Tuple[float, float, float]]:
    """Candidate zones at ONE impulse threshold.

    Keyed by (origin_bar_index, kind) -- the same identity discipline swings
    use. Value is (lower, upper, impulse_size). An impulse is measured
    close-to-close over the single bar following the origin: deliberately the
    simplest defensible definition, and disclosed as such.
    """
    if multiple <= 0:
        raise ValueError(f"impulse multiple must be > 0, got {multiple}")
    out: Dict[Tuple[int, str], Tuple[float, float, float]] = {}
    for i in range(len(bars) - 1):
        scale = typical_range(bars, i, lookback)
        if scale is None:
            continue
        origin, impulse_bar = bars[i], bars[i + 1]
        move = impulse_bar.close - origin.close
        if abs(move) < multiple * scale:
            continue
        kind = taxonomy.ZONE_DEMAND if move > 0 else taxonomy.ZONE_SUPPLY
        out[(i, kind)] = (origin.low, origin.high, abs(move))
    return out


def zones_surviving_agreement(
    bars: Sequence[Bar],
    multiples: Sequence[float] = taxonomy.DEFAULT_IMPULSE_MULTIPLES,
    lookback: int = taxonomy.DEFAULT_RANGE_LOOKBACK,
) -> Tuple[List[Tuple[int, str, float, float, float]], int]:
    """Zones produced at EVERY multiple, plus how many the loosest one saw.

    Note the direction of the gate: a larger multiple is STRICTER, so
    survivors are exactly the zones the strictest threshold also produced.
    Requiring all of them makes that explicit rather than relying on a
    reader to know which end of the parameter is conservative.
    """
    if not multiples:
        raise ValueError("at least one impulse multiple is required")
    ordered = tuple(sorted(set(float(m) for m in multiples)))

    per_multiple = {m: detect_zones_at_multiple(bars, m, lookback) for m in ordered}
    loosest = per_multiple[ordered[0]]

    survivors = []
    for key, (lower, upper, impulse) in loosest.items():
        if all(key in per_multiple[m] for m in ordered):
            survivors.append((key[0], key[1], lower, upper, impulse))
    survivors.sort(key=lambda z: (z[0], z[1]))
    return survivors, len(loosest)


def track_zone(
    bars: Sequence[Bar], origin_index: int, kind: str, lower: float, upper: float,
) -> Tuple[str, int, Optional[str], Optional[str]]:
    """What happened to this zone AFTER it formed.

    Returns (status, test_count, last_test_at, broken_at). Scanning starts
    after the impulse bar: the impulse itself is what created the zone, not
    a test of it.

    BROKEN is decided on a CLOSE through the far side, not a wick. A wick
    through and back is exactly the test a zone is supposed to survive, and
    calling that a break would retire every zone on its first real test.
    """
    tests = 0
    last_test: Optional[str] = None
    for bar in bars[origin_index + 2:]:
        if kind == taxonomy.ZONE_DEMAND:
            if bar.close < lower:
                return taxonomy.ZONE_BROKEN, tests, last_test, bar.timestamp
            entered = bar.low <= upper
        else:
            if bar.close > upper:
                return taxonomy.ZONE_BROKEN, tests, last_test, bar.timestamp
            entered = bar.high >= lower
        if entered:
            tests += 1
            last_test = bar.timestamp
    status = taxonomy.ZONE_TESTED if tests else taxonomy.ZONE_FRESH
    return status, tests, last_test, None


def build_zones(
    bars: Sequence[Bar],
    multiples: Sequence[float] = taxonomy.DEFAULT_IMPULSE_MULTIPLES,
    lookback: int = taxonomy.DEFAULT_RANGE_LOOKBACK,
    source_resolution: str = "",
) -> Tuple[List[SupplyDemandZone], int]:
    """Published zones with their lifecycle resolved, plus the gate's cost."""
    survivors, seen_by_loosest = zones_surviving_agreement(bars, multiples, lookback)
    ordered = tuple(sorted(set(float(m) for m in multiples)))

    zones: List[SupplyDemandZone] = []
    for origin_index, kind, lower, upper, impulse in survivors:
        status, tests, last_test, broken_at = track_zone(bars, origin_index, kind, lower, upper)
        zones.append(SupplyDemandZone(
            lower=lower, upper=upper, kind=kind,
            formed_at=bars[origin_index].timestamp, formed_bar_index=origin_index,
            impulse_size=impulse, status=status, test_count=tests,
            last_test_at=last_test, broken_at=broken_at,
            detected_at_multiples=ordered, source_resolution=source_resolution,
        ))
    zones.sort(key=lambda z: (z.lower, z.kind))
    return zones, seen_by_loosest

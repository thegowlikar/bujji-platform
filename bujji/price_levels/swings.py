"""Swing detection, and the agreement gate that decides what counts.

A pivot high at strength N is a bar whose high is strictly greater than the
high of each of the N bars on either side. Strictly: a tie means the pivot is
not unique, and publishing both sides of a flat top as two levels would
double-count one piece of structure.

THE AGREEMENT GATE IS THE POINT OF THIS MODULE. Any charting tool will draw
pivots at one parameter and they will look convincing. A pivot visible at
strength 3 and gone at strength 8 is an artifact of the parameter, not a
level in the market -- the identical failure the regime stability gate
already catches, where derived structure moved with the sampling interval
alone and nothing else. So a swing is published only if EVERY configured
strength sees it.

This is expected to be expensive in level count, and that is the intended
trade. Twenty levels that survive scrutiny beat two hundred that don't, and
if almost nothing survives, that is a real finding about the method rather
than a number to tune away.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from . import taxonomy
from .models import Bar, SwingPoint


def detect_swings_at_strength(bars: Sequence[Bar], strength: int) -> List[SwingPoint]:
    """Every pivot high and pivot low at ONE strength.

    Pure and total: no clock, no I/O, no randomness. The first and last
    `strength` bars can never be pivots -- there is not enough evidence on
    both sides -- and are skipped rather than judged on partial evidence.
    """
    if strength < 1:
        raise ValueError(f"strength must be >= 1, got {strength}")
    out: List[SwingPoint] = []
    n = len(bars)
    for i in range(strength, n - strength):
        bar = bars[i]
        left = bars[i - strength:i]
        right = bars[i + 1:i + 1 + strength]
        if all(bar.high > b.high for b in left) and all(bar.high > b.high for b in right):
            out.append(SwingPoint(i, bar.timestamp, bar.high, taxonomy.LEVEL_SWING_HIGH, (strength,)))
        if all(bar.low < b.low for b in left) and all(bar.low < b.low for b in right):
            out.append(SwingPoint(i, bar.timestamp, bar.low, taxonomy.LEVEL_SWING_LOW, (strength,)))
    return out


def swings_surviving_agreement(
    bars: Sequence[Bar], strengths: Sequence[int] = taxonomy.DEFAULT_SWING_STRENGTHS,
) -> Tuple[List[SwingPoint], int]:
    """Pivots seen by EVERY strength, plus how many the loosest one saw.

    Returns `(survivors, seen_by_loosest)`. The second number is what makes
    the gate's cost visible: a caller can report "11 of 214 pivots survived"
    rather than quietly publishing 11 and implying that was all there was.

    Identity is (bar_index, kind) -- the same pivot bar, not a price within
    some tolerance. Two detections either point at the same bar or they are
    about different structure; there is no judgement call to fudge.
    """
    if not strengths:
        raise ValueError("at least one detection strength is required")
    ordered = tuple(sorted(set(int(s) for s in strengths)))

    per_strength: Dict[int, Dict[Tuple[int, str], SwingPoint]] = {}
    for strength in ordered:
        per_strength[strength] = {
            (sw.bar_index, sw.kind): sw for sw in detect_swings_at_strength(bars, strength)
        }

    loosest = per_strength[ordered[0]]
    seen_by_loosest = len(loosest)

    survivors: List[SwingPoint] = []
    for key, swing in loosest.items():
        if all(key in per_strength[s] for s in ordered):
            survivors.append(SwingPoint(
                bar_index=swing.bar_index, timestamp=swing.timestamp, price=swing.price,
                kind=swing.kind, detected_at_strengths=ordered,
            ))
    survivors.sort(key=lambda sw: (sw.bar_index, sw.kind))
    return survivors, seen_by_loosest

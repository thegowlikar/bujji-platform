"""Episode Timeline comparison — Phase 17J.4.

Per `docs/PHASE_17J4_EPISODE_TIMELINE_COMPARISON_DESIGN.md`: compares
two sessions by their STRUCTURAL PATH (a fixed-interval sequence of
`SituationFeatureVector`s across the session), not a single
end-of-session snapshot -- the snapshot-only approach (17J.2 revisit)
was empirically shown to fail (`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`)
and 17J.3 explained why structurally: `Episode`/assessment objects were
designed as growth snapshots, never a summary value.

CHECKPOINT SCHEDULE: fixed 30-minute wall-clock intervals from market
open to close (design doc §2, Option A) -- every session gets the same
NOMINAL checkpoint count and the same time-of-day at each index, so two
timelines align by POSITION with no warping/alignment algorithm needed.
First-pass, disclosed interval (30 min), not a validated conclusion --
same convention as `lookback_bars=75`.

Missing checkpoints (insufficient trailing data, e.g. right after
09:15 open) are kept as `None` PLACEHOLDERS at their correct index,
never dropped -- dropping them would silently misalign later
checkpoints between two timelines of different missing-checkpoint
counts. `compare_timelines()` skips `None` positions from its average,
never treats them as a match or a mismatch.

NO new weights, scoring formula, ML, or clustering: `compare_timelines()`
is a plain mean of the already-decided `compare()` exact-match-fraction
across aligned positions.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import List, Optional, Tuple

from bujji.market_understanding.similarity import SituationFeatureVector, build_feature_vector, compare
from bujji.market_understanding.structure import IntradayStructureCatalog
from bujji.reality_memory.catalog import RealityMemoryCatalog

SCHEMA_VERSION = "1.0.0"

CHECKPOINT_INTERVAL_MINUTES = 30
MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30 -- see reality_structure_bridge's
# own note; harmless for pre-2026-08-03 dates since no bars exist past their real
# historical close anyway, so this checkpoint just resolves to the same data as
# the real close would.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _checkpoint_times(date: str, interval_minutes: int) -> List[str]:
    day = datetime.date.fromisoformat(date)
    cursor = datetime.datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    close = datetime.datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    step = datetime.timedelta(minutes=interval_minutes)
    times: List[str] = []
    while cursor < close:
        times.append(cursor.isoformat())
        cursor += step
    times.append(close.isoformat())  # always include the exact close, even off-cycle.
    return times


@dataclass(frozen=True)
class EpisodeTimeline:
    """`checkpoints` is always the SAME length as the nominal schedule
    for `interval_minutes` -- `None` at any index a checkpoint could
    not be computed (insufficient trailing bars), never shortened."""

    instrument_identity: str
    date: str
    interval_minutes: int
    checkpoint_times: Tuple[str, ...]
    checkpoints: Tuple[Optional[SituationFeatureVector], ...]
    schema_version: str = SCHEMA_VERSION


def build_episode_timeline(
    instrument_identity: str, date: str, *,
    structure_catalog: IntradayStructureCatalog, memory_catalog: RealityMemoryCatalog,
    interval_minutes: int = CHECKPOINT_INTERVAL_MINUTES,
) -> Optional[EpisodeTimeline]:
    """Returns None only if EVERY checkpoint in the nominal schedule is
    unavailable (e.g. the date has no Reality data at all) -- a
    partially-populated timeline (some None checkpoints) is still
    returned, honestly."""
    times = _checkpoint_times(date, interval_minutes)
    checkpoints = tuple(
        build_feature_vector(
            instrument_identity, date, t,
            structure_catalog=structure_catalog, memory_catalog=memory_catalog,
        )
        for t in times
    )
    if all(c is None for c in checkpoints):
        return None
    return EpisodeTimeline(
        instrument_identity=instrument_identity, date=date, interval_minutes=interval_minutes,
        checkpoint_times=tuple(times), checkpoints=checkpoints,
    )


def compare_timelines(a: EpisodeTimeline, b: EpisodeTimeline) -> Optional[float]:
    """Position-wise `compare()`, averaged over positions where BOTH
    timelines have a real checkpoint. Returns None (never 0.0) if the
    schedules don't match in length (an undefined comparison, not a
    zero-similarity fact) or if zero positions are comparable."""
    if len(a.checkpoints) != len(b.checkpoints):
        return None
    scores: List[float] = []
    for checkpoint_a, checkpoint_b in zip(a.checkpoints, b.checkpoints):
        if checkpoint_a is None or checkpoint_b is None:
            continue
        score = compare(checkpoint_a, checkpoint_b)
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    return sum(scores) / len(scores)

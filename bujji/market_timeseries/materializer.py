"""Tick -> Candle Materializer -- Phase 17F.1.

Wires `bujji.market_reality` (Layer 0) into `CandleAggregator` (Phase
15Q). The aggregator's arithmetic -- wall-clock-aligned windows,
first/max/min/last OHLC, refusal to fabricate a candle from an empty
window -- is REUSED UNCHANGED. This module adds only what Phase 17F.0
specified as missing: provenance.

A MATERIALIZER, BY DEFINITION (Phase 17F design): a deterministic, pure
function of Layer 0 records. No wall-clock read (the caller supplies
`as_of`), no randomness, no network, no hidden state. Calling this
function twice with the same `reality_store` and `as_of` must produce
byte-identical output -- that determinism is what makes the
`REPLAY_VERIFIED` proof (Phase 17D Part 5) meaningful.

This module NEVER writes to `CandleStore` itself. It returns
provenance-stamped `Candle` objects; the caller decides whether/where to
persist them. Keeping materialization and persistence separate is what
lets the rebuild proof run repeatedly with nothing to reset.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional

from bujji.epistemics.lineage import calc_version_for
from bujji.market_reality import replay as reality_replay
from bujji.market_reality import taxonomy as reality_taxonomy

from .aggregator import CandleAggregator, window_bounds
from .models import Candle

MATERIALIZER_ID = "tick_to_candle"

# The exact aggregation definition being content-hashed. Changing this
# string (or the parameters passed to calc_version_for) changes
# calc_version automatically -- per epistemics.lineage.calc_version_for's
# own design, this is never a hand-maintained integer someone can forget
# to bump.
_DEFINITION_SOURCE = (
    "OHLC = (first tick, max tick, min tick, last tick) over a wall-clock-"
    "aligned window; volume = sum of tick volumes when any tick reports "
    "one, else None; a window with zero ticks produces no candle. "
    "(bujji.market_timeseries.aggregator.CandleAggregator, unmodified.)"
)

# Only these Layer 0 kinds carry a price this materializer folds into a
# candle. MARKET_DEPTH/OPTION_CHAIN/CANDLE are separate materializers'
# inputs (17F.1.2/17F.1.3, not built here); CAPTURE_EVENT is excluded
# structurally by `reality_replay.replay()` itself, before this module
# ever sees it.
_FOLDABLE_KINDS = (reality_taxonomy.KIND_MARKET_TICK, reality_taxonomy.KIND_QUOTE)


def default_calc_version(interval: str) -> str:
    """The calc_version this materializer produces for a given interval,
    when the caller doesn't pin an explicit one. A read that wants "the
    current definition's candles" resolves it via this function, never
    by guessing a string."""
    return calc_version_for(_DEFINITION_SOURCE, {"interval": interval})


def materialize_candles(
    *,
    reality_store,
    instrument: str,
    kind: str,
    interval: str,
    as_of: str,
    calc_version: Optional[str] = None,
) -> List[Candle]:
    """Replay Layer 0 observations for `instrument` through the existing
    `CandleAggregator` and return provenance-stamped, UNWRITTEN candles.

    `as_of` is mandatory (no default) -- the same no-lookahead discipline
    as every Layer 0/1 read in this project. Only observations with
    `event_time <= as_of` (Layer 0's own `replay()` contract) are folded
    in, and every emitted candle's `knowledge_boundary` is stamped with
    this same `as_of` -- so a query later asking "what did this
    materializer know as of T" gets an honest answer.

    Read-only on `reality_store`: this function only calls `replay()` and
    `replay_capture_events()`, never writes to Layer 0.
    """
    resolved_version = calc_version or default_calc_version(interval)

    # Per-window accumulation, keyed by the SAME window_bounds() helper
    # the aggregator itself uses internally -- so ids/event-times line up
    # with the candles it emits without reaching into its internals or
    # duplicating its windowing logic.
    window_observation_ids: Dict[str, List[str]] = {}
    window_event_times: Dict[str, List[str]] = {}
    emitted: List[Candle] = []

    def _on_candle(candle: Candle) -> None:
        key = candle.window_start
        obs_ids = tuple(window_observation_ids.get(key, ()))
        event_times = sorted(t for t in window_event_times.get(key, ()) if t)
        emitted.append(replace(
            candle,
            source_observation_ids=obs_ids,
            materializer_id=MATERIALIZER_ID,
            calc_version=resolved_version,
            first_event_time=event_times[0] if event_times else None,
            last_event_time=event_times[-1] if event_times else None,
            knowledge_boundary=as_of,
            transformation_history=(
                reality_taxonomy.TRANSFORMATION_RAW_CAPTURE,
                f"CANDLE:{MATERIALIZER_ID}:{resolved_version}",
            ),
        ))

    agg = CandleAggregator(interval=interval, on_candle=_on_candle)

    for observation in reality_replay.replay(reality_store, as_of=as_of):
        if observation.instrument != instrument or observation.kind not in _FOLDABLE_KINDS:
            continue
        payload = observation.payload
        price = payload.get("ltp") if isinstance(payload, dict) else None
        if price is None:
            continue  # A tick/quote that never reached a price is not foldable evidence.
        volume = payload.get("volume") if isinstance(payload, dict) else None
        event_time = observation.lineage.event_timestamp or observation.lineage.capture_timestamp
        start, _ = window_bounds(event_time, interval)
        window_observation_ids.setdefault(start, []).append(observation.observation_id)
        window_event_times.setdefault(start, []).append(event_time)
        agg.ingest(instrument, event_time, float(price), kind=kind, volume=volume)

    agg.flush()  # Closes any still-open window; emits via the SAME on_candle hook above.

    # Gap-overlap flagging: a candle built over a window Bujji was
    # partially blind for must say so (Phase 17F.0.1 Part 5.3) -- cross-
    # reference every CaptureEvent whose event_time falls inside each
    # candle's [window_start, window_end).
    capture_events = reality_replay.replay_capture_events(reality_store, as_of=as_of)
    final: List[Candle] = []
    for candle in emitted:
        overlapping = tuple(
            ev.event_id for ev in capture_events
            if ev.event_time is not None and candle.window_start <= ev.event_time < candle.window_end
        )
        final.append(replace(candle, capture_event_overlap=overlapping) if overlapping else candle)
    return final

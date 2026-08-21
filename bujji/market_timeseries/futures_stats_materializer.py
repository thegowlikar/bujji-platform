"""Futures Statistics Materializer -- Phase 17F.1.2.

A deterministic, pure function of Layer 0 (`bujji.market_reality`)
`MARKET_DEPTH` observations plus already-materialized `Candle` rows
(futures, and spot when computing basis). No wall-clock read (the
caller supplies `as_of`), no randomness, no network, no hidden state.
Calling this function twice against the same `reality_store`,
`candle_store` content, and `as_of` must produce byte-identical output
-- the same `REPLAY_VERIFIED` discipline as `materializer.py` (Tick ->
Candle, 17F.1).

Never writes to `CandleStore` or `FuturesStatsStore` itself -- returns
unwritten `FuturesStatistics` records; the caller decides whether/where
to persist them, exactly like `materialize_candles()`.

REUSE, NOT REINVENTION (per the design doc's audit, Part 1.2):
  * `indicators.realised_volatility()` is called directly for
    `price_volatility` -- never reimplemented.
  * `epistemics.uncertainty.compose()` builds `quality` -- never a
    bespoke score.
  * `epistemics.lineage.calc_version_for()` mints this materializer's
    own `calc_version` -- content-hashed, never hand-bumped, and
    INDEPENDENT of the Tick->Candle materializer's own `calc_version`
    (a change to OI/basis logic must never silently invalidate price
    candles, or vice versa).
  * `aggregator.window_bounds()` provides the exact same window grid
    Candle uses, so the two series line up without reimplementing
    windowing.

SCOPE LOCK (design doc Part 2, restated): every field here is a
deterministic transformation of already-stored facts. Nothing here
infers trader intent, labels a bias, calls a level support/resistance,
or fuses OI and price into a causal-sounding conclusion. OI change and
price change are reported side by side, never combined.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from bujji.epistemics.lineage import calc_version_for
from bujji.epistemics.uncertainty import GAP, KNOWN, Input, Uncertainty, compose, unknown
from bujji.market_reality import replay as reality_replay
from bujji.market_reality import taxonomy as reality_taxonomy

from . import indicators
from .aggregator import window_bounds
from .futures_stats_models import (
    BOOK_STATE_NOT_OBSERVED,
    BOOK_STATE_OBSERVED_EMPTY,
    BOOK_STATE_OBSERVED_NONEMPTY,
    FuturesStatistics,
)
from .materializer import MATERIALIZER_ID as CANDLE_MATERIALIZER_ID
from .materializer import default_calc_version as default_candle_calc_version

MATERIALIZER_ID = "futures_stats"

# The exact transformation definition being content-hashed (Part 5 of the
# design doc). Changing this string, or `volatility_period`, changes
# `calc_version` automatically -- never a hand-maintained integer.
_DEFINITION_SOURCE = (
    "FuturesStatistics: oi_open/oi_close = first/last non-null 'oi' among "
    "MARKET_DEPTH observations in [window_start, window_end); oi_change = "
    "oi_close - oi_open only if both non-null; book_state/top_bid_size_last/"
    "top_ask_size_last from the LAST MARKET_DEPTH observation in the window "
    "(OBSERVED_NONEMPTY/OBSERVED_EMPTY/NOT_OBSERVED, three states); "
    "price_change/volume read from the referenced futures Candle "
    "(close - open), never re-derived from raw ticks; basis = futures.close "
    "- spot.close, basis_percent = basis / spot.close * 100, both only if "
    "both Candles exist for the identical key; price_volatility = "
    "market_timeseries.indicators.realised_volatility() over trailing "
    "futures Candles ending at this window, called unmodified. "
    "(bujji.market_timeseries.futures_stats_materializer, unmodified.)"
)


def default_calc_version(interval: str, volatility_period: int = 20) -> str:
    return calc_version_for(
        _DEFINITION_SOURCE, {"interval": interval, "volatility_period": volatility_period}
    )


def _compose_quality(
    *, has_candle: bool, has_depth: bool, capture_reasons: Tuple[str, ...]
) -> Uncertainty:
    """Built via `epistemics.uncertainty` composition, never invented.

    Missing candle or missing depth in a window is real (a genuine gap in
    one series, not the other) and demotes confidence one band without
    gating the whole record -- both are declared non-critical. A
    `CaptureEvent` overlapping the window is a genuine blind spot and IS
    critical: it taints the record to `GAP`, confidence capped at LOW,
    with `limiting_factor` naming the capture reason(s).
    """
    inputs = [
        Input("futures_candle",
              Uncertainty(state=KNOWN) if has_candle else unknown("no futures Candle for window"),
              critical=False),
        Input("market_depth",
              Uncertainty(state=KNOWN) if has_depth else unknown("no MARKET_DEPTH observation in window"),
              critical=False),
        Input(
            f"capture_event:{','.join(sorted(set(capture_reasons)))}" if capture_reasons else "capture_event",
            Uncertainty(state=GAP) if capture_reasons else Uncertainty(state=KNOWN),
            critical=True,
        ),
    ]
    return compose(inputs, base_confidence="HIGH", range_dependent=False)


def materialize_futures_stats(
    *,
    reality_store,
    candle_store,
    futures_instrument: str,
    interval: str,
    window_start: str,
    window_end: str,
    as_of: str,
    spot_instrument: Optional[str] = None,
    candle_calc_version: Optional[str] = None,
    calc_version: Optional[str] = None,
    volatility_period: int = 20,
) -> List[FuturesStatistics]:
    """Materialize `FuturesStatistics` for every window in
    `[window_start, window_end)` that has EITHER a futures Candle OR at
    least one `MARKET_DEPTH` observation -- a window with neither has no
    evidence to report and is silently absent, exactly like a Candle
    store never rows a zero-tick window.

    `window_start`/`window_end` are REQUIRED and explicit (unlike
    `materialize_candles()`, which processes everything `replay()`
    yields) -- this materializer must not silently scan Layer 0's full
    history on every call; the caller decides the range, exactly like
    `CandleStore.range()`'s own required bounds.

    `candle_calc_version` defaults to the Tick->Candle materializer's own
    current definition (`materializer.default_calc_version`) -- the
    caller only needs to override it when reading Candles produced under
    a non-default aggregation rule.
    """
    resolved_candle_cv = candle_calc_version or default_candle_calc_version(interval)
    resolved_version = calc_version or default_calc_version(interval, volatility_period)

    _EPOCH = "0001-01-01T00:00:00+00:00"

    futures_candles = candle_store.range(
        futures_instrument, interval, window_start, window_end,
        as_of=as_of, calc_version=resolved_candle_cv,
    )
    candles_by_window: Dict[str, object] = {c.window_start: c for c in futures_candles}

    spot_candles_by_window: Dict[str, object] = {}
    if spot_instrument:
        spot_candles = candle_store.range(
            spot_instrument, interval, window_start, window_end,
            as_of=as_of, calc_version=resolved_candle_cv,
        )
        spot_candles_by_window = {c.window_start: c for c in spot_candles}

    # -- Bucket Layer 0 MARKET_DEPTH observations for the futures
    # instrument by window, using the SAME window_bounds() helper Candle
    # uses, so the two series share an identical grid without
    # reimplementing windowing.
    depth_ids: Dict[str, List[str]] = {}
    depth_event_times: Dict[str, List[str]] = {}
    depth_oi_by_window: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    depth_last_by_window: Dict[str, dict] = {}

    for observation in reality_replay.replay(reality_store, as_of=as_of):
        if (
            observation.instrument != futures_instrument
            or observation.kind != reality_taxonomy.KIND_MARKET_DEPTH
        ):
            continue
        event_time = observation.lineage.event_timestamp or observation.lineage.capture_timestamp
        start, end = window_bounds(event_time, interval)
        if start < window_start or start >= window_end:
            continue  # Outside the caller's explicit range -- bounded, not a full scan.

        payload = observation.payload if isinstance(observation.payload, dict) else {}
        depth_ids.setdefault(start, []).append(observation.observation_id)
        depth_event_times.setdefault(start, []).append(event_time)
        depth_oi_by_window.setdefault(start, []).append((event_time, payload.get("oi")))

        last = depth_last_by_window.get(start)
        if last is None or event_time >= last["event_time"]:
            depth_last_by_window[start] = {"event_time": event_time, "payload": payload}

    capture_events = reality_replay.replay_capture_events(reality_store, as_of=as_of)

    window_starts = sorted(set(candles_by_window) | set(depth_last_by_window))

    results: List[FuturesStatistics] = []
    for ws in window_starts:
        candle = candles_by_window.get(ws)
        we = candle.window_end if candle is not None else window_bounds(ws, interval)[1]

        spot_candle = spot_candles_by_window.get(ws)

        # -- OI: first/last non-null 'oi' among this window's depth obs,
        # ordered by event_time. Absence stays absence.
        oi_points = sorted(depth_oi_by_window.get(ws, ()), key=lambda p: p[0])
        oi_values = [(t, v) for t, v in oi_points if v is not None]
        oi_open = oi_values[0][1] if oi_values else None
        oi_close = oi_values[-1][1] if oi_values else None
        oi_change = (oi_close - oi_open) if (oi_open is not None and oi_close is not None) else None

        depth_observation_count = len(depth_ids.get(ws, ()))
        oi_observation_count = len(oi_values)

        # -- book_state / top-of-book from the LAST depth observation.
        last_depth = depth_last_by_window.get(ws)
        if last_depth is None:
            book_state = BOOK_STATE_NOT_OBSERVED
            top_bid = None
            top_ask = None
        else:
            bids = last_depth["payload"].get("bids") or []
            asks = last_depth["payload"].get("asks") or []
            if bids and asks:
                book_state = BOOK_STATE_OBSERVED_NONEMPTY
                top_bid = bids[0].get("volume") if isinstance(bids[0], dict) else None
                top_ask = asks[0].get("volume") if isinstance(asks[0], dict) else None
            else:
                book_state = BOOK_STATE_OBSERVED_EMPTY
                top_bid = None
                top_ask = None

        # -- price_change/volume: referenced from the Candle, never
        # re-derived from raw ticks a second time.
        price_change = (candle.close - candle.open) if candle is not None else None
        volume = candle.volume if candle is not None else None

        # -- basis: SEMANTIC CONTRACT (design doc Part 5, item 5) --
        # basis = futures_candle.close - spot_candle.close, for the SAME
        # (interval, window_start) key, both bounded by the SAME as_of
        # and produced under the SAME candle_calc_version. This is a
        # comparison of two already-closed, already-windowed bars -- NOT
        # a live futures-premium/discount snapshot pairing an
        # instantaneous futures quote against an instantaneous spot
        # quote. Only if BOTH the futures and spot Candle exist for this
        # exact key; never approximated from a neighboring window.
        #
        # basis is a MEASUREMENT, not an interpretation: a negative
        # basis is not a bearish signal, a positive basis is not a
        # bullish signal. See the design doc's scope lock (Part 2).
        basis = None
        basis_percent = None
        if candle is not None and spot_candle is not None:
            basis = candle.close - spot_candle.close
            if spot_candle.close != 0:
                basis_percent = basis / spot_candle.close * 100

        # -- price_volatility: trailing futures Candles ending at this
        # window's close, delegated to indicators.realised_volatility()
        # unmodified.
        price_volatility = None
        if candle is not None:
            trailing_full = candle_store.range(
                futures_instrument, interval, _EPOCH, we, as_of=as_of, calc_version=resolved_candle_cv,
            )
            price_volatility = indicators.realised_volatility(trailing_full, period=volatility_period)

        # -- capture_event_overlap: any CaptureEvent whose event_time
        # falls inside [ws, we).
        overlapping = tuple(
            ev.event_id for ev in capture_events
            if ev.event_time is not None and ws <= ev.event_time < we
        )
        overlapping_reasons = tuple(
            ev.reason for ev in capture_events
            if ev.event_id in overlapping
        )

        # -- referenced_candle_keys: explicit pointers, never a copy.
        referenced_keys: List[Tuple[str, str, str, str]] = []
        if candle is not None:
            referenced_keys.append((futures_instrument, interval, ws, resolved_candle_cv))
        if spot_candle is not None:
            referenced_keys.append((spot_instrument, interval, ws, resolved_candle_cv))

        source_ids = tuple(depth_ids.get(ws, ()))
        candidate_event_times = list(depth_event_times.get(ws, ()))
        if candle is not None:
            if candle.first_event_time:
                candidate_event_times.append(candle.first_event_time)
            if candle.last_event_time:
                candidate_event_times.append(candle.last_event_time)
        candidate_event_times = sorted(t for t in candidate_event_times if t)

        transformation_history = (
            reality_taxonomy.TRANSFORMATION_RAW_CAPTURE,
            f"CANDLE:{CANDLE_MATERIALIZER_ID}:{resolved_candle_cv}",
            f"FUTURES_STATS:{MATERIALIZER_ID}:{resolved_version}",
        )

        quality = _compose_quality(
            has_candle=candle is not None,
            has_depth=bool(oi_points) or depth_observation_count > 0,
            capture_reasons=overlapping_reasons,
        )

        results.append(FuturesStatistics(
            instrument=futures_instrument, interval=interval,
            window_start=ws, window_end=we,
            source_observation_ids=source_ids,
            referenced_candle_keys=tuple(referenced_keys),
            materializer_id=MATERIALIZER_ID,
            calc_version=resolved_version,
            transformation_history=transformation_history,
            first_event_time=candidate_event_times[0] if candidate_event_times else None,
            last_event_time=candidate_event_times[-1] if candidate_event_times else None,
            knowledge_boundary=as_of,
            capture_event_overlap=overlapping,
            oi_open=oi_open, oi_close=oi_close, oi_change=oi_change,
            oi_observation_count=oi_observation_count,
            depth_observation_count=depth_observation_count,
            volume=volume, price_change=price_change,
            book_state=book_state,
            top_bid_size_last=top_bid, top_ask_size_last=top_ask,
            basis=basis, basis_percent=basis_percent,
            price_volatility=price_volatility,
            quality=quality,
        ))

    return results

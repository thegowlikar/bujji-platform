"""Tick -> 5-minute OHLC aggregator -- Phase 15Q.

REUSES `bujji.live_observation` for all tick-window mechanics
(`new_window`, `add_tick`, `close_window`, `Tick`, `LateTick`) rather
than reimplementing them -- that package already handles late/
out-of-order ticks correctly and already refuses to fabricate a candle
from an empty window. This module adds only what was missing:

  1. multi-instrument routing (one rolling window per instrument),
  2. automatic window rollover on the wall-clock boundary,
  3. folding a closed window into a persistable `Candle`,
  4. exposing the currently-forming ("live ticking") bar.

No broker import, no execution, no order placement -- this module is
fed ticks by a caller; it never opens a socket itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from bujji.live_observation.engine import add_tick, close_window, new_window
from bujji.live_observation.models import AggregationWindow, Tick

from .models import Candle, FormingCandle, INTERVAL_FIVE_MINUTE, KIND_OPTION, KIND_SPOT, KIND_VIX

_INTERVAL_SECONDS = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
}


def window_bounds(timestamp: str, interval: str = INTERVAL_FIVE_MINUTE) -> Tuple[str, str]:
    """Floor `timestamp` to its interval boundary. Boundaries are
    aligned to the wall clock (09:15:00, 09:20:00, ...), NOT to the
    first tick seen -- so a restart mid-session produces exactly the
    same window boundaries as an uninterrupted run, and two instruments
    that started streaming at different moments still share a common
    time axis (essential for any cross-instrument analysis)."""
    seconds = _INTERVAL_SECONDS.get(interval)
    if seconds is None:
        raise ValueError(f"unsupported interval {interval!r}")
    dt = datetime.fromisoformat(timestamp)
    epoch = datetime(1970, 1, 1, tzinfo=dt.tzinfo) if dt.tzinfo else datetime(1970, 1, 1)
    elapsed = int((dt - epoch).total_seconds())
    floored = elapsed - (elapsed % seconds)
    start = epoch + timedelta(seconds=floored)
    return start.isoformat(), (start + timedelta(seconds=seconds)).isoformat()


class CandleAggregator:
    """Stateful, single-threaded tick router. One rolling
    `AggregationWindow` per instrument.

    `on_candle` (optional) fires once per genuinely-closed window that
    produced real ticks -- the natural place to persist. A window that
    received ZERO ticks closes silently and produces nothing: a gap in
    the series is preserved as a gap, never as a synthetic flat bar.
    """

    def __init__(
        self,
        interval: str = INTERVAL_FIVE_MINUTE,
        on_candle: Optional[Callable[[Candle], None]] = None,
    ) -> None:
        if interval not in _INTERVAL_SECONDS:
            raise ValueError(f"unsupported interval {interval!r}")
        self._interval = interval
        self._on_candle = on_candle
        self._windows: Dict[str, AggregationWindow] = {}
        self._kinds: Dict[str, str] = {}
        self._open_interest: Dict[str, Optional[float]] = {}

    @property
    def interval(self) -> str:
        return self._interval

    @property
    def tracked_instruments(self) -> Tuple[str, ...]:
        return tuple(sorted(self._windows))

    def ingest(
        self, instrument: str, timestamp: str, price: float, *,
        kind: str = KIND_SPOT, volume: Optional[float] = None,
        open_interest: Optional[float] = None,
    ) -> Optional[Candle]:
        """Feed ONE tick. Returns a `Candle` if this tick's arrival
        rolled a previous window shut AND that window had real ticks;
        otherwise None."""
        start, end = window_bounds(timestamp, self._interval)
        self._kinds[instrument] = kind
        if open_interest is not None:
            self._open_interest[instrument] = open_interest

        emitted: Optional[Candle] = None
        current = self._windows.get(instrument)

        if current is not None and start > current.window_start:
            emitted = self._close_and_emit(instrument, current)
            current = None

        if current is None:
            current = new_window(instrument, self._interval, start, end)

        self._windows[instrument] = add_tick(current, Tick(timestamp=timestamp, price=price, volume=volume))
        return emitted

    def _close_and_emit(self, instrument: str, window: AggregationWindow) -> Optional[Candle]:
        closed, observation = close_window(window, source="market_timeseries.aggregator")
        if observation is None:
            return None  # zero real ticks -- an honest gap, never a fabricated bar.
        candle = self._candle_from(instrument, closed)
        if self._on_candle is not None:
            self._on_candle(candle)
        return candle

    def _candle_from(self, instrument: str, window: AggregationWindow) -> Candle:
        """Fold a closed window into a Candle. OHLC is recomputed from
        the window's own accumulated ticks -- identical arithmetic to
        `live_observation.close_window`'s own reduction (first/max/min/
        last), kept here so the persisted row is derived from the raw
        ticks rather than from a re-parsed Observation payload."""
        prices = [t.price for t in window.ticks]
        volumes = [t.volume for t in window.ticks if t.volume is not None]
        return Candle(
            instrument=instrument,
            kind=self._kinds.get(instrument, KIND_SPOT),
            interval=window.interval,
            window_start=window.window_start,
            window_end=window.window_end,
            open=prices[0], high=max(prices), low=min(prices), close=prices[-1],
            volume=(sum(volumes) if volumes else None),
            tick_count=len(window.ticks),
            open_interest=self._open_interest.get(instrument),
        )

    def forming(self, instrument: str) -> Optional[FormingCandle]:
        """The currently-open bar for `instrument` -- the live ticking
        candle. None if no window is open or it has no ticks yet.
        Deliberately returns `FormingCandle`, a distinct type whose
        latest price field is named `last` rather than `close`, so an
        in-progress bar can never be silently consumed as settled
        history by an indicator."""
        window = self._windows.get(instrument)
        if window is None or not window.ticks:
            return None
        prices = [t.price for t in window.ticks]
        volumes = [t.volume for t in window.ticks if t.volume is not None]
        return FormingCandle(
            instrument=instrument, kind=self._kinds.get(instrument, KIND_SPOT),
            interval=window.interval, window_start=window.window_start, window_end=window.window_end,
            open=prices[0], high=max(prices), low=min(prices), last=prices[-1],
            volume=(sum(volumes) if volumes else None), tick_count=len(window.ticks),
        )

    def forming_all(self) -> Dict[str, FormingCandle]:
        out = {}
        for instrument in self._windows:
            f = self.forming(instrument)
            if f is not None:
                out[instrument] = f
        return out

    def flush(self) -> List[Candle]:
        """Close every open window -- call at session end (e.g. 15:20)
        so the final partial bar is settled rather than lost. Windows
        with zero ticks still emit nothing."""
        emitted = []
        for instrument, window in list(self._windows.items()):
            candle = self._close_and_emit(instrument, window)
            if candle is not None:
                emitted.append(candle)
            del self._windows[instrument]
        return emitted

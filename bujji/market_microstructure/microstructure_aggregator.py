"""bujji.market_microstructure.microstructure_aggregator — Phase 19.20.2.

Turns raw ticks into `MinuteObservation`s at 1-minute resolution.

REUSE, NOT REIMPLEMENTATION — an explicit design note:

The obvious approach would be to wrap `bujji.market_timeseries.
aggregator.CandleAggregator` as an opaque black box and consume its
`on_candle` callback. That was tried first and rejected: `CandleAggregator`
discards each instrument's `AggregationWindow` (and therefore its raw
`ticks`) the moment it folds a window into a `Candle`
(`_close_and_emit` -> `_candle_from`, see that module) — the closed
window's ticks are never exposed to the `on_candle` callback. Since
every microstructure field this module computes (tick silence gaps,
average tick interval, tick-to-tick price/premium movement) requires
the raw tick timestamps of a closed window, wrapping `CandleAggregator`
as a black box cannot work without either (a) modifying `CandleAggregator`
itself (forbidden — Phase 19.19/general reuse discipline: don't touch
shared production code for one new caller) or (b) reimplementing window
rollover logic in parallel (forbidden by this phase's own spec).

The correct reuse boundary, and the one this module actually uses: call
the exact same UNDERLYING primitives `CandleAggregator` itself is built
from — `bujji.live_observation.engine.{new_window,add_tick,close_window}`
and `bujji.market_timeseries.aggregator.window_bounds` — directly. These
are pure functions, imported verbatim, never reimplemented. This is a
narrower and more faithful reuse than wrapping `CandleAggregator`, not a
looser one: window opening, rollover-boundary computation, and the
"zero ticks -> no candle, ever" rule are 100% inherited from existing,
tested code; the only genuinely new logic in this file is the
tick-to-tick metadata derivation performed on an already-closed window's
`ticks`, immediately before that window (and its ticks) are discarded.

No broker import. No execution surface. Fed already-decoded
(instrument, timestamp, price) ticks by a caller — never opens a socket.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from bujji.live_observation.engine import add_tick, close_window, new_window
from bujji.live_observation.models import AggregationWindow
from bujji.live_observation.models import Tick as _Tick
from bujji.market_timeseries.aggregator import window_bounds
from bujji.market_timeseries.models import INTERVAL_ONE_MINUTE, KIND_OPTION, KIND_SPOT

from .models import MinuteObservation

# --- Quality-score thresholds (Phase 19.20.2 initial defaults) ------------
# Disclosed, revisable design decision -- not derived from any external
# spec or measurement. See the Phase 19.20.2 verification report for the
# worked examples these were chosen against. Intended to be tuned once
# real market tick density is observed (Phase 19.20.6's real-data step),
# not treated as final.
MIN_HEALTHY_TICKS = 6              # ticks/minute considered "full density" -> no density penalty
SILENCE_TOLERANCE_SECONDS = 20.0   # gaps up to this length incur no penalty
SILENCE_PENALTY_PER_SECOND = 1.5   # score lost per second of silence beyond the tolerance
REJECTED_TICK_PENALTY = 10.0       # score lost per rejected (invalid) tick seen in the window


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _is_valid_tick(timestamp: str, price: float) -> bool:
    """Structural validity only (never a judgment on whether a price is
    'reasonable' -- that boundary matches `bujji.market_observation`'s
    own Observation/Derived-Evidence split). Rejects: non-positive price,
    a timestamp that doesn't parse as ISO 8601."""
    if price is None or price <= 0:
        return False
    try:
        _parse_ts(timestamp)
    except (ValueError, TypeError):
        return False
    return True


class MicrostructureAggregator:
    """Stateful, single-threaded tick router — one rolling
    `AggregationWindow` per instrument, always at `INTERVAL_ONE_MINUTE`.

    Mirrors `CandleAggregator`'s own per-instrument rollover shape
    (see its docstring) but keeps each window's raw ticks accessible
    at close time, purely to derive microstructure metadata before
    discarding them. Nothing here persists a tick anywhere."""

    def __init__(self) -> None:
        self._windows: Dict[str, AggregationWindow] = {}
        self._kinds: Dict[str, str] = {}
        self._open_interest: Dict[str, Optional[float]] = {}
        self._strike: Dict[str, Optional[float]] = {}
        self._option_type: Dict[str, Optional[str]] = {}
        self._rejected_current_window: Dict[str, int] = {}
        self._rejected_total: Dict[str, int] = {}

    @property
    def tracked_instruments(self) -> tuple:
        return tuple(sorted(self._windows))

    def rejected_count(self, instrument: str) -> int:
        """Total invalid ticks seen for `instrument` across this
        aggregator's lifetime (not reset on window rollover) -- an
        integrity signal, distinct from a genuine silent gap."""
        return self._rejected_total.get(instrument, 0)

    def ingest(
        self, instrument: str, timestamp: str, price: float, *,
        kind: str = KIND_SPOT, volume: Optional[float] = None,
        open_interest: Optional[float] = None,
        strike: Optional[float] = None, option_type: Optional[str] = None,
    ) -> Optional[MinuteObservation]:
        """Feed ONE tick. Returns a `MinuteObservation` if this tick's
        arrival rolled a previous window shut AND that window had at
        least one valid tick; `None` otherwise (including: this tick
        was invalid and rejected, or no rollover happened yet)."""
        if not _is_valid_tick(timestamp, price):
            self._rejected_current_window[instrument] = self._rejected_current_window.get(instrument, 0) + 1
            self._rejected_total[instrument] = self._rejected_total.get(instrument, 0) + 1
            return None

        self._kinds[instrument] = kind
        if open_interest is not None:
            self._open_interest[instrument] = open_interest
        if strike is not None:
            self._strike[instrument] = strike
        if option_type is not None:
            self._option_type[instrument] = option_type

        start, end = window_bounds(timestamp, INTERVAL_ONE_MINUTE)

        emitted: Optional[MinuteObservation] = None
        current = self._windows.get(instrument)

        if current is not None and start > current.window_start:
            emitted = self._close_and_emit(instrument, current)
            current = None

        if current is None:
            current = new_window(instrument, INTERVAL_ONE_MINUTE, start, end)
            self._rejected_current_window[instrument] = self._rejected_current_window.get(instrument, 0)

        self._windows[instrument] = add_tick(current, _Tick(timestamp=timestamp, price=price, volume=volume))
        return emitted

    def flush(self) -> Dict[str, Optional[MinuteObservation]]:
        """Force-close every instrument's currently-open window (e.g.
        at end of session). Returns {instrument: MinuteObservation or
        None} — None where the open window had zero valid ticks."""
        result: Dict[str, Optional[MinuteObservation]] = {}
        for instrument, window in list(self._windows.items()):
            result[instrument] = self._close_and_emit(instrument, window)
            del self._windows[instrument]
        return result

    def _close_and_emit(self, instrument: str, window: AggregationWindow) -> Optional[MinuteObservation]:
        closed, _obs = close_window(window, source="bujji.market_microstructure")
        rejected_this_window = self._rejected_current_window.pop(instrument, 0)

        if not closed.ticks:
            # Zero valid ticks this window -- never fabricate. Matches
            # close_window's own inherited discipline exactly (this is
            # its return contract, not a re-check of it).
            return None

        # Sort defensively -- add_tick accumulates in call order, which
        # is not guaranteed to equal timestamp order (a tick can arrive
        # out of the caller's own real-time sequence). Sorting here is
        # metadata derivation, not window/rollover logic, and is the one
        # place ordering correctness for THIS module's own output is
        # this module's responsibility.
        ticks = sorted(closed.ticks, key=lambda t: t.timestamp)
        prices = [t.price for t in ticks]
        tick_count = len(ticks)

        open_, high, low, close_ = prices[0], max(prices), min(prices), prices[-1]

        if tick_count >= 2:
            gaps = [
                (_parse_ts(ticks[i].timestamp) - _parse_ts(ticks[i - 1].timestamp)).total_seconds()
                for i in range(1, tick_count)
            ]
            max_silence = max(gaps)
            avg_interval = sum(gaps) / len(gaps)
            moves = [abs(prices[i] - prices[i - 1]) for i in range(1, tick_count)]
            max_move = max(moves)
        else:
            max_silence = None
            avg_interval = None
            max_move = 0.0

        kind = self._kinds.get(instrument, KIND_SPOT)
        is_option = kind == KIND_OPTION
        max_premium_move = max_move if is_option else None

        session_date = _parse_ts(ticks[0].timestamp).date().isoformat()

        quality_score = self._quality_score(tick_count, max_silence, rejected_this_window)

        return MinuteObservation(
            instrument=instrument, kind=kind, session_date=session_date,
            window_start=window.window_start, window_end=window.window_end,
            open=open_, high=high, low=low, close=close_,
            tick_count=tick_count,
            max_tick_silence_seconds=max_silence,
            avg_tick_interval_seconds=avg_interval,
            max_price_move=max_move,
            max_premium_move=max_premium_move,
            open_interest=self._open_interest.get(instrument),
            strike=self._strike.get(instrument),
            option_type=self._option_type.get(instrument),
            first_tick_timestamp=ticks[0].timestamp,
            last_tick_timestamp=ticks[-1].timestamp,
            rejected_tick_count=rejected_this_window,
            observation_quality_score=quality_score,
        )

    @staticmethod
    def _quality_score(tick_count: int, max_silence: Optional[float], rejected: int) -> float:
        """0-100. Density term rewards more ticks (capped at
        MIN_HEALTHY_TICKS -- more than that adds no further credit);
        silence and rejected-tick terms subtract. Clamped to [0, 100].
        Disclosed as a first, revisable default -- see module docstring."""
        density = 100.0 * min(1.0, tick_count / MIN_HEALTHY_TICKS)
        silence_penalty = 0.0
        if max_silence is not None and max_silence > SILENCE_TOLERANCE_SECONDS:
            silence_penalty = (max_silence - SILENCE_TOLERANCE_SECONDS) * SILENCE_PENALTY_PER_SECOND
        rejected_penalty = rejected * REJECTED_TICK_PENALTY
        score = density - silence_penalty - rejected_penalty
        return max(0.0, min(100.0, round(score, 2)))

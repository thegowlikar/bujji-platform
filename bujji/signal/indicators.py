"""Market-structure indicators used by the Signal Engine and Trade Manager.

Pure, side-effect-free calculations over completed candles. Both the entry
logic and the ongoing thesis-validation reuse these so the "would I enter now?"
question is answered with the exact same math as the original entry.
"""
from __future__ import annotations

from datetime import time
from typing import Iterable, Optional

from ..core.models import Candle, OpeningRange


class VwapTracker:
    """Incremental session VWAP — true volume-weighted when volume is real.

    FYERS provides genuine per-candle volume for NIFTY spot via the historical
    endpoint (the live quote's ``volume``/``atp`` are ``0`` for indices and are
    NOT used). When real volume is present this computes the correct
    volume-weighted VWAP: sum(typical * volume) / sum(volume).

    The equal-weight (typical-price average) approximation is a *fallback only*,
    disabled by default. When disabled and a feed supplies no volume, VWAP is
    reported as not ready so the Signal Engine will refuse to trade on an
    unreliable reference rather than risk capital on an approximation.
    """

    def __init__(self, allow_equal_weight_fallback: bool = False) -> None:
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._cum_tp = 0.0
        self._count = 0
        self._zero_vol_candles = 0
        self._allow_fallback = allow_equal_weight_fallback

    def update(self, candle: Candle) -> float:
        typical = (candle.high + candle.low + candle.close) / 3.0
        self._cum_tp += typical
        self._count += 1
        if candle.volume > 0:
            self._cum_pv += typical * candle.volume
            self._cum_vol += candle.volume
        else:
            self._zero_vol_candles += 1
        return self.value

    @property
    def value(self) -> float:
        if self._cum_vol > 0:
            return self._cum_pv / self._cum_vol  # True volume-weighted VWAP.
        if self._allow_fallback and self._count > 0:
            return self._cum_tp / self._count    # Explicit approximation.
        return 0.0

    @property
    def is_real(self) -> bool:
        """True when VWAP is backed by genuine traded volume."""
        return self._cum_vol > 0

    @property
    def ready(self) -> bool:
        """Whether VWAP may be relied upon for a trading decision."""
        if self.is_real:
            return True
        return self._allow_fallback and self._count > 0

    @property
    def zero_volume_candles(self) -> int:
        return self._zero_vol_candles

    @property
    def candle_count(self) -> int:
        """Number of completed candles folded into the VWAP so far."""
        return self._count

    @property
    def cumulative_volume(self) -> float:
        """Total real volume used to weight the VWAP."""
        return self._cum_vol

    @property
    def using_fallback(self) -> bool:
        """True when the reported value comes from the equal-weight fallback."""
        return not self.is_real and self._allow_fallback and self._count > 0

    @property
    def fallback_reason(self) -> Optional[str]:
        """Human-readable reason describing the VWAP quality, or None if real."""
        if self.is_real:
            return None
        if self._count == 0:
            return "no_candles_yet"
        if self._allow_fallback:
            return "no_real_volume__equal_weight_fallback_enabled"
        return "no_real_volume__trading_disabled_fallback_off"


class OpeningRangeBuilder:
    """Builds the ORB from candles within the [start, end) window."""

    def __init__(self, start: time, end: time) -> None:
        self._start = start
        self._end = end
        self._high: Optional[float] = None
        self._low: Optional[float] = None
        self._first: Optional[Candle] = None
        self._last: Optional[Candle] = None

    def _in_window(self, candle: Candle) -> bool:
        t = candle.timestamp.time()
        return self._start <= t < self._end

    def add(self, candle: Candle) -> None:
        if not self._in_window(candle):
            return
        self._high = candle.high if self._high is None else max(self._high, candle.high)
        self._low = candle.low if self._low is None else min(self._low, candle.low)
        self._first = self._first or candle
        self._last = candle

    def build_from(self, candles: Iterable[Candle]) -> Optional[OpeningRange]:
        for c in candles:
            self.add(c)
        return self.result

    @property
    def complete(self) -> bool:
        return self._high is not None and self._low is not None

    @property
    def result(self) -> Optional[OpeningRange]:
        if not self.complete or self._first is None or self._last is None:
            return None
        return OpeningRange(
            high=self._high,
            low=self._low,
            start=self._first.timestamp,
            end=self._last.timestamp,
        )

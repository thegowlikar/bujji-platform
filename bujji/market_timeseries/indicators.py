"""Technical analysis over stored candles -- Phase 15Q.

Pure functions over a `List[Candle]`. No IO, no broker, no execution,
no strategy-selection import. These COMPUTE values; they never emit a
signal, recommendation, or decision -- consistent with the project's
rule that analysis and decision stay separate layers.

TWO EPISTEMIC RULES, enforced by every function here:

1. INSUFFICIENT HISTORY IS NOT ZERO. An indicator needing `period`
   bars returns `None` when fewer exist. It never seeds from a partial
   window, never back-pads, never returns a "best effort" number. A
   14-period RSI computed from 6 bars is not a weak RSI -- it is not an
   RSI, and reporting one would be fabricated evidence.

2. GAPS ARE REAL. The store never forward-fills, so a series may have
   missing windows (a genuinely untraded 5 minutes in a far strike).
   These functions operate on the bars they are GIVEN, in order, and
   `series_is_contiguous()` lets a caller check whether the sample it
   pulled actually is continuous before trusting a time-sensitive
   indicator. A caller that ignores this is knowingly analysing a
   discontinuous series -- the store refuses to hide it from them.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from .models import Candle

_INTERVAL_SECONDS = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300}


def closes(candles: Sequence[Candle]) -> List[float]:
    return [c.close for c in candles]


def series_is_contiguous(candles: Sequence[Candle]) -> bool:
    """True if every consecutive pair is exactly one interval apart --
    i.e. the sample has no missing windows. Callers should check this
    before trusting any indicator whose meaning depends on even
    spacing."""
    if len(candles) < 2:
        return True
    from datetime import datetime
    step = _INTERVAL_SECONDS.get(candles[0].interval)
    if step is None:
        return False
    for prev, curr in zip(candles, candles[1:]):
        delta = (datetime.fromisoformat(curr.window_start)
                 - datetime.fromisoformat(prev.window_start)).total_seconds()
        if int(delta) != step:
            return False
    return True


def sma(candles: Sequence[Candle], period: int) -> Optional[float]:
    """Simple moving average of closes. None if fewer than `period`."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = closes(candles)
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(candles: Sequence[Candle], period: int) -> Optional[float]:
    """Exponential moving average, seeded with the SMA of the first
    `period` bars (the standard, deterministic seeding). None if fewer
    than `period` bars exist."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = closes(candles)
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    for price in values[period:]:
        current = (price - current) * multiplier + current
    return current


def rsi(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Wilder's RSI. Needs `period + 1` bars (period changes). None
    otherwise. Returns 100.0 when there are no losses in the window --
    that is the defined limit of the formula, not a fabricated value."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = closes(candles)
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for prev, curr in zip(values, values[1:]):
        change = curr - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_range(prev: Candle, curr: Candle) -> float:
    return max(curr.high - curr.low,
               abs(curr.high - prev.close),
               abs(curr.low - prev.close))


def atr(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Wilder's ATR. Needs `period + 1` bars. This is the indicator
    that most depends on REAL high/low -- it is only meaningful because
    the candles come from true tick aggregation rather than sampled
    snapshots (see the phase report's data-source rationale)."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        return None
    trs = [true_range(p, c) for p, c in zip(candles, candles[1:])]
    current = sum(trs[:period]) / period
    for tr in trs[period:]:
        current = (current * (period - 1) + tr) / period
    return current


def bollinger(candles: Sequence[Candle], period: int = 20, num_std: float = 2.0):
    """Returns (lower, middle, upper) or None if insufficient history.
    Uses the population standard deviation over the last `period`
    closes."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = closes(candles)
    if len(values) < period:
        return None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((v - middle) ** 2 for v in window) / period
    sd = variance ** 0.5
    return (middle - num_std * sd, middle, middle + num_std * sd)


def realised_volatility(candles: Sequence[Candle], period: int = 20) -> Optional[float]:
    """Standard deviation of simple close-to-close returns over the
    last `period` bars. Needs `period + 1` bars. Not annualised -- the
    caller decides the scaling factor, since that depends on the
    interval and on trading-day conventions this module deliberately
    does not assume."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = closes(candles)
    if len(values) < period + 1:
        return None
    rets = []
    for prev, curr in zip(values[-(period + 1):], values[-period:]):
        if prev == 0:
            return None  # an undefined return -- never silently treated as 0.0.
        rets.append((curr - prev) / prev)
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / len(rets)
    return variance ** 0.5


def adx(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Average Directional Index (Wilder's original formulation) --
    added Phase 20.1B. The classic, textbook trend-strength measure,
    used here purely as an INDEPENDENT cross-check against
    `bujji.intelligence.regime_brain.RegimeBrain`'s own Kaufman
    Efficiency Ratio -- two different, well-established measures of
    "is this market actually trending" computed from the same candles,
    so a validation report can show whether they agree rather than
    trusting one lens alone. Not previously implemented anywhere in
    this codebase (confirmed by repository-wide search before writing
    this).

    Needs `2 * period` bars (Wilder's smoothing needs `period` bars to
    seed the initial average, then another `period` to produce a
    stable smoothed reading) -- returns None below that, never a noisy
    early value presented as if it were reliable.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(candles) < 2 * period + 1:
        return None

    ordered = list(candles)
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    tr: List[float] = []
    for i in range(1, len(ordered)):
        prev, curr = ordered[i - 1], ordered[i]
        up_move = curr.high - prev.high
        down_move = prev.low - curr.low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr.append(true_range(prev, curr))

    def _wilder_smooth(values: List[float]) -> List[float]:
        smoothed = [sum(values[:period])]
        for v in values[period:]:
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + v)
        return smoothed

    smoothed_tr = _wilder_smooth(tr)
    smoothed_plus_dm = _wilder_smooth(plus_dm)
    smoothed_minus_dm = _wilder_smooth(minus_dm)

    dx_values: List[float] = []
    for str_, spdm, smdm in zip(smoothed_tr, smoothed_plus_dm, smoothed_minus_dm):
        if str_ <= 0:
            continue  # no true range -- undefined DI, never fabricated as 0.
        plus_di = 100.0 * spdm / str_
        minus_di = 100.0 * smdm / str_
        di_sum = plus_di + minus_di
        if di_sum <= 0:
            dx_values.append(0.0)
        else:
            dx_values.append(100.0 * abs(plus_di - minus_di) / di_sum)

    if len(dx_values) < period:
        return None
    return sum(dx_values[-period:]) / period

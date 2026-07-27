"""Regime Brain — Market Intelligence Core.

Answers: is today trending, ranging, volatile, compressed, or
transitioning between regimes? Pure spot-price statistics, computed from
real candle data BUJJI already has live access to (the same NIFTY spot
candles the Signal Engine already consumes) -- no new data source, no
external dependency, no unverified assumption.

DESIGN PRINCIPLE (matches every other module in this codebase): explainable
over clever. This uses two well-established, simple, auditable measures --
not a black box, not ML (there isn't enough real history to train or
honestly validate one yet, per the MIC research report). Every reading
carries its raw inputs so a human can independently recompute and check it
against the same candles.

METHOD
------
1. Kaufman's Efficiency Ratio (ER) = |net directional move| / (sum of
   absolute candle-to-candle moves). Ranges 0 (pure back-and-forth noise)
   to 1 (a straight line in one direction). This is THE classic, simple,
   well-understood measure for "is price actually going somewhere, or just
   churning" -- deliberately not reinvented here.

2. Realized volatility = stdev of candle-to-candle log returns over the
   window. Used two ways:
     a. Absolute level -> flags a VOLATILE session outright, regardless of
        efficiency ratio (a choppy, wide-swinging day is volatile even if
        it nets out to a moderate ER).
     b. Second-half-of-window vs first-half-of-window ratio -> detects
        COMPRESSION (range visibly narrowing within the session) or
        TRANSITIONING (range visibly expanding -- the regime may be
        shifting under us).

Classification priority (checked in this order -- documented so the
"why this label and not another" question always has one answer):
    1. High realized vol            -> VOLATILE
    2. Strong intra-session compression -> COMPRESSED
    3. Strong intra-session expansion   -> TRANSITIONING
    4. High efficiency ratio        -> TRENDING
    5. Low efficiency ratio         -> RANGING
    6. Otherwise (ambiguous middle) -> TRANSITIONING

CALIBRATION NOTE: the four threshold constants below are a documented
first pass, not a proven-optimal set -- there is not yet enough real
multi-week NIFTY history to calibrate them statistically (the same
data-availability constraint flagged throughout this session's research).
Treat them as a starting point to be revisited once real history
accumulates, not as a validated conclusion.
"""
from __future__ import annotations

import math
from typing import Optional

from ..core.clock import now_ist
from ..core.models import Candle
from .models import DataQuality, RegimeReading, RegimeType

MIN_CANDLES = 6  # 30 minutes at 5-min candles -- below this, refuse to guess.

# -- Calibration constants (documented first pass; see module docstring) --
ER_TRENDING_THRESHOLD = 0.60
ER_RANGING_THRESHOLD = 0.30
VOL_HIGH_THRESHOLD = 0.0025    # ~0.25% stdev of 5-min log returns.
COMPRESSION_RATIO_THRESHOLD = 0.60   # second-half vol / first-half vol <= this -> compressing
EXPANSION_RATIO_THRESHOLD = 1.60     # second-half vol / first-half vol >= this -> expanding


class RegimeBrain:
    """Stateless: call `analyze(candles)` with the session's candles so far.
    Never mutates anything, never talks to a broker, never decides whether
    to trade -- purely an observation, per the Market Intelligence Core's
    first principle."""

    def analyze(self, candles: list[Candle]) -> RegimeReading:
        candles = sorted(candles, key=lambda c: c.timestamp)
        n = len(candles)

        if n < MIN_CANDLES:
            return RegimeReading(
                regime=RegimeType.UNKNOWN,
                confidence=0.0,
                data_quality=DataQuality.INSUFFICIENT,
                reason=f"insufficient_data: {n} candle(s), need >= {MIN_CANDLES}",
                candles_used=n,
                as_of=now_ist(),
            )

        closes = [c.close for c in candles]
        er, path_length, net_move = self._efficiency_ratio(closes)
        returns = self._log_returns(closes)
        realized_vol = self._stdev(returns) if len(returns) >= 2 else 0.0
        compression_ratio = self._compression_ratio(returns)

        evidence = {
            "efficiency_ratio": round(er, 4),
            "net_move": round(net_move, 2),
            "path_length": round(path_length, 2),
            "realized_vol": round(realized_vol, 6),
            "compression_ratio": (round(compression_ratio, 3)
                                  if compression_ratio is not None else None),
        }

        regime, reason, confidence = self._classify(er, realized_vol, compression_ratio)

        return RegimeReading(
            regime=regime,
            confidence=confidence,
            data_quality=DataQuality.SUFFICIENT,
            evidence=evidence,
            reason=reason,
            candles_used=n,
            as_of=now_ist(),
        )

    # ------------------------------------------------------------------ #
    # Metrics (each independently testable, each documented above)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _efficiency_ratio(closes: list[float]) -> tuple[float, float, float]:
        net_move = closes[-1] - closes[0]
        path_length = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        er = abs(net_move) / path_length if path_length > 0 else 0.0
        return er, path_length, net_move

    @staticmethod
    def _log_returns(closes: list[float]) -> list[float]:
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0 and closes[i] > 0:
                returns.append(math.log(closes[i] / closes[i - 1]))
        return returns

    @staticmethod
    def _stdev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(var)

    def _compression_ratio(self, returns: list[float]) -> Optional[float]:
        """second-half realized vol / first-half realized vol. None if
        there isn't enough data to split meaningfully (needs >= 4 returns,
        i.e. >= 5 candles, so both halves have >= 2 data points each)."""
        if len(returns) < 4:
            return None
        mid = len(returns) // 2
        first_half_vol = self._stdev(returns[:mid])
        second_half_vol = self._stdev(returns[mid:])
        if first_half_vol <= 0:
            return None
        return second_half_vol / first_half_vol

    # ------------------------------------------------------------------ #
    # Classification (priority order documented in the module docstring)
    # ------------------------------------------------------------------ #
    def _classify(
        self, er: float, realized_vol: float, compression_ratio: Optional[float],
    ) -> tuple[RegimeType, str, float]:
        if realized_vol >= VOL_HIGH_THRESHOLD:
            confidence = self._scaled_confidence(realized_vol, VOL_HIGH_THRESHOLD, VOL_HIGH_THRESHOLD * 3)
            return (RegimeType.VOLATILE,
                    f"realized_vol {realized_vol:.4f} >= threshold {VOL_HIGH_THRESHOLD:.4f}",
                    confidence)

        if compression_ratio is not None and compression_ratio <= COMPRESSION_RATIO_THRESHOLD:
            confidence = self._scaled_confidence(
                COMPRESSION_RATIO_THRESHOLD - compression_ratio, 0.0, COMPRESSION_RATIO_THRESHOLD)
            return (RegimeType.COMPRESSED,
                    f"compression_ratio {compression_ratio:.3f} <= threshold {COMPRESSION_RATIO_THRESHOLD}",
                    confidence)

        if compression_ratio is not None and compression_ratio >= EXPANSION_RATIO_THRESHOLD:
            confidence = self._scaled_confidence(
                compression_ratio - EXPANSION_RATIO_THRESHOLD, 0.0, EXPANSION_RATIO_THRESHOLD)
            return (RegimeType.TRANSITIONING,
                    f"compression_ratio {compression_ratio:.3f} >= threshold {EXPANSION_RATIO_THRESHOLD}"
                    " (volatility expanding intra-session)",
                    confidence)

        if er >= ER_TRENDING_THRESHOLD:
            confidence = self._scaled_confidence(er, ER_TRENDING_THRESHOLD, 1.0)
            return (RegimeType.TRENDING,
                    f"efficiency_ratio {er:.3f} >= threshold {ER_TRENDING_THRESHOLD}",
                    confidence)

        if er <= ER_RANGING_THRESHOLD:
            confidence = self._scaled_confidence(ER_RANGING_THRESHOLD - er, 0.0, ER_RANGING_THRESHOLD)
            return (RegimeType.RANGING,
                    f"efficiency_ratio {er:.3f} <= threshold {ER_RANGING_THRESHOLD}",
                    confidence)

        # Ambiguous middle ground: neither clearly trending nor ranging,
        # and volatility/compression didn't trigger either -- genuinely
        # uncertain, not a forced guess.
        return (RegimeType.TRANSITIONING,
                f"efficiency_ratio {er:.3f} between thresholds "
                f"({ER_RANGING_THRESHOLD}-{ER_TRENDING_THRESHOLD}); no clear regime",
                0.35)

    @staticmethod
    def _scaled_confidence(distance_past_threshold: float, low: float, high: float) -> float:
        """Confidence rises from 0.5 at the threshold boundary itself to
        1.0 at (or past) `high` -- a reading that JUST crosses a threshold
        is reported as low-confidence, not the same 100% as a decisive one.
        """
        if high <= low:
            return 0.5
        span = high - low
        scaled = min(1.0, max(0.0, distance_past_threshold / span))
        return round(0.5 + 0.5 * scaled, 4)

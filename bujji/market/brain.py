"""Market Brain — the single interpreter of market state.

The Brain answers *what is the market doing?* in direction-neutral terms:
where price sits relative to VWAP and the ORB, whether the last candle made
progress, and who currently controls the tape. It computes raw structural
facts once, so both the Signal Engine (entry) and the Trade Manager (ongoing
validation) reason from the *exact same* interpretation.

Crucially, the Brain makes no trading decision and applies no directional bias.
It reports; the engines decide. This is what keeps the entry test and the
"would I still enter now?" test mathematically identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..core.models import Candle, OpeningRange


@dataclass(frozen=True)
class MarketState:
    """Direction-neutral structural reading of one completed candle."""

    timestamp: datetime
    spot: float
    vwap: float

    # VWAP relationship.
    above_vwap: bool          # close > vwap (strict).
    below_vwap: bool          # close < vwap (strict).
    at_or_above_vwap: bool    # close >= vwap.
    at_or_below_vwap: bool    # close <= vwap.

    # Opening-range relationship.
    above_orb_high: bool
    below_orb_low: bool

    # Candle quality.
    body_ratio: float
    strong_body: bool         # body_ratio >= configured threshold.
    candle_bullish: bool
    candle_bearish: bool

    # Progress vs the previous candle (True when no previous candle exists).
    higher_high: bool
    higher_close: bool
    lower_low: bool
    lower_close: bool

    # Control: a strong-bodied candle in one direction.
    aggressive_bullish: bool
    aggressive_bearish: bool

    def summary(self) -> str:
        vwap_rel = "above" if self.above_vwap else "below" if self.below_vwap else "at"
        control = (
            "buyers" if self.aggressive_bullish
            else "sellers" if self.aggressive_bearish
            else "balanced"
        )
        return (f"spot {self.spot} {vwap_rel} VWAP {self.vwap:.2f}; "
                f"body {self.body_ratio:.0%}; control={control}")


class MarketBrain:
    """Turns raw candles into a :class:`MarketState`."""

    def __init__(self, body_threshold: float) -> None:
        self._body_threshold = body_threshold

    def interpret(
        self,
        candle: Candle,
        vwap: float,
        orb: OpeningRange,
        previous: Optional[Candle] = None,
    ) -> MarketState:
        strong_body = candle.body_ratio >= self._body_threshold
        if previous is None:
            hh = hc = ll = lc = True  # First candle never counts as collapse.
        else:
            hh = candle.high >= previous.high
            hc = candle.close >= previous.close
            ll = candle.low <= previous.low
            lc = candle.close <= previous.close

        return MarketState(
            timestamp=candle.timestamp,
            spot=candle.close,
            vwap=vwap,
            above_vwap=candle.close > vwap,
            below_vwap=candle.close < vwap,
            at_or_above_vwap=candle.close >= vwap,
            at_or_below_vwap=candle.close <= vwap,
            above_orb_high=candle.close > orb.high,
            below_orb_low=candle.close < orb.low,
            body_ratio=candle.body_ratio,
            strong_body=strong_body,
            candle_bullish=candle.is_bullish,
            candle_bearish=candle.is_bearish,
            higher_high=hh,
            higher_close=hc,
            lower_low=ll,
            lower_close=lc,
            aggressive_bullish=strong_body and candle.is_bullish,
            aggressive_bearish=strong_body and candle.is_bearish,
        )

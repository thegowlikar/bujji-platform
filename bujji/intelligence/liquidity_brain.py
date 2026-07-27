"""Liquidity Brain — Market Intelligence Core.

Answers: is the combined straddle's top-of-book bid/ask spread tight
enough to enter or exit right now without meaningful slippage?

DATA REALITY (verified live before writing a line of this brain's logic,
2026-07-20): the real FYERS `quotes` response for an option symbol
includes per-symbol `bid`, `ask`, and `spread` fields --

    NSE:NIFTY2672124250CE -> bid=82.4  ask=82.6  spread=0.2
    NSE:NIFTY2672124250PE -> bid=68.0  ask=68.05 spread=0.05

`spread == ask - bid` held exactly on both legs, confirming these are
genuine top-of-book quotes, not placeholders. This brain consumes ONLY
those three fields.

NOT used, deliberately: the quote's `volume` field. In the same live
capture it returned 400M+ for a single option symbol -- not a plausible
per-symbol traded quantity, and not independently corroborated against
another source. Rather than guess at what it actually represents (total
exchange-wide volume? a different unit?), this brain does not consume
it. Full multi-level market depth (beyond top-of-book bid/ask) was not
observed in the raw quote response either, and is NOT assumed available
-- if BUJJI needs deeper book data later, that requires its own live
verification pass, same as this one.

METHOD
------
Combined spread = (ce_ask + pe_ask) - (ce_bid + pe_bid) -- the real cost,
in points, of buying back both legs at the current top-of-book ask (the
relevant side when exiting a short straddle) versus the mid. Reported as
both an absolute (points) and a percentage of the combined mid price, so
it's comparable across very different premium levels.

CALIBRATION NOTE (same discipline as every other brain in the MIC): the
TIGHT/WIDE thresholds below are a documented first pass, not a
statistically calibrated conclusion -- there is not yet enough real
history of spreads across market conditions (opening volatility, low
liquidity strikes, expiry day, etc.) to calibrate them properly.

INTEGRATION NOTE: no current BUJJI broker method exposes bid/ask -- only
`get_ltp()` (last traded price only, from the same quote response this
brain's live verification pulled bid/ask from). Wiring real bid/ask into
production (extending the broker interface) is a separate, not-yet-done
integration step; this brain is data-source agnostic and takes bid/ask
as plain arguments, exactly like the Volatility/Premium/Greeks Brains
take premiums and IV as plain arguments rather than calling a broker
themselves.
"""
from __future__ import annotations

from typing import Optional

from ..core.clock import now_ist
from .models import DataQuality, LiquidityReading, SpreadTightness

SPREAD_TIGHT_THRESHOLD_PCT = 0.50   # Combined spread <= this % of mid -> TIGHT.
SPREAD_WIDE_THRESHOLD_PCT = 2.00    # Combined spread >= this % of mid -> WIDE.


class LiquidityBrain:
    """Stateless: call `analyze(...)` with real top-of-book bid/ask for
    both legs. Never mutates anything, never talks to a broker, never
    decides whether to trade."""

    def analyze(
        self,
        ce_bid: float,
        ce_ask: float,
        pe_bid: float,
        pe_ask: float,
    ) -> LiquidityReading:
        as_of = now_ist()

        for label, value in (("ce_bid", ce_bid), ("ce_ask", ce_ask),
                              ("pe_bid", pe_bid), ("pe_ask", pe_ask)):
            if value is None or value <= 0:
                return self._unknown(as_of, ce_bid, ce_ask, pe_bid, pe_ask,
                                     f"invalid_quote: {label} is missing or non-positive")

        if ce_ask < ce_bid or pe_ask < pe_bid:
            return self._unknown(as_of, ce_bid, ce_ask, pe_bid, pe_ask,
                                 "crossed_market: ask below bid on at least one leg -- stale/bad quote")

        ce_mid = (ce_bid + ce_ask) / 2.0
        pe_mid = (pe_bid + pe_ask) / 2.0
        ce_spread_pct = (ce_ask - ce_bid) / ce_mid * 100.0
        pe_spread_pct = (pe_ask - pe_bid) / pe_mid * 100.0

        combined_bid = ce_bid + pe_bid
        combined_ask = ce_ask + pe_ask
        combined_spread = combined_ask - combined_bid
        combined_mid = (combined_bid + combined_ask) / 2.0
        combined_spread_pct = combined_spread / combined_mid * 100.0

        tightness, reason, confidence = self._classify_tightness(combined_spread_pct)

        evidence = {
            "ce_mid": round(ce_mid, 2), "pe_mid": round(pe_mid, 2),
            "combined_bid": round(combined_bid, 2), "combined_ask": round(combined_ask, 2),
            "combined_mid": round(combined_mid, 2),
        }

        return LiquidityReading(
            ce_bid=ce_bid, ce_ask=ce_ask, pe_bid=pe_bid, pe_ask=pe_ask,
            ce_spread_pct=round(ce_spread_pct, 4), pe_spread_pct=round(pe_spread_pct, 4),
            combined_spread=round(combined_spread, 2), combined_spread_pct=round(combined_spread_pct, 4),
            tightness=tightness, confidence=confidence, data_quality=DataQuality.SUFFICIENT,
            evidence=evidence, reason=reason, as_of=as_of,
        )

    @staticmethod
    def _classify_tightness(spread_pct: float) -> tuple[SpreadTightness, str, float]:
        if spread_pct <= SPREAD_TIGHT_THRESHOLD_PCT:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (SPREAD_TIGHT_THRESHOLD_PCT - spread_pct) / SPREAD_TIGHT_THRESHOLD_PCT))
            return (SpreadTightness.TIGHT,
                    f"combined spread {spread_pct:.3f}% <= threshold {SPREAD_TIGHT_THRESHOLD_PCT}%",
                    round(confidence, 4))
        if spread_pct >= SPREAD_WIDE_THRESHOLD_PCT:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (spread_pct - SPREAD_WIDE_THRESHOLD_PCT) / SPREAD_WIDE_THRESHOLD_PCT))
            return (SpreadTightness.WIDE,
                    f"combined spread {spread_pct:.3f}% >= threshold {SPREAD_WIDE_THRESHOLD_PCT}%",
                    round(confidence, 4))
        return (SpreadTightness.NORMAL,
                f"combined spread {spread_pct:.3f}% between {SPREAD_TIGHT_THRESHOLD_PCT}% and {SPREAD_WIDE_THRESHOLD_PCT}%",
                0.5)

    @staticmethod
    def _unknown(as_of, ce_bid, ce_ask, pe_bid, pe_ask, reason: str) -> LiquidityReading:
        return LiquidityReading(
            ce_bid=ce_bid, ce_ask=ce_ask, pe_bid=pe_bid, pe_ask=pe_ask,
            ce_spread_pct=None, pe_spread_pct=None,
            combined_spread=None, combined_spread_pct=None,
            tightness=SpreadTightness.UNKNOWN, confidence=0.0,
            data_quality=DataQuality.INSUFFICIENT, reason=reason, as_of=as_of,
        )

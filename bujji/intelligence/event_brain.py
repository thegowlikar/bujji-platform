"""Event Brain — Market Intelligence Core.

Answers: is today carrying extra event risk that a plain price/vol/OI
view wouldn't surface -- either because expiry itself is imminent (pin
risk, accelerating gamma) or because the market's own priced-in
volatility expectation (India VIX) is elevated?

SCOPE, STATED UP FRONT: this is deliberately narrower than the MIC's
original "Event Brain" concept. The calendar half of that design (FOMC,
RBI policy, Union Budget, and similar scheduled macro events) needs an
external economic-calendar data source. BUJJI has no such integration,
and none of the FYERS endpoints already in use elsewhere in this
codebase (quotes, historical, optionchain) provide one. Rather than
fabricate event dates from general knowledge -- which would silently go
stale and could simply be wrong for any given contract cycle -- that
half is NOT built here and remains an explicit open item, same as OI
(standalone brain) in the MIC status table.

DATA REALITY (verified live before writing this brain's logic,
2026-07-20): `NSE:INDIAVIX-INDEX` is a real, live-quotable symbol via the
same `quotes` endpoint the Liquidity Brain's check used --
`lp=13.02, prev_close_price=13.15, chp=-0.99` at capture time, a
plausible historical India VIX level. Used here for its `lp` (current
level) and `chp` (real day-over-day % change, computed by FYERS from
`lp` vs `prev_close_price` -- not derived or guessed by this brain).

METHOD
------
1. Expiry proximity -- pure date arithmetic between `today` and the
   straddle's own real `expiry_date`. Needs no external data at all.
2. VIX regime -- classifies the real current VIX level. CALIBRATION
   NOTE (same discipline as every other brain in the MIC): the LOW/
   MODERATE/ELEVATED bands below are a documented first pass drawn from
   India VIX's well-known historical behavior (single digits to
   low-teens = calm, high teens to 20s = building concern, above that =
   acute stress), not a statistically calibrated conclusion from BUJJI's
   own accumulated history -- there isn't one yet.

Deliberately NOT combined into one synthesized "risk score" -- expiry
proximity and VIX regime are reported as two independent dimensions.
Inventing a formula to merge them would be exactly the kind of
unvalidated synthesis this codebase's discipline avoids.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from ..core.clock import now_ist
from .models import DataQuality, EventReading, ExpiryProximity, VixRegime

VIX_LOW_THRESHOLD = 13.0       # VIX <= this -> LOW.
VIX_ELEVATED_THRESHOLD = 20.0  # VIX >= this -> ELEVATED.


class EventBrain:
    """Stateless: call `analyze(...)` with the straddle's real expiry
    date, today's date, and a real current VIX level (+ optional prior
    close for the change %). Never mutates anything, never talks to a
    broker, never decides whether to trade."""

    def analyze(
        self,
        expiry_date: date,
        today: date,
        vix_level: Optional[float],
        vix_prev_close: Optional[float] = None,
    ) -> EventReading:
        as_of = now_ist()

        days_to_expiry = None
        expiry_proximity = ExpiryProximity.UNKNOWN
        expiry_reason = "invalid_dates: expiry_date must be on or after today"
        if expiry_date is not None and today is not None and expiry_date >= today:
            days_to_expiry = (expiry_date - today).days
            if days_to_expiry == 0:
                expiry_proximity, expiry_reason = ExpiryProximity.EXPIRY_DAY, "days_to_expiry == 0"
            elif days_to_expiry == 1:
                expiry_proximity, expiry_reason = ExpiryProximity.EXPIRY_EVE, "days_to_expiry == 1"
            else:
                expiry_proximity, expiry_reason = ExpiryProximity.NORMAL, f"days_to_expiry == {days_to_expiry}"

        vix_change_pct = None
        vix_regime = VixRegime.UNKNOWN
        vix_reason = "missing_vix: vix_level not provided or non-positive"
        if vix_level is not None and vix_level > 0:
            vix_regime, vix_reason = self._classify_vix(vix_level)
            if vix_prev_close is not None and vix_prev_close > 0:
                vix_change_pct = (vix_level - vix_prev_close) / vix_prev_close * 100.0

        if expiry_proximity is ExpiryProximity.UNKNOWN and vix_regime is VixRegime.UNKNOWN:
            return EventReading(
                days_to_expiry=None, expiry_proximity=ExpiryProximity.UNKNOWN,
                vix_level=vix_level, vix_change_pct=None, vix_regime=VixRegime.UNKNOWN,
                confidence=0.0, data_quality=DataQuality.INSUFFICIENT,
                reason=f"{expiry_reason}; {vix_reason}", as_of=as_of,
            )

        # At least one dimension is valid (the all-UNKNOWN case already
        # returned above) -- report what's known, same "partial but
        # honest" pattern as the Structure Brain's one-sided wall data.
        # Confidence reflects how many of the two independent dimensions
        # actually resolved.
        both_known = expiry_proximity is not ExpiryProximity.UNKNOWN and vix_regime is not VixRegime.UNKNOWN
        data_quality = DataQuality.SUFFICIENT
        confidence = 1.0 if both_known else 0.5

        evidence = {"expiry_date": expiry_date.isoformat() if expiry_date else None,
                    "today": today.isoformat() if today else None}
        if vix_prev_close is not None:
            evidence["vix_prev_close"] = vix_prev_close

        return EventReading(
            days_to_expiry=days_to_expiry, expiry_proximity=expiry_proximity,
            vix_level=vix_level, vix_change_pct=round(vix_change_pct, 3) if vix_change_pct is not None else None,
            vix_regime=vix_regime, confidence=confidence, data_quality=data_quality,
            evidence=evidence, reason=f"{expiry_reason}; {vix_reason}", as_of=as_of,
        )

    @staticmethod
    def _classify_vix(vix_level: float) -> tuple[VixRegime, str]:
        if vix_level <= VIX_LOW_THRESHOLD:
            return VixRegime.LOW, f"vix {vix_level} <= threshold {VIX_LOW_THRESHOLD}"
        if vix_level >= VIX_ELEVATED_THRESHOLD:
            return VixRegime.ELEVATED, f"vix {vix_level} >= threshold {VIX_ELEVATED_THRESHOLD}"
        return VixRegime.MODERATE, f"vix {vix_level} between {VIX_LOW_THRESHOLD} and {VIX_ELEVATED_THRESHOLD}"

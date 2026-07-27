"""Premium Brain — Market Intelligence Core.

Answers: is the combined straddle premium decaying the way a seller
wants -- driven by time (theta) -- or is something else (spot movement,
IV expansion/contraction) fighting or accelerating that decay?

METHOD
------
Hold spot and IV FIXED at their values at entry, and reprice the
straddle at the CURRENT point in time using Black-Scholes -- this is a
pure counterfactual: "what would the combined premium be right now if
literally nothing had changed except time passing?" Compare that
theoretical, time-decay-only premium against the REAL current combined
premium (which reflects everything that actually happened -- real spot
movement, real IV changes, real liquidity/quote noise).

The gap between the two isolates what is NOT explained by time decay.
If the real premium has fallen further than the theta-only baseline,
something outside of pure time decay (e.g. IV compression) is helping
the seller. If the real premium has fallen less, or risen, despite time
passing, something is fighting the seller's position (spot moving away
from the straddle's center, or IV expanding) -- direct, real-time
information a Trade Manager or a human overseeing the position needs to
see, that a plain P&L number does not surface on its own.

This reuses the exact same Black-Scholes pricer (`_bs_price`) already
validated in the Volatility Brain's solver round-trip test -- no new
untested pricing math introduced here.

DATA REALITY: requires a known IV at entry (from the Volatility Brain,
or any other reliable IV source). Without it there is no legitimate
theta-only baseline to compare against -- this brain refuses to guess
one, and reports UNKNOWN/INSUFFICIENT instead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..core.clock import now_ist
from ..core.enums import OptionType
from .models import DataQuality, PremiumBehavior, PremiumReading
from .volatility_brain import _bs_price

RATIO_FASTER_THRESHOLD = 0.85   # actual/theoretical <= this -> decaying faster than theta alone.
RATIO_RISING_THRESHOLD = 1.15   # actual/theoretical >= this -> rising against theta.


class PremiumBrain:
    """Stateless: call `analyze(...)` with real entry/current combined
    premiums, real spot at entry, the strike, entry IV (from the
    Volatility Brain or another reliable source), and the entry/current/
    expiry timestamps. Never mutates anything, never talks to a broker,
    never decides whether to trade."""

    def analyze(
        self,
        entry_combined_premium: float,
        current_combined_premium: float,
        spot_at_entry: float,
        strike: float,
        entry_iv: Optional[float],
        entry_time: datetime,
        now: datetime,
        expiry_time: datetime,
        risk_free_rate: float = 0.065,
    ) -> PremiumReading:
        as_of = now_ist()

        if entry_iv is None:
            return PremiumReading(
                entry_combined_premium=entry_combined_premium,
                current_combined_premium=current_combined_premium,
                theoretical_time_decay_only_premium=None,
                behavior=PremiumBehavior.UNKNOWN,
                behavior_ratio=None,
                premium_captured_pct=None,
                time_elapsed_pct=None,
                confidence=0.0,
                data_quality=DataQuality.INSUFFICIENT,
                reason="no_entry_iv: cannot build a theta-only baseline without a known entry IV",
                as_of=as_of,
            )

        total_t = (expiry_time - entry_time).total_seconds()
        elapsed_t = (now - entry_time).total_seconds()
        remaining_t = (expiry_time - now).total_seconds()

        if total_t <= 0 or elapsed_t < 0 or entry_combined_premium <= 0 or current_combined_premium <= 0:
            return PremiumReading(
                entry_combined_premium=entry_combined_premium,
                current_combined_premium=current_combined_premium,
                theoretical_time_decay_only_premium=None,
                behavior=PremiumBehavior.UNKNOWN,
                behavior_ratio=None,
                premium_captured_pct=None,
                time_elapsed_pct=None,
                confidence=0.0,
                data_quality=DataQuality.INSUFFICIENT,
                reason="invalid_inputs: non-positive premium or non-positive/negative time window",
                as_of=as_of,
            )

        remaining_t_years = max(remaining_t, 0.0) / (365 * 24 * 3600)

        theoretical_ce = _bs_price(spot_at_entry, strike, remaining_t_years, risk_free_rate,
                                   entry_iv, OptionType.CE)
        theoretical_pe = _bs_price(spot_at_entry, strike, remaining_t_years, risk_free_rate,
                                   entry_iv, OptionType.PE)
        theoretical_combined = theoretical_ce + theoretical_pe

        time_elapsed_pct = min(100.0, max(0.0, elapsed_t / total_t * 100.0))
        premium_captured_pct = (entry_combined_premium - current_combined_premium) / entry_combined_premium * 100.0

        evidence = {
            "spot_at_entry": spot_at_entry,
            "strike": strike,
            "entry_iv": round(entry_iv, 4),
            "theoretical_ce": round(theoretical_ce, 2),
            "theoretical_pe": round(theoretical_pe, 2),
            "remaining_t_years": round(remaining_t_years, 5),
        }

        if theoretical_combined <= 0:
            return PremiumReading(
                entry_combined_premium=entry_combined_premium,
                current_combined_premium=current_combined_premium,
                theoretical_time_decay_only_premium=round(theoretical_combined, 2),
                behavior=PremiumBehavior.UNKNOWN,
                behavior_ratio=None,
                premium_captured_pct=round(premium_captured_pct, 2),
                time_elapsed_pct=round(time_elapsed_pct, 2),
                confidence=0.0,
                data_quality=DataQuality.INSUFFICIENT,
                evidence=evidence,
                reason="theoretical_baseline_nonpositive: at/past expiry, no meaningful theta-only baseline",
                as_of=as_of,
            )

        ratio = current_combined_premium / theoretical_combined
        behavior, reason, confidence = self._classify_behavior(ratio)
        evidence["behavior_ratio"] = round(ratio, 4)

        return PremiumReading(
            entry_combined_premium=entry_combined_premium,
            current_combined_premium=current_combined_premium,
            theoretical_time_decay_only_premium=round(theoretical_combined, 2),
            behavior=behavior,
            behavior_ratio=round(ratio, 4),
            premium_captured_pct=round(premium_captured_pct, 2),
            time_elapsed_pct=round(time_elapsed_pct, 2),
            confidence=confidence,
            data_quality=DataQuality.SUFFICIENT,
            evidence=evidence,
            reason=reason,
            as_of=as_of,
        )

    @staticmethod
    def _classify_behavior(ratio: float) -> tuple[PremiumBehavior, str, float]:
        if ratio <= RATIO_FASTER_THRESHOLD:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (RATIO_FASTER_THRESHOLD - ratio) / RATIO_FASTER_THRESHOLD))
            return (PremiumBehavior.DECAYING_FASTER_THAN_THETA,
                    f"actual/theoretical ratio {ratio:.3f} <= threshold {RATIO_FASTER_THRESHOLD}",
                    round(confidence, 4))
        if ratio >= RATIO_RISING_THRESHOLD:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (ratio - RATIO_RISING_THRESHOLD) / RATIO_RISING_THRESHOLD))
            return (PremiumBehavior.RISING_AGAINST_THETA,
                    f"actual/theoretical ratio {ratio:.3f} >= threshold {RATIO_RISING_THRESHOLD}",
                    round(confidence, 4))
        return (PremiumBehavior.DECAYING_AS_EXPECTED,
                f"actual/theoretical ratio {ratio:.3f} between {RATIO_FASTER_THRESHOLD} and {RATIO_RISING_THRESHOLD}",
                0.5)

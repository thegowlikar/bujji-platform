"""Structure Brain — Market Intelligence Core.

Answers: is spot sitting right at a real open-interest wall -- a strike
where heavy call or put writing tends to act as resistance or support --
or comfortably between walls?

DATA REALITY (verified live before writing a line of this brain's logic,
2026-07-20): the real FYERS `optionchain` endpoint
(`client.optionchain({"symbol": ..., "strikecount": N})`) -- a DIFFERENT
endpoint from the plain `quotes` call the other brains' live checks used
-- returns per-strike `oi`, `prev_oi`, and `oich` (OI change) for both CE
and PE legs. Confirmed live against real NIFTY strikes:

    24100 PE: oi=17,299,295  prev_oi=10,241,300  oich=7,057,995
    24100 CE: oi=3,940,820   prev_oi=2,905,820    oich=1,035,000

`oich == oi - prev_oi` held exactly on every strike checked -- genuine,
internally consistent open-interest data, not a placeholder.

METHOD
------
Resistance = the strike ABOVE spot with the highest CE open interest
(heavy call writing above spot is the classic "call wall" -- writers are
betting price won't clear that level, and their own hedging flow tends
to reinforce that). Support = the strike BELOW spot with the highest PE
open interest ("put wall"), same logic in the other direction. This is
real market positioning, not price action -- a genuinely different
signal from the Regime Brain's pure price-action view.

Put/Call OI ratio (total PE OI / total CE OI across the strikes
provided) is reported as evidence only -- informational, not yet used
for classification. There isn't enough real history yet to calibrate a
PCR-based signal honestly, same discipline as every other still-loose
threshold in this codebase.

CALIBRATION NOTE (same discipline as every other brain in the MIC): the
"near a wall" distance threshold below is a documented first pass sized
to roughly one NIFTY strike-width (50 points is ~0.2% of a 24000 spot),
not a statistically calibrated conclusion.
"""
from __future__ import annotations

from typing import Optional

from .context import IntelligenceContext
from .evidence import wrap_evidence
from .models import DataQuality, StructureProximity, StructureReading

NEAR_WALL_THRESHOLD_PCT = 0.25  # Roughly one NIFTY strike-width at ~24000 spot.


class StructureBrain:
    """Stateless: call `analyze(...)` with real spot and a list of
    (strike, ce_oi, pe_oi) tuples from the real option chain. Never
    mutates anything, never talks to a broker, never decides whether to
    trade."""

    def analyze(
        self,
        spot: float,
        strikes: list[tuple[float, float, float]],
        context: IntelligenceContext,
    ) -> StructureReading:
        as_of = context.as_of_time

        if spot is None or spot <= 0:
            return self._unknown(as_of, spot, "invalid_spot: spot must be positive")

        valid = [(s, ce, pe) for s, ce, pe in strikes if ce is not None and pe is not None and ce >= 0 and pe >= 0]
        if not valid:
            return self._unknown(as_of, spot, "no_valid_strikes: no strike had usable CE/PE OI data")

        above = [(s, ce, pe) for s, ce, pe in valid if s > spot]
        below = [(s, ce, pe) for s, ce, pe in valid if s < spot]

        resistance = max(above, key=lambda row: row[1]) if above else None
        support = max(below, key=lambda row: row[2]) if below else None

        if resistance is None and support is None:
            return self._unknown(as_of, spot,
                                 "no_strikes_on_either_side: need at least one strike above or below spot")

        total_ce_oi = sum(ce for _, ce, _ in valid)
        total_pe_oi = sum(pe for _, _, pe in valid)
        put_call_oi_ratio = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else None

        resistance_strike, resistance_oi = (resistance[0], resistance[1]) if resistance else (None, None)
        support_strike, support_oi = (support[0], support[2]) if support else (None, None)

        distance_to_resistance_pct = ((resistance_strike - spot) / spot * 100.0) if resistance_strike is not None else None
        distance_to_support_pct = ((spot - support_strike) / spot * 100.0) if support_strike is not None else None

        proximity, reason, confidence = self._classify_proximity(distance_to_resistance_pct, distance_to_support_pct)

        evidence = {
            "strikes_considered": len(valid),
            "strikes_above_spot": len(above),
            "strikes_below_spot": len(below),
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
        }

        return StructureReading(
            spot=spot,
            resistance_strike=resistance_strike, resistance_oi=resistance_oi,
            support_strike=support_strike, support_oi=support_oi,
            distance_to_resistance_pct=round(distance_to_resistance_pct, 4) if distance_to_resistance_pct is not None else None,
            distance_to_support_pct=round(distance_to_support_pct, 4) if distance_to_support_pct is not None else None,
            put_call_oi_ratio=round(put_call_oi_ratio, 4) if put_call_oi_ratio is not None else None,
            proximity=proximity, confidence=confidence, data_quality=DataQuality.SUFFICIENT,
            evidence=evidence, evidence_lineage=wrap_evidence(evidence, context=context),
            reason=reason, as_of=as_of,
        )

    @staticmethod
    def _classify_proximity(
        distance_to_resistance_pct: Optional[float],
        distance_to_support_pct: Optional[float],
    ) -> tuple[StructureProximity, str, float]:
        near_resistance = distance_to_resistance_pct is not None and distance_to_resistance_pct <= NEAR_WALL_THRESHOLD_PCT
        near_support = distance_to_support_pct is not None and distance_to_support_pct <= NEAR_WALL_THRESHOLD_PCT

        if near_resistance and (not near_support or distance_to_resistance_pct <= distance_to_support_pct):
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (NEAR_WALL_THRESHOLD_PCT - distance_to_resistance_pct) / NEAR_WALL_THRESHOLD_PCT))
            return (StructureProximity.NEAR_RESISTANCE_WALL,
                    f"distance to resistance {distance_to_resistance_pct:.3f}% <= threshold {NEAR_WALL_THRESHOLD_PCT}%",
                    round(confidence, 4))
        if near_support:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (NEAR_WALL_THRESHOLD_PCT - distance_to_support_pct) / NEAR_WALL_THRESHOLD_PCT))
            return (StructureProximity.NEAR_SUPPORT_WALL,
                    f"distance to support {distance_to_support_pct:.3f}% <= threshold {NEAR_WALL_THRESHOLD_PCT}%",
                    round(confidence, 4))
        return (StructureProximity.MID_RANGE,
                "spot is not within threshold distance of either wall",
                0.5)

    @staticmethod
    def _unknown(as_of, spot, reason: str) -> StructureReading:
        return StructureReading(
            spot=spot, resistance_strike=None, resistance_oi=None,
            support_strike=None, support_oi=None,
            distance_to_resistance_pct=None, distance_to_support_pct=None,
            put_call_oi_ratio=None, proximity=StructureProximity.UNKNOWN,
            confidence=0.0, data_quality=DataQuality.INSUFFICIENT, reason=reason, as_of=as_of,
        )

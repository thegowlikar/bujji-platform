"""Premium Behaviour Intelligence -- Phase 15E, engine.

Pure functions only: `evaluate()` reads a PremiumBehaviourState's
bounded window and returns a PremiumBehaviourReading -- never mutates
anything, never reads a wall clock, never talks to a broker.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import (
    ACCEL_ACCELERATING, ACCEL_DECELERATING, ACCEL_STEADY, ACCEL_UNKNOWN,
    DIRECTION_FALLING, DIRECTION_RISING, DIRECTION_STEADY, DIRECTION_UNKNOWN,
    MIN_OBSERVATIONS_FOR_ACCELERATION, MIN_OBSERVATIONS_FOR_DIRECTION, STEADY_THRESHOLD_PCT,
    PremiumBehaviourReading, PremiumBehaviourState, SeriesReading,
)


def _pct_change(old: float, new: float) -> Optional[float]:
    if old == 0:
        return None  # A zero-premium baseline makes % change undefined, not infinite/fabricated.
    return (new - old) / abs(old) * 100.0


def _evaluate_series(values: Sequence[Optional[float]], label: str) -> SeriesReading:
    """`values` is the real, in-order sequence of one series' (CE/PE/
    combined) values across the current window -- None entries (a real
    cycle where that value was unavailable) are excluded from the
    comparison, never filled with a guess."""
    real_values = [v for v in values if v is not None]

    if len(real_values) < MIN_OBSERVATIONS_FOR_DIRECTION:
        return SeriesReading(
            DIRECTION_UNKNOWN, None, ACCEL_UNKNOWN,
            real_values[-1] if real_values else None, None,
            f"insufficient history for {label}: {len(real_values)} real observation(s), "
            f"need >= {MIN_OBSERVATIONS_FOR_DIRECTION}",
        )

    current, previous = real_values[-1], real_values[-2]
    rate = _pct_change(previous, current)
    if rate is None:
        direction = DIRECTION_UNKNOWN
        reason = f"{label} previous value was zero -- rate of change undefined"
    elif abs(rate) < STEADY_THRESHOLD_PCT:
        direction = DIRECTION_STEADY
        reason = f"{label} changed {rate:.2f}% (within +-{STEADY_THRESHOLD_PCT}% steady threshold)"
    elif rate > 0:
        direction = DIRECTION_RISING
        reason = f"{label} rose {rate:.2f}% over the last observation"
    else:
        direction = DIRECTION_FALLING
        reason = f"{label} fell {rate:.2f}% over the last observation"

    acceleration = ACCEL_UNKNOWN
    if len(real_values) >= MIN_OBSERVATIONS_FOR_ACCELERATION and rate is not None:
        prior_rate = _pct_change(real_values[-3], real_values[-2])
        if prior_rate is not None:
            delta_rate = abs(rate) - abs(prior_rate)
            if abs(delta_rate) < STEADY_THRESHOLD_PCT:
                acceleration = ACCEL_STEADY
            elif delta_rate > 0:
                acceleration = ACCEL_ACCELERATING
            else:
                acceleration = ACCEL_DECELERATING

    return SeriesReading(direction, round(rate, 3) if rate is not None else None, acceleration,
                          current, previous, reason)


def _relative_expansion(ce: SeriesReading, pe: SeriesReading) -> str:
    if ce.rate_of_change_pct is None or pe.rate_of_change_pct is None:
        return DIRECTION_UNKNOWN
    diff = ce.rate_of_change_pct - pe.rate_of_change_pct
    if abs(diff) < STEADY_THRESHOLD_PCT:
        return "SYMMETRIC"
    return "CE_EXPANDING_FASTER" if diff > 0 else "PE_EXPANDING_FASTER"


def _premium_vs_underlying(combined: SeriesReading, spot_values: Sequence[Optional[float]]) -> str:
    real_spots = [v for v in spot_values if v is not None]
    if len(real_spots) < MIN_OBSERVATIONS_FOR_DIRECTION or combined.rate_of_change_pct is None:
        return DIRECTION_UNKNOWN
    spot_rate = _pct_change(real_spots[-2], real_spots[-1])
    if spot_rate is None:
        return DIRECTION_UNKNOWN
    # Combined premium expanding alongside a real underlying move (either
    # direction) is CO_EXPANDING; combined premium moving opposite/absent
    # a meaningful underlying move is DIVERGING. A meaningful spot move is
    # itself gated by the same steady threshold, so tiny float noise in
    # spot never triggers a false co-movement/divergence read.
    spot_moved = abs(spot_rate) >= STEADY_THRESHOLD_PCT
    premium_expanding = combined.direction == "RISING"
    if not spot_moved:
        return DIRECTION_UNKNOWN
    return "CO_EXPANDING" if premium_expanding else "DIVERGING"


def _confidence(observation_count: int) -> str:
    if observation_count < MIN_OBSERVATIONS_FOR_DIRECTION:
        return "NONE"
    if observation_count < MIN_OBSERVATIONS_FOR_ACCELERATION:
        return "LOW"
    if observation_count < 5:
        return "MODERATE"
    return "HIGH"


def evaluate(state: PremiumBehaviourState) -> PremiumBehaviourReading:
    history = state.history
    if not history:
        empty = SeriesReading(DIRECTION_UNKNOWN, None, ACCEL_UNKNOWN, None, None, "no observations yet")
        return PremiumBehaviourReading(
            timestamp="", lookback_used=0, ce=empty, pe=empty, combined=empty,
            ce_vs_pe_relative=DIRECTION_UNKNOWN, premium_vs_underlying=DIRECTION_UNKNOWN,
            confidence="NONE", provenance="bujji.premium_behaviour.engine.evaluate",
        )

    ce_reading = _evaluate_series([o.ce_premium for o in history], "CE premium")
    pe_reading = _evaluate_series([o.pe_premium for o in history], "PE premium")
    combined_reading = _evaluate_series([o.combined_premium for o in history], "combined premium")

    real_combined_count = len([o for o in history if o.combined_premium is not None])

    return PremiumBehaviourReading(
        timestamp=history[-1].timestamp, lookback_used=len(history),
        ce=ce_reading, pe=pe_reading, combined=combined_reading,
        ce_vs_pe_relative=_relative_expansion(ce_reading, pe_reading),
        premium_vs_underlying=_premium_vs_underlying(combined_reading, [o.spot for o in history]),
        confidence=_confidence(real_combined_count),
        provenance="bujji.premium_behaviour.engine.evaluate",
    )

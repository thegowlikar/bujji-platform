"""Premium Behaviour Intelligence -- Phase 15E, models.

A temporal memory of ATM CE/PE premium movement -- mirrors
`bujji.market_regime_memory`'s own frozen, `.advance()`-threaded
pattern exactly (same cross-cycle state-threading precedent as
RegimeMemoryState/ObservationMemory), not a new architecture.

Direction/acceleration are always UNKNOWN, never FLAT or fabricated,
when there isn't yet enough real history to compute them -- "no
movement observed yet" (insufficient history) is a DIFFERENT thing
from "movement observed and it was flat" (STEADY), and this module
never confuses the two.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Tuple

SCHEMA_VERSION = "1.0.0"

DIRECTION_RISING = "RISING"
DIRECTION_FALLING = "FALLING"
DIRECTION_STEADY = "STEADY"
DIRECTION_UNKNOWN = "UNKNOWN"
ALL_DIRECTIONS = (DIRECTION_RISING, DIRECTION_FALLING, DIRECTION_STEADY, DIRECTION_UNKNOWN)

ACCEL_ACCELERATING = "ACCELERATING"
ACCEL_DECELERATING = "DECELERATING"
ACCEL_STEADY = "STEADY"
ACCEL_UNKNOWN = "UNKNOWN"

# STEADY threshold: a relative change smaller than this magnitude counts
# as no real movement, not a coin-flip RISING/FALLING on float noise.
STEADY_THRESHOLD_PCT = 0.5  # percent

# Minimum real observations required before ANY direction/rate/acceleration
# claim is made -- below this, everything is honestly UNKNOWN.
MIN_OBSERVATIONS_FOR_DIRECTION = 2
MIN_OBSERVATIONS_FOR_ACCELERATION = 3

DEFAULT_LOOKBACK = 5  # cycles -- bounded window, never an unbounded/whole-session history.


@dataclass(frozen=True)
class PremiumObservation:
    """One real cycle's ATM CE/PE mid premiums + the underlying spot at
    that same moment -- never a derived/fabricated value."""

    timestamp: str
    ce_premium: Optional[float]
    pe_premium: Optional[float]
    spot: Optional[float]

    @property
    def combined_premium(self) -> Optional[float]:
        if self.ce_premium is None or self.pe_premium is None:
            return None
        return self.ce_premium + self.pe_premium


@dataclass(frozen=True)
class PremiumBehaviourState:
    """Bounded rolling window (never the whole session -- an unbounded
    history is neither necessary for rate-of-change/acceleration nor
    safe to keep growing forever in memory or on disk)."""

    history: Tuple[PremiumObservation, ...] = ()
    lookback: int = DEFAULT_LOOKBACK

    def advance(self, observation: PremiumObservation) -> "PremiumBehaviourState":
        new_history = (self.history + (observation,))[-self.lookback:]
        return replace(self, history=new_history)


@dataclass(frozen=True)
class SeriesReading:
    """One series' (CE, PE, or combined) own direction/rate/acceleration
    read -- reused identically for all three series so CE/PE/combined
    are always evaluated the exact same way."""

    direction: str
    rate_of_change_pct: Optional[float]
    acceleration: str
    current_value: Optional[float]
    previous_value: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return {
            "direction": self.direction, "rate_of_change_pct": self.rate_of_change_pct,
            "acceleration": self.acceleration, "current_value": self.current_value,
            "previous_value": self.previous_value, "reason": self.reason,
        }


@dataclass(frozen=True)
class PremiumBehaviourReading:
    timestamp: str
    lookback_used: int
    ce: SeriesReading
    pe: SeriesReading
    combined: SeriesReading
    ce_vs_pe_relative: str          # CE_EXPANDING_FASTER / PE_EXPANDING_FASTER / SYMMETRIC / UNKNOWN.
    premium_vs_underlying: str      # CO_EXPANDING / DIVERGING / UNKNOWN -- combined premium direction vs |spot move| direction.
    confidence: str                 # NONE / LOW / MODERATE / HIGH -- driven purely by how much real history is available.
    provenance: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp, "lookback_used": self.lookback_used,
            "ce": self.ce.to_dict(), "pe": self.pe.to_dict(), "combined": self.combined.to_dict(),
            "ce_vs_pe_relative": self.ce_vs_pe_relative, "premium_vs_underlying": self.premium_vs_underlying,
            "confidence": self.confidence, "provenance": self.provenance, "schema_version": self.schema_version,
        }

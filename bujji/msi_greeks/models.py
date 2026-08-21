"""Greeks Intelligence -- Phase 15E, models.

PER-LEG only, deliberately NOT position-combined: unlike the legacy
`bujji.intelligence.greeks_brain.GreeksBrain`, which hardcodes a SHORT
straddle combination (`position_x = -(leg_x_ce + leg_x_pe)`), this
module makes no assumption about what position (if any) Bujji holds --
that assumption doesn't belong in an observational intelligence layer
that runs every cycle regardless of whether any trade exists. A future
position-aware consumer can combine these per-leg values however its
actual position shape requires.

UNKNOWN is a first-class per-leg outcome (`GreeksLegAssessment.available
= False`), never a fabricated zero or omitted field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class GreeksLegAssessment:
    available: bool
    reason: Optional[str]  # Set when available=False -- WHY this leg's Greeks could not be computed.
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta_per_day: Optional[float]
    vega_per_pct: Optional[float]

    def to_dict(self) -> dict:
        return {
            "available": self.available, "reason": self.reason, "iv": self.iv,
            "delta": self.delta, "gamma": self.gamma,
            "theta_per_day": self.theta_per_day, "vega_per_pct": self.vega_per_pct,
        }


@dataclass(frozen=True)
class GreeksAssessment:
    timestamp: str
    spot: Optional[float]
    strike: Optional[float]
    t_years: Optional[float]
    risk_free_rate: float
    ce: GreeksLegAssessment
    pe: GreeksLegAssessment
    provenance: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp, "spot": self.spot, "strike": self.strike,
            "t_years": self.t_years, "risk_free_rate": self.risk_free_rate,
            "ce": self.ce.to_dict(), "pe": self.pe.to_dict(),
            "provenance": self.provenance, "schema_version": self.schema_version,
        }

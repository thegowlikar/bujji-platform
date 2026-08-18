"""Market Context Window -- Phase 11 Upgrade 3. Pure models, no IO.

Multiple observation horizons over the SAME already-persisted per-cycle
records this session already produces. Observation only -- nothing here
forecasts, scores, or ranks; every field is a plain aggregate of what
was actually recorded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HorizonSummary:
    cycles_observed: int
    direction: Optional[str]
    volatility_trend: str
    dominant_regime: Optional[str]

    def to_dict(self) -> dict:
        return {
            "cycles_observed": self.cycles_observed,
            "direction": self.direction,
            "volatility_trend": self.volatility_trend,
            "dominant_regime": self.dominant_regime,
        }


@dataclass(frozen=True)
class SessionContext:
    cycles_observed: int
    dominant_regime: Optional[str]
    range_status: str

    def to_dict(self) -> dict:
        return {
            "cycles_observed": self.cycles_observed,
            "dominant_regime": self.dominant_regime,
            "range_status": self.range_status,
        }


@dataclass(frozen=True)
class HistoricalContext:
    """No cross-session index exists yet -- honestly disclosed as
    unavailable rather than fabricated. See Phase 11 investigation
    notes for what wiring this for real would require."""

    available: bool
    similar_sessions_found: int
    outcomes: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "similar_sessions_found": self.similar_sessions_found,
            "outcomes": self.outcomes,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContextWindowReport:
    short_term: HorizonSummary
    medium_term: HorizonSummary
    session_context: SessionContext
    historical_context: HistoricalContext

    def to_dict(self) -> dict:
        return {
            "short_term": self.short_term.to_dict(),
            "medium_term": self.medium_term.to_dict(),
            "session_context": self.session_context.to_dict(),
            "historical_context": self.historical_context.to_dict(),
        }

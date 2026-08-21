"""Phase 20.13 -- pure data contracts. No IO, no broker, no execution,
no holdings- or realized-outcome vocabulary anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

SHADOW_RUN_OPEN = "OPEN"
SHADOW_RUN_RUNNING = "RUNNING"
SHADOW_RUN_CLOSED = "CLOSED"
SHADOW_RUN_FAILED = "FAILED"
ALL_SHADOW_RUN_STATES = (SHADOW_RUN_OPEN, SHADOW_RUN_RUNNING, SHADOW_RUN_CLOSED, SHADOW_RUN_FAILED)

RUNTIME_HEALTHY = "HEALTHY"
RUNTIME_DEGRADED = "DEGRADED"
RUNTIME_FAILED = "FAILED"
ALL_RUNTIME_STATUSES = (RUNTIME_HEALTHY, RUNTIME_DEGRADED, RUNTIME_FAILED)


@dataclass(frozen=True)
class ShadowRunConfig:
    """No broker fields, no execution fields -- `data_source` names
    where real candles/VIX come from (a real historical store path in
    dry-run/validation, a real live feed identifier in production);
    this package never constructs or owns that connection itself."""

    market: str
    session_date: str
    cycle_interval_minutes: int
    data_source: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.cycle_interval_minutes <= 0:
            raise ValueError(f"cycle_interval_minutes={self.cycle_interval_minutes!r} must be positive")


@dataclass(frozen=True)
class ShadowRunState:
    """Open -> Running -> Closed (or Failed). Distinct from, and never
    confused with, `DailySessionRuntime`'s own `DailySessionStage`
    (Phase 19.11, PRE_MARKET/MARKET_OPEN/.../SESSION_COMPLETE) -- that
    tracks the OUTER day lifecycle this package's `intelligence_fn`
    runs inside; this tracks the INNER per-cycle loop state this
    package itself owns."""

    session_date: str
    state: str
    cycles_completed: int
    last_cycle_timestamp: Optional[str]
    errors: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in ALL_SHADOW_RUN_STATES:
            raise ValueError(f"state={self.state!r} not in {ALL_SHADOW_RUN_STATES}")
        if self.cycles_completed < 0:
            raise ValueError("cycles_completed cannot be negative")


@dataclass(frozen=True)
class FeedHealth:
    last_observation_timestamp: Optional[str]
    missing_intervals: Tuple[str, ...]
    data_fresh: bool

    def to_dict(self) -> dict:
        return {
            "last_observation_timestamp": self.last_observation_timestamp,
            "missing_intervals": list(self.missing_intervals),
            "data_fresh": self.data_fresh,
        }


@dataclass(frozen=True)
class IntelligenceHealth:
    decision_cycles_completed: int
    missing_intelligence_count: int
    uncertainty_frequency: float   # fraction of recorded cycles carrying >=1 uncertainty entry.

    def to_dict(self) -> dict:
        return {
            "decision_cycles_completed": self.decision_cycles_completed,
            "missing_intelligence_count": self.missing_intelligence_count,
            "uncertainty_frequency": self.uncertainty_frequency,
        }


@dataclass(frozen=True)
class HealthReport:
    """The runtime's own honest self-assessment -- three independent
    dimensions (feed, intelligence, overall runtime), never collapsed
    into a single opaque score."""

    session_date: str
    runtime_status: str            # ALL_RUNTIME_STATUSES
    feed_health: FeedHealth
    intelligence_health: IntelligenceHealth
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.runtime_status not in ALL_RUNTIME_STATUSES:
            raise ValueError(f"runtime_status={self.runtime_status!r} not in {ALL_RUNTIME_STATUSES}")

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date,
            "runtime_status": self.runtime_status,
            "feed_health": self.feed_health.to_dict(),
            "intelligence_health": self.intelligence_health.to_dict(),
            "reasons": list(self.reasons),
        }

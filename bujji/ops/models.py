"""Operations Layer — data contracts (Sprint 4).

The Operations Layer observes, measures, alerts, and reports. It NEVER
influences a trading decision, order, position, or exit -- every object
here is read by the dashboard and the Incident Log only, never by the
Orchestrator's own trading logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class HealthState(str, Enum):
    """Immutable, objectively-ordered operational health states.
    Precedence for combining multiple signals is always
    CRITICAL > DEGRADED > WARNING > HEALTHY -- the single worst signal
    wins, never averaged, never a subjective judgment call."""

    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"  # Only ever set by an EXTERNAL watcher -- a running
                         # process cannot objectively declare itself offline.


_SEVERITY_ORDER = {
    HealthState.HEALTHY: 0, HealthState.WARNING: 1,
    HealthState.DEGRADED: 2, HealthState.CRITICAL: 3, HealthState.OFFLINE: 4,
}


def worst(states: list[HealthState]) -> HealthState:
    """The single objective combination rule for this whole layer: highest
    severity wins. No weighting, no averaging, no subjective judgment."""
    if not states:
        return HealthState.HEALTHY
    return max(states, key=lambda s: _SEVERITY_ORDER[s])


@dataclass(frozen=True)
class OpsSnapshot:
    """One cycle's complete operational observation. Read-only, produced
    by HealthMonitor.observe(), never mutated afterward."""

    as_of: datetime
    health_state: HealthState
    reasons: list[str]                  # Which specific signals produced this state.
    uptime_seconds: float
    restart_count_last_hour: int
    auth_expired: bool
    auth_expired_since: Optional[str]   # ISO timestamp of first failure in the current streak, or None.
    auth_expired_duration_seconds: Optional[float]
    candle_age_seconds: Optional[float]
    ws_connected: bool
    memory_rss_kb: float
    disk_free_pct: float
    exception_count_last_hour: int
    journal_write_ok: bool
    decision_journal_write_ok: bool
    latest_decision_id: Optional[str]
    latest_trade_id: Optional[str]

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "health_state": self.health_state.value,
            "reasons": self.reasons,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "restart_count_last_hour": self.restart_count_last_hour,
            "auth_expired": self.auth_expired,
            "auth_expired_since": self.auth_expired_since,
            "auth_expired_duration_seconds": self.auth_expired_duration_seconds,
            "candle_age_seconds": self.candle_age_seconds,
            "ws_connected": self.ws_connected,
            "memory_rss_kb": round(self.memory_rss_kb, 1),
            "disk_free_pct": round(self.disk_free_pct, 1),
            "exception_count_last_hour": self.exception_count_last_hour,
            "journal_write_ok": self.journal_write_ok,
            "decision_journal_write_ok": self.decision_journal_write_ok,
            "latest_decision_id": self.latest_decision_id,
            "latest_trade_id": self.latest_trade_id,
        }


@dataclass(frozen=True)
class Alert:
    """A single alert, generated on a state transition or threshold
    breach. Alert generation only -- never a trading action."""

    alert_id: str
    as_of: datetime
    severity: HealthState
    category: str    # e.g. "auth_expired", "stale_market_data", "journal_failure".
    message: str
    subsystem: str   # e.g. "broker", "market_data", "journal", "process".


@dataclass
class Incident:
    """Operational Incident Log entry -- separate from TradeJournal and
    DecisionJournal by design (Sprint 4 spec)."""

    incident_id: str
    opened_at: str
    severity: str
    root_cause: str
    affected_subsystem: str
    resolution: str = ""
    resolved_at: str = ""
    status: str = "OPEN"  # OPEN | RESOLVED.

    @property
    def duration_seconds(self) -> Optional[float]:
        if not self.resolved_at:
            return None
        return (datetime.fromisoformat(self.resolved_at)
                - datetime.fromisoformat(self.opened_at)).total_seconds()

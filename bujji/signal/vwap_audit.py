"""VWAP Audit subsystem.

Produces, on every completed 5-minute evaluation cycle, a structured record of
VWAP *quality* alongside the trading context (state, trade state, decision).
This exists purely for production auditability — debugging, compliance, and
post-trade investigation. It reads state; it never influences trading logic.

The record is emitted to the structured JSON logs and published to the
dashboard's "Market Data Health" section.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

from .indicators import VwapTracker


@dataclass(frozen=True)
class VwapQuality:
    """Snapshot of VWAP data quality at a point in time."""

    value: float                     # The VWAP value in use.
    candles_used: int                # Completed candles folded into VWAP.
    cumulative_volume: float         # Real volume used to weight VWAP.
    is_real: bool                    # Backed by genuine traded volume.
    using_fallback: bool             # Value came from the equal-weight fallback.
    fallback_reason: Optional[str]   # Why fallback/degraded, else None.
    trading_permitted: bool          # Whether VWAP is usable for a decision.

    @classmethod
    def from_tracker(cls, tracker: VwapTracker) -> "VwapQuality":
        return cls(
            value=round(tracker.value, 4),
            candles_used=tracker.candle_count,
            cumulative_volume=tracker.cumulative_volume,
            is_real=tracker.is_real,
            using_fallback=tracker.using_fallback,
            fallback_reason=tracker.fallback_reason,
            trading_permitted=tracker.ready,
        )


@dataclass(frozen=True)
class VwapAuditRecord:
    """A single cycle's VWAP audit entry."""

    timestamp: datetime
    strategy_state: str      # FSM state, e.g. IN_POSITION.
    trade_state: str         # FLAT | IN_POSITION.
    decision: str            # The decision made this cycle.
    quality: VwapQuality

    def to_log(self) -> dict[str, Any]:
        """Flat dict for structured JSON logging."""
        return {
            "audit": "vwap",
            "timestamp": self.timestamp.isoformat(),
            "strategy_state": self.strategy_state,
            "trade_state": self.trade_state,
            "decision": self.decision,
            "vwap_value": self.quality.value,
            "candles_used": self.quality.candles_used,
            "cumulative_volume": self.quality.cumulative_volume,
            "vwap_is_real": self.quality.is_real,
            "vwap_using_fallback": self.quality.using_fallback,
            "vwap_fallback_reason": self.quality.fallback_reason,
            "trading_permitted": self.quality.trading_permitted,
        }

    def to_dashboard(self) -> dict[str, Any]:
        """Nested dict for the dashboard 'Market Data Health' section."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "strategy_state": self.strategy_state,
            "trade_state": self.trade_state,
            "decision": self.decision,
            "quality": asdict(self.quality),
        }

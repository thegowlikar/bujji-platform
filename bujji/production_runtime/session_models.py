"""Session Heartbeat & Report models -- BUJJI Options OS v3, Gate F.5.

Pure data + pure aggregation. `generate_session_summary()` performs NO
new analytics/calculation of its own -- every figure it reports is
either a direct count of `ShadowTradeTimeline` entries (F.1's own
already-recorded event stream) or copied verbatim from an
already-computed `PortfolioValuation` (F.3's own output). This module
never recomputes PnL, exposure, or any risk figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

from bujji.trading_brain.portfolio_valuation.models import PortfolioValuation


@dataclass(frozen=True)
class SessionHeartbeat:
    timestamp: datetime
    runtime_state: str
    broker_status: str
    market_feed_status: str
    active_positions_count: int
    last_decision_timestamp: Optional[datetime]
    last_execution_timestamp: Optional[datetime]


@dataclass(frozen=True)
class SessionSummary:
    session_start: Optional[datetime]
    session_end: Optional[datetime]
    final_state: str
    state_transition_count: int
    orders_submitted_count: int
    fills_count: int
    rejects_count: int
    lifecycle_event_count: int
    error_count: int
    total_realized_pnl: float
    total_unrealized_pnl: Optional[float]
    unresolved_position_group_ids: Tuple[str, ...]


@dataclass(frozen=True)
class EODReconciliationResult:
    final_state: str
    final_valuations: Dict[str, PortfolioValuation]
    unresolved_position_group_ids: Tuple[str, ...]
    summary: SessionSummary


def generate_session_summary(
    timeline_entries: Tuple, final_valuations: Dict[str, PortfolioValuation],
    unresolved_position_group_ids: Tuple[str, ...], final_state: str,
) -> SessionSummary:
    """Pure aggregation over `timeline_entries` (ShadowTradeTimeline.
    entries(), already-recorded) and `final_valuations` (already
    computed by Portfolio Reality Engine) -- counts and sums only, no
    new figure is derived that wasn't already recorded elsewhere."""
    state_transitions = [e for e in timeline_entries if e.event_type == "STATE_CHANGED"]
    orders_submitted = [e for e in timeline_entries if e.stage in ("ORDER_SUBMITTED", "LIFECYCLE_ORDER_CREATED")]
    fills = [e for e in timeline_entries if e.stage in ("ORDER_FILLED", "LIFECYCLE_ORDER_FILLED")]
    rejects = [e for e in timeline_entries if e.stage in ("CONTEXT_UNAVAILABLE", "LIFECYCLE_ACTION_FAILED")]
    lifecycle_events = [e for e in timeline_entries if "LIFECYCLE" in e.stage]
    errors = [e for e in timeline_entries if e.stage in ("LIFECYCLE_ACTION_FAILED", "CONTEXT_UNAVAILABLE")]

    session_start = timeline_entries[0].timestamp if timeline_entries else None
    session_end = timeline_entries[-1].timestamp if timeline_entries else None

    total_realized = sum(v.total_realized_pnl for v in final_valuations.values())
    any_unavailable = any(v.total_unrealized_pnl is None for v in final_valuations.values())
    total_unrealized = None if (any_unavailable or not final_valuations) else sum(
        v.total_unrealized_pnl for v in final_valuations.values()
    )

    return SessionSummary(
        session_start=session_start, session_end=session_end, final_state=final_state,
        state_transition_count=len(state_transitions), orders_submitted_count=len(orders_submitted),
        fills_count=len(fills), rejects_count=len(rejects), lifecycle_event_count=len(lifecycle_events),
        error_count=len(errors), total_realized_pnl=total_realized, total_unrealized_pnl=total_unrealized,
        unresolved_position_group_ids=unresolved_position_group_ids,
    )

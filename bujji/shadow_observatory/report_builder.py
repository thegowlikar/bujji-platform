"""Session Report Builder -- BUJJI Options OS v3, Gate V.0.

PURPOSE: build `summary.json` purely by reading back the SAME artifact
files this gate already wrote to disk -- counts and sums only, no new
figure computed. This is deliberately NOT the same code path as F.5's
own `session_models.generate_session_summary()` (which aggregates
`ShadowTradeTimeline`'s in-memory entries): that function requires the
live Python object; this one proves the artifacts on disk are
sufficient on their own to reconstruct the same story, which is the
entire point of a forensic black-box recorder -- it must be usable
after the process that wrote it has exited. Not a duplicate truth
store: it is the SAME underlying facts (already-published EventBus
payloads), read from a different, durable location.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from .models import SessionSummaryArtifact
from .session_store import SessionStore


def build_session_summary(
    store: SessionStore, session_id: str, final_positions: Tuple[str, ...],
    realized_pnl: float, unrealized_pnl: Optional[float],
) -> SessionSummaryArtifact:
    """`realized_pnl`/`unrealized_pnl`/`final_positions` are supplied
    by the caller (the session controller, from its own already-
    computed final valuation) -- this function never recomputes PnL
    or re-derives open positions itself, only counts and timestamps
    already-recorded artifact rows."""
    state_changes = store.read_jsonl("state_changes.jsonl")
    orders = store.read_jsonl("orders.jsonl")
    executions = store.read_jsonl("executions.jsonl")
    errors = store.read_jsonl("errors.jsonl")

    reject_count = sum(
        1 for e in executions
        if "REJECTED" in str(e.get("stage", "")) or "FAILED" in str(e.get("stage", ""))
    )

    all_timestamps = [
        row["timestamp"] for source in (state_changes, orders, executions) for row in source if "timestamp" in row
    ]
    session_start = min(all_timestamps) if all_timestamps else None
    session_end = max(all_timestamps) if all_timestamps else None
    duration = None
    if session_start and session_end:
        duration = (datetime.fromisoformat(session_end) - datetime.fromisoformat(session_start)).total_seconds()

    return SessionSummaryArtifact(
        session_id=session_id, session_start=session_start, session_end=session_end,
        session_duration_seconds=duration, state_transitions=len(state_changes), orders_count=len(orders),
        fills_count=len(executions), reject_count=reject_count, final_positions=tuple(final_positions),
        realized_pnl=realized_pnl, unrealized_pnl=unrealized_pnl, error_count=len(errors),
    )

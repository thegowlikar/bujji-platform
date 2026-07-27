"""Shadow Trading Engine query helpers — Series 100. Pure, read-only
lookups AND Deliverable 8's Daily/Portfolio dashboard views. No
scoring, no ranking, no judgement -- only counting and formatting real
recorded fields."""
from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from .models import ShadowPosition


def by_id(positions: Sequence[ShadowPosition], shadow_trade_id: str) -> Optional[ShadowPosition]:
    for p in positions:
        if p.shadow_trade_id == shadow_trade_id:
            return p
    return None


def open_only(positions: Sequence[ShadowPosition]) -> tuple:
    return tuple(p for p in positions if not p.completed)


def closed_only(positions: Sequence[ShadowPosition]) -> tuple:
    return tuple(p for p in positions if p.completed)


def daily_view(position: ShadowPosition, decision_outcome: str = "TRADE_APPROVED") -> str:
    """Deliverable 8's "Today" card -- plain text, real recorded fields only."""
    mtm = position.realised_pnl if position.completed else position.unrealised_pnl
    mtm_str = f"+₹{mtm:,.0f}" if mtm is not None and mtm >= 0 else (f"-₹{abs(mtm):,.0f}" if mtm is not None else "unavailable")
    lines = [
        "Today", "", "Decision:", f"  {decision_outcome}", "Strategy:", f"  {position.entry_structure}",
        "Entry:", f"  {position.entry_time}", f"Current {'Realised' if position.completed else 'MTM'}:", f"  {mtm_str}",
        "Lifecycle:", f"  {position.lifecycle_state}",
        "Exit:", f"  {position.exit_reason if position.completed else 'Pending'}",
    ]
    return "\n".join(lines)


def portfolio_view(positions: Sequence[ShadowPosition], no_trade_count: int) -> str:
    """Deliverable 8's "Portfolio" summary. Win/Loss are counted directly
    from real, already-recorded `realised_pnl` sign -- never a
    performance judgement, purely a factual count."""
    open_count = len(open_only(positions))
    closed = closed_only(positions)
    wins = sum(1 for p in closed if p.realised_pnl is not None and p.realised_pnl > 0)
    losses = sum(1 for p in closed if p.realised_pnl is not None and p.realised_pnl <= 0)
    lines = [
        "Shadow Positions", "", "Open:", f"  {open_count}", "Closed:", f"  {len(closed)}",
        "Win:", f"  {wins}", "Loss:", f"  {losses}", "No Trade:", f"  {no_trade_count}",
    ]
    return "\n".join(lines)


def exit_reason_distribution(positions: Sequence[ShadowPosition]) -> Counter:
    return Counter(p.exit_reason for p in closed_only(positions))

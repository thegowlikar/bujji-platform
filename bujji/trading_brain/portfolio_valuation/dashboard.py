"""Portfolio Valuation dashboard rendering — Live Shadow Real-Time
Paper Execution sprint (Part 5). Matches the existing
bujji/live_shadow_operator/health.py rendering convention (plain text,
no new framework) rather than inventing a second dashboard mechanism.
"""
from __future__ import annotations

from .models import PortfolioValuation


def render_portfolio_dashboard(valuation: PortfolioValuation) -> str:
    lines = [
        f"BUJJI Live Shadow -- Portfolio Valuation [{valuation.valuation_id}]",
        f"  as_of:                {valuation.as_of}",
    ]
    if valuation.triggering_symbol:
        lines.append(f"  triggered_by_tick:    {valuation.triggering_symbol} @ {valuation.triggering_tick_timestamp}")
    lines.append("  legs:")
    for leg in valuation.legs:
        price_str = f"{leg.current_price:.2f}" if leg.current_price is not None else "UNKNOWN (no tick observed)"
        pnl_str = f"{leg.unrealized_pnl:+.2f}" if leg.unrealized_pnl is not None else "UNKNOWN"
        stale = " (STALE -- not this tick)" if leg.price_is_stale else ""
        lines.append(
            f"    {leg.symbol}  {leg.side} qty={leg.quantity}  "
            f"entry={leg.entry_price:.2f}@{leg.entry_timestamp}  "
            f"current={price_str}{stale}  unrealized={pnl_str}"
        )
    lines.append(f"  total_realized_pnl:   {valuation.total_realized_pnl:+.2f}")
    lines.append(
        f"  total_unrealized_pnl: "
        f"{valuation.total_unrealized_pnl:+.2f}" if valuation.total_unrealized_pnl is not None
        else "  total_unrealized_pnl: UNKNOWN (at least one leg has no observed price)"
    )
    lines.append(
        f"  total_pnl:            {valuation.total_pnl:+.2f}" if valuation.total_pnl is not None
        else "  total_pnl:            UNKNOWN"
    )
    return "\n".join(lines)
